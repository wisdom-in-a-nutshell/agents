#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


_AGENTS_ROOT = Path(__file__).resolve().parent.parent
if str(_AGENTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENTS_ROOT))

from hooks.scripts.stop import register_current_codex_transaction_paths  # noqa: E402


ALLOWED_ORIGINS = {"external", "owned"}
ALLOWED_SCOPES = {"global", "repo", "dormant"}
DEFAULT_REGISTRY_FILE = Path(__file__).resolve().parent.parent / "skills" / "registry.json"
DEFAULT_USER_SKILLS_DIR = Path.home() / ".agents" / "skills"


def expand_path(raw: str, home: Path) -> Path:
    if raw.startswith("~/"):
        return home / raw[2:]
    return Path(raw)


def ensure_str(value: Any, field: str, idx: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"managed_skills[{idx}] invalid {field}: {value!r}")
    return value.strip()


def rel_link(dst: Path, src: Path) -> str:
    return os.path.relpath(str(src), str(dst.parent))


def resolved_target(link_path: Path) -> Path:
    cur = os.readlink(link_path)
    if os.path.isabs(cur):
        return Path(cur).resolve()
    return (link_path.parent / cur).resolve()


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def resolve_repo_root(repo: str, github_root: Path, home: Path) -> Path:
    if repo.startswith("~/") or repo.startswith("/"):
        return expand_path(repo, home).resolve()
    return (github_root / repo).resolve()


def repo_local_skill_source(repo_root: Path, skill: str) -> Path:
    return repo_root / ".agents" / "skills" / skill / "SKILL.md"


def sync_link(dst: Path, src: Path, apply: bool) -> bool:
    rel = rel_link(dst, src)
    if dst.is_symlink() and resolved_target(dst) == src.resolve():
        print(f"UNCHANGED {dst}")
        return False

    print(f"SYNC {dst} -> {rel}")
    if not apply:
        return True

    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.is_symlink() or dst.is_file():
        dst.unlink()
    elif dst.is_dir():
        shutil.rmtree(dst)
    elif dst.exists():
        dst.unlink()
    dst.symlink_to(rel)
    return True


def git_root_for(path: Path) -> Path | None:
    probe = path.parent if path.is_symlink() or not path.is_dir() else path
    result = subprocess.run(
        ["git", "-C", str(probe), "rev-parse", "--show-toplevel"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    root = result.stdout.strip()
    if not root:
        return None
    return Path(root).resolve()


def repo_git_root(repo_root: Path) -> Path | None:
    if not repo_root.exists():
        return None
    if not repo_root.is_dir():
        return None
    result = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "--show-toplevel"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    root = result.stdout.strip()
    if not root:
        return None
    return Path(root).resolve()


def stage_git_paths(paths: set[Path]) -> None:
    grouped: dict[Path, list[str]] = {}
    for path in sorted(paths):
        git_root = git_root_for(path)
        if git_root is None:
            continue
        try:
            rel = Path(os.path.abspath(path)).relative_to(git_root)
        except ValueError:
            continue
        if not path.exists() and not path.is_symlink():
            tracked = subprocess.run(
                ["git", "-C", str(git_root), "ls-files", "--error-unmatch", "--", str(rel)],
                check=False,
                capture_output=True,
                text=True,
            )
            if tracked.returncode != 0:
                continue
        grouped.setdefault(git_root, []).append(str(rel))

    for git_root, rel_paths in sorted(grouped.items()):
        print(f"TRACK {git_root}: {' '.join(rel_paths)}")
        subprocess.run(
            ["git", "-C", str(git_root), "add", "-A", "--", *rel_paths],
            check=True,
        )


def validate_registry(
    data: dict[str, Any], root_dir: Path, home: Path
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], Path]:
    managed = data.get("managed_skills", [])
    if not isinstance(managed, list):
        raise ValueError("managed_skills must be an array")

    managed_plugin_skills = data.get("managed_plugin_skills", [])
    if not isinstance(managed_plugin_skills, list):
        raise ValueError("managed_plugin_skills must be an array")

    unmanaged = data.get("unmanaged_repo_local_skills", [])
    if not isinstance(unmanaged, list):
        raise ValueError("unmanaged_repo_local_skills must be an array")

    seen: set[tuple[str, str]] = set()
    validated_managed: list[dict[str, Any]] = []
    for label, items in (
        ("managed_skills", managed),
        ("managed_plugin_skills", managed_plugin_skills),
    ):
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                raise ValueError(f"{label}[{idx}] must be an object")
            skill = ensure_str(item.get("skill"), "skill", idx)
            origin = ensure_str(item.get("origin"), "origin", idx)
            scope = ensure_str(item.get("scope"), "scope", idx)
            source_path = ensure_str(item.get("source_path"), "source_path", idx)
            upstream_ref = item.get("upstream_ref", "-")
            if origin not in ALLOWED_ORIGINS:
                raise ValueError(f"{label}[{idx}] invalid origin: {origin}")
            if scope not in ALLOWED_SCOPES:
                raise ValueError(f"{label}[{idx}] invalid scope: {scope}")

            if (skill, scope) in seen:
                raise ValueError(f"duplicate skill+scope entry: {skill}/{scope}")
            seen.add((skill, scope))

            repos_raw = item.get("repos", [])
            if not isinstance(repos_raw, list):
                raise ValueError(f"{label}[{idx}] repos must be an array")
            repos = [str(repo).strip() for repo in repos_raw if str(repo).strip()]
            if scope == "repo" and not repos:
                raise ValueError(f"{label}[{idx}] repo scope needs repos")
            if scope in {"global", "dormant"}:
                repos = []

            src = Path(source_path)
            if not src.is_absolute():
                src = (root_dir / src).resolve()
            if not (src / "SKILL.md").is_file():
                raise ValueError(f"source missing SKILL.md for {skill}: {src}")

            validated = {
                "skill": skill,
                "origin": origin,
                "scope": scope,
                "repos": repos,
                "source_path": source_path,
                "source_abs": src,
                "upstream_ref": str(upstream_ref).strip() or "-",
            }
            if label == "managed_plugin_skills":
                validated["source_plugin"] = str(item.get("source_plugin", "")).strip()
            validated_managed.append(validated)

    if not validated_managed:
        raise ValueError("managed_skills plus managed_plugin_skills must not both be empty")

    validated_unmanaged: list[dict[str, Any]] = []
    github_root_raw = data.get("paths", {}).get("github_root", "~/GitHub")
    if not isinstance(github_root_raw, str) or not github_root_raw.strip():
        raise ValueError("paths.github_root must be a non-empty string")
    github_root = expand_path(github_root_raw.strip(), home).resolve()

    for idx, item in enumerate(unmanaged):
        if not isinstance(item, dict):
            raise ValueError(f"unmanaged_repo_local_skills[{idx}] must be an object")
        repo = ensure_str(item.get("repo"), "repo", idx)
        skill = ensure_str(item.get("skill"), "skill", idx)
        repo_root = resolve_repo_root(repo, github_root, home)
        if repo_root.exists():
            skill_md = repo_local_skill_source(repo_root, skill)
            if not skill_md.is_file():
                raise ValueError(
                    f"unmanaged_repo_local_skills[{idx}] missing SKILL.md for "
                    f"{repo}/{skill}: {skill_md}"
                )
        validated_unmanaged.append({"repo": repo, "skill": skill})

    return validated_managed, validated_unmanaged, github_root


def run_sync(
    managed: list[dict[str, Any]],
    root_dir: Path,
    user_skills_dir: Path,
    github_root: Path,
    apply: bool,
    repo_filters: set[Path] | None = None,
) -> None:
    home = Path.home()
    repo_filters = repo_filters or set()
    desired_links: dict[Path, Path] = {}
    dormant_skills: set[str] = set()
    touched_links: set[Path] = set()
    for item in managed:
        skill = item["skill"]
        src = item["source_abs"]
        if item["scope"] == "global":
            dst = user_skills_dir / skill
            desired_links[dst] = src
            if sync_link(dst, src, apply):
                touched_links.add(dst)
            continue
        if item["scope"] == "dormant":
            dormant_skills.add(skill)
            continue

        for repo in item["repos"]:
            repo_root = resolve_repo_root(repo, github_root, home)
            if repo_filters and repo_root not in repo_filters:
                continue
            actual_repo = repo_git_root(repo_root)
            if actual_repo is None:
                if repo_root.exists():
                    print(f"WARNING: skipping existing non-git path: {repo_root}", file=sys.stderr)
                continue
            if repo_filters and actual_repo not in repo_filters:
                continue
            dst = actual_repo / ".agents" / "skills" / skill
            desired_links[dst] = src
            if sync_link(dst, src, apply):
                touched_links.add(dst)

    touched_links.update(prune_obsolete_global_links(root_dir, user_skills_dir, desired_links, apply))
    touched_links.update(
        prune_obsolete_repo_links(root_dir, github_root, desired_links, apply, repo_filters)
    )
    touched_links.update(
        prune_dormant_repo_links(root_dir, github_root, dormant_skills, apply, repo_filters)
    )
    if apply:
        # User-scope links are runtime state, even when a historical source
        # checkout still surrounds ~/.agents. Only repo-scoped links publish.
        repo_touched_links = {path for path in touched_links if path.parent != user_skills_dir}
        registered = register_current_codex_transaction_paths(repo_touched_links)
        for git_root, item in sorted(registered.items()):
            print(f"REGISTER CODEX STOP {git_root}: {' '.join(sorted(item.paths))}")
        repo_touched_links.update(path for path in desired_links if path.parent != user_skills_dir)
        stage_git_paths(repo_touched_links)


def prune_obsolete_global_links(
    root_dir: Path,
    user_skills_dir: Path,
    desired_links: dict[Path, Path],
    apply: bool,
) -> set[Path]:
    touched_links: set[Path] = set()
    managed_source_roots = [
        (root_dir / "skills-source").resolve(),
        (root_dir / "plugins-source").resolve(),
    ]
    skills_dir = user_skills_dir
    if not skills_dir.exists():
        return touched_links
    for entry in sorted(skills_dir.iterdir()):
        if not entry.is_symlink():
            continue
        target = resolved_target(entry)
        if not any(is_relative_to(target, root) for root in managed_source_roots):
            continue
        if entry in desired_links:
            continue
        print(f"PRUNE {entry}")
        if apply:
            entry.unlink()
        touched_links.add(entry)
    return touched_links


def prune_obsolete_repo_links(
    root_dir: Path,
    github_root: Path,
    desired_links: dict[Path, Path],
    apply: bool,
    repo_filters: set[Path] | None = None,
) -> set[Path]:
    touched_links: set[Path] = set()
    repo_filters = repo_filters or set()
    managed_source_roots = [
        (root_dir / "skills-source").resolve(),
        (root_dir / "plugins-source").resolve(),
    ]
    candidate_dirs: set[Path] = set()
    if not repo_filters or root_dir.resolve() in repo_filters:
        candidate_dirs.add(root_dir / ".agents" / "skills")
    if github_root.is_dir():
        for repo_root in github_root.iterdir():
            actual_repo = repo_git_root(repo_root)
            if actual_repo is None:
                continue
            if repo_filters and actual_repo not in repo_filters:
                continue
            candidate_dirs.add(actual_repo / ".agents" / "skills")

    for skills_dir in sorted(candidate_dirs):
        touched_links.update(
            prune_obsolete_managed_links_in_dir(
                skills_dir,
                desired_links,
                managed_source_roots,
                apply,
            )
        )
    return touched_links


def prune_obsolete_managed_links_in_dir(
    skills_dir: Path,
    desired_links: dict[Path, Path],
    managed_source_roots: list[Path],
    apply: bool,
) -> set[Path]:
    touched_links: set[Path] = set()
    if not skills_dir.exists():
        return touched_links
    for entry in sorted(skills_dir.iterdir()):
        if not entry.is_symlink():
            continue
        target = resolved_target(entry)
        if not any(is_relative_to(target, root) for root in managed_source_roots):
            continue
        if entry in desired_links:
            continue
        print(f"PRUNE {entry}")
        if apply:
            entry.unlink()
        touched_links.add(entry)
    return touched_links


def prune_dormant_links_in_dir(
    skills_dir: Path,
    dormant_skills: set[str],
    managed_source_roots: list[Path],
    apply: bool,
) -> set[Path]:
    touched_links: set[Path] = set()
    if not skills_dir.exists():
        return touched_links
    for entry in sorted(skills_dir.iterdir()):
        if entry.name not in dormant_skills:
            continue
        if not entry.is_symlink():
            continue
        target = resolved_target(entry)
        if not any(is_relative_to(target, root) for root in managed_source_roots):
            continue
        print(f"PRUNE {entry}")
        if apply:
            entry.unlink()
        touched_links.add(entry)
    return touched_links


def prune_dormant_repo_links(
    root_dir: Path,
    github_root: Path,
    dormant_skills: set[str],
    apply: bool,
    repo_filters: set[Path] | None = None,
) -> set[Path]:
    if not dormant_skills:
        return set()
    touched_links: set[Path] = set()
    repo_filters = repo_filters or set()
    managed_source_roots = [
        (root_dir / "skills-source").resolve(),
        (root_dir / "plugins-source").resolve(),
    ]
    candidate_dirs: set[Path] = set()
    if not repo_filters or root_dir.resolve() in repo_filters:
        candidate_dirs.add(root_dir / ".agents" / "skills")
    if github_root.is_dir():
        for repo_root in github_root.iterdir():
            actual_repo = repo_git_root(repo_root)
            if actual_repo is None:
                continue
            if repo_filters and actual_repo not in repo_filters:
                continue
            candidate_dirs.add(actual_repo / ".agents" / "skills")

    for skills_dir in sorted(candidate_dirs):
        touched_links.update(
            prune_dormant_links_in_dir(
                skills_dir,
                dormant_skills,
                managed_source_roots,
                apply,
            )
        )
    return touched_links


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the canonical skill registry and sync managed skill symlinks."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply link changes (default is dry-run for linking).",
    )
    parser.add_argument(
        "--repo",
        action="append",
        default=[],
        help="Limit repo-local sync to an exact repo path or repo name (repeatable).",
    )
    parser.add_argument(
        "--user-skills-dir",
        default=str(DEFAULT_USER_SKILLS_DIR),
        help="Codex user-scope skills directory to render global links into (default: ~/.agents/skills).",
    )
    parser.add_argument(
        "registry_file",
        nargs="?",
        default=str(DEFAULT_REGISTRY_FILE),
        help="Path to canonical registry JSON file.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    registry_file = Path(args.registry_file).expanduser().resolve()
    user_skills_dir = Path(args.user_skills_dir).expanduser().resolve()
    if not registry_file.is_file():
        print(f"Registry not found: {registry_file}", file=sys.stderr)
        return 1

    try:
        data = json.loads(registry_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"Invalid JSON in {registry_file}: {exc}", file=sys.stderr)
        return 1

    registry_dir = registry_file.parent
    root_dir = registry_dir.parent
    home = Path.home()

    try:
        managed, unmanaged, github_root = validate_registry(data, root_dir, home)
    except ValueError as exc:
        print(f"Registry validation failed: {exc}", file=sys.stderr)
        return 1

    repo_filters = {
        resolve_repo_root(raw.strip(), github_root, home)
        for raw in args.repo
        if raw.strip()
    }

    run_sync(managed, root_dir, user_skills_dir, github_root, args.apply, repo_filters)

    if args.apply:
        print("Apply complete.")
    else:
        print("Dry run complete. Re-run with --apply to execute link changes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
