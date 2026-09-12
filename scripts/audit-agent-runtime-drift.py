#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore


SCHEMA_VERSION = "1.0"
COMMAND = "audit-agent-runtime-drift"
APP_MANAGED_PLUGIN_IDS = {
    "documents@openai-primary-runtime",
    "pdf@openai-primary-runtime",
    "plugin-management@openai-curated-remote",
    "presentations@openai-primary-runtime",
    "spreadsheets@openai-primary-runtime",
    "sites@openai-curated-remote",
    "template-creator@openai-primary-runtime",
}
REVIEW_MARKETPLACE_PREFIXES = ("openai-",)

def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def tail_text(value: str, *, max_lines: int = 80, max_chars: int = 12000) -> str:
    lines = value.splitlines()
    if len(lines) > max_lines:
        lines = ["..."] + lines[-max_lines:]
    text = "\n".join(lines)
    if len(text) > max_chars:
        return "..." + text[-max_chars:]
    return text


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_toml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return tomllib.loads(path.read_text(encoding="utf-8"))


def check_result(
    name: str,
    status: str,
    summary: str,
    *,
    details: dict[str, Any] | None = None,
    hint: str | None = None,
    error_code: str | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "summary": summary,
        "details": details or {},
        "hint": hint,
        "error_code": error_code,
    }


def run_control_plane_check(agents_repo: Path, timeout_sec: int, *, skip: bool) -> dict[str, Any]:
    name = "agent_control_plane"
    script = agents_repo / "scripts" / "check-agent-control-planes.sh"
    if skip:
        return check_result(name, "skipped", "control-plane check skipped by request")
    if not script.is_file() or not os.access(script, os.X_OK):
        return check_result(
            name,
            "error",
            f"missing executable: {script}",
            hint="Restore the shared control-plane check script in ~/GitHub/agents/scripts/.",
            error_code="E_MISSING_CHECK_SCRIPT",
        )

    start = time.monotonic()
    try:
        completed = subprocess.run(
            [str(script)],
            cwd=str(agents_repo),
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        duration_ms = int((time.monotonic() - start) * 1000)
        output = "\n".join(part for part in [exc.stdout or "", exc.stderr or ""] if part)
        return check_result(
            name,
            "error",
            f"control-plane check timed out after {timeout_sec}s",
            details={"duration_ms": duration_ms, "output_tail": tail_text(output)},
            hint="Run ~/GitHub/agents/scripts/check-agent-control-planes.sh manually and inspect the slow or stuck check.",
            error_code="E_CONTROL_PLANE_TIMEOUT",
        )

    duration_ms = int((time.monotonic() - start) * 1000)
    combined_output = "\n".join(part for part in [completed.stdout, completed.stderr] if part)
    details = {
        "duration_ms": duration_ms,
        "exit_code": completed.returncode,
        "output_tail": tail_text(combined_output),
    }
    if completed.returncode == 0:
        return check_result(name, "ok", "shared agent control-plane check passed", details=details)
    return check_result(
        name,
        "error",
        f"shared agent control-plane check failed with exit code {completed.returncode}",
        details=details,
        hint="Run ~/GitHub/agents/scripts/check-agent-control-planes.sh and fix the first failing control-plane surface.",
        error_code="E_CONTROL_PLANE_CHECK_FAILED",
    )


def configured_plugin_ids(agents_repo: Path) -> set[str]:
    ids: set[str] = set()
    data = load_toml(agents_repo / "codex/config/global.config.toml")
    plugins = data.get("plugins", {})
    if isinstance(plugins, dict):
        ids.update(str(key) for key in plugins.keys())
    return ids


def enabled_canonical_plugin_ids(agents_repo: Path) -> set[str]:
    enabled: set[str] = set()
    data = load_toml(agents_repo / "codex/config/global.config.toml")
    plugins = data.get("plugins", {})
    if not isinstance(plugins, dict):
        return enabled
    for plugin_id, config in plugins.items():
        if isinstance(config, dict) and config.get("enabled") is True:
            enabled.add(str(plugin_id))
    return enabled


def expand_registry_path(raw: str, home: Path) -> Path:
    if raw.startswith("~/"):
        return home / raw[2:]
    return Path(raw)


def resolve_registry_repo(repo: str, github_root: Path, home: Path) -> Path:
    if repo.startswith("~/") or repo.startswith("/"):
        return expand_registry_path(repo, home).resolve()
    return (github_root / repo).resolve()


def git_worktree_root(path: Path) -> Path | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    root = completed.stdout.strip()
    if not root:
        return None
    return Path(root).resolve()


def registry_plugin_entries(agents_repo: Path, home: Path) -> list[dict[str, Any]]:
    registry_path = agents_repo / "plugins" / "registry.json"
    if not registry_path.is_file():
        return []
    registry = load_json(registry_path)
    paths = registry.get("paths", {})
    if not isinstance(paths, dict):
        paths = {}
    github_root_raw = paths.get("github_root", "~/GitHub")
    github_root = expand_registry_path(str(github_root_raw), home).resolve()

    entries: list[dict[str, Any]] = []
    for item in registry.get("managed_plugins", []):
        if not isinstance(item, dict):
            continue
        plugin = item.get("plugin")
        marketplace = item.get("marketplace")
        if not isinstance(plugin, str) or not isinstance(marketplace, str):
            continue
        repos_raw = item.get("repos", [])
        repos = [str(repo).strip() for repo in repos_raw] if isinstance(repos_raw, list) else []
        entries.append(
            {
                "id": f"{plugin}@{marketplace}",
                "enabled": item.get("enabled") is True,
                "scope": str(item.get("scope", "global")).strip() or "global",
                "repos": [repo for repo in repos if repo],
                "github_root": github_root,
            }
        )
    return entries


def registry_plugin_ids(agents_repo: Path, home: Path) -> set[str]:
    return {entry["id"] for entry in registry_plugin_entries(agents_repo, home)}


def enabled_registry_plugin_entries(agents_repo: Path, home: Path) -> list[dict[str, Any]]:
    return [entry for entry in registry_plugin_entries(agents_repo, home) if entry["enabled"]]


def installed_codex_plugins(home: Path) -> list[dict[str, Any]]:
    cache_root = home / ".codex" / "plugins" / "cache"
    if not cache_root.is_dir():
        return []

    plugins: list[dict[str, Any]] = []
    for manifest in sorted(cache_root.glob("*/*/*/.codex-plugin/plugin.json")):
        version_dir = manifest.parent.parent
        plugin_dir = version_dir.parent
        marketplace_dir = plugin_dir.parent
        try:
            metadata = load_json(manifest)
        except Exception as exc:
            plugins.append(
                {
                    "id": f"{plugin_dir.name}@{marketplace_dir.name}",
                    "name": plugin_dir.name,
                    "marketplace": marketplace_dir.name,
                    "version": version_dir.name,
                    "path": str(version_dir),
                    "manifest_error": str(exc),
                }
            )
            continue
        plugin_name = metadata.get("name") if isinstance(metadata.get("name"), str) else plugin_dir.name
        plugins.append(
            {
                "id": f"{plugin_name}@{marketplace_dir.name}",
                "name": plugin_name,
                "marketplace": marketplace_dir.name,
                "version": metadata.get("version", version_dir.name),
                "path": str(version_dir),
                "display_name": (metadata.get("interface") or {}).get("displayName")
                if isinstance(metadata.get("interface"), dict)
                else None,
                "manifest_error": None,
            }
        )
    return plugins


def audit_codex_plugins(agents_repo: Path, home: Path) -> dict[str, Any]:
    installed = installed_codex_plugins(home)
    known_plugin_ids = configured_plugin_ids(agents_repo) | registry_plugin_ids(agents_repo, home)

    malformed = [plugin for plugin in installed if plugin.get("manifest_error")]
    if malformed:
        return check_result(
            "codex_plugin_inventory",
            "error",
            "one or more Codex plugin manifests could not be read",
            details={"malformed_plugins": malformed},
            hint="Inspect the listed ~/.codex/plugins/cache plugin manifests and reinstall or remove broken runtime plugins.",
            error_code="E_CODEX_PLUGIN_MANIFEST_INVALID",
        )

    unknown_review_plugins = [
        plugin
        for plugin in installed
        if str(plugin["marketplace"]).startswith(REVIEW_MARKETPLACE_PREFIXES)
        and plugin["id"] not in known_plugin_ids
        and plugin["id"] not in APP_MANAGED_PLUGIN_IDS
    ]
    if unknown_review_plugins:
        return check_result(
            "codex_plugin_inventory",
            "error",
            "unclassified OpenAI Codex plugin(s) installed locally",
            details={
                "unknown_plugins": unknown_review_plugins,
                "known_plugin_ids": sorted(known_plugin_ids),
            },
            hint=(
                "Decide whether each plugin belongs in plugins/registry.json or should be "
                "removed from the local Codex runtime."
            ),
            error_code="E_UNCLASSIFIED_CODEX_PLUGIN",
        )

    return check_result(
        "codex_plugin_inventory",
        "ok",
        "installed OpenAI Codex plugins are classified",
        details={
            "installed_plugins": installed,
            "known_plugin_ids": sorted(known_plugin_ids),
        },
    )


def plugin_enabled_in_config(config_path: Path, plugin_id: str) -> bool:
    data = load_toml(config_path)
    plugins = data.get("plugins", {})
    if not isinstance(plugins, dict):
        return False
    plugin_config = plugins.get(plugin_id)
    return isinstance(plugin_config, dict) and plugin_config.get("enabled") is True


def audit_required_codex_plugins(agents_repo: Path, home: Path) -> dict[str, Any]:
    canonical_enabled = enabled_canonical_plugin_ids(agents_repo)
    registry_enabled = enabled_registry_plugin_entries(agents_repo, home)
    required_ids = sorted(
        set(canonical_enabled) | {entry["id"] for entry in registry_enabled}
    )
    installed_ids = {plugin["id"] for plugin in installed_codex_plugins(home)}
    live_global_config = home / ".codex" / "config.toml"

    failures: list[str] = []
    repo_targets: dict[str, list[str]] = {}
    details: dict[str, Any] = {
        "required_plugin_ids": required_ids,
        "installed_plugin_ids": sorted(installed_ids),
        "live_global_config": str(live_global_config),
    }
    for plugin_id in canonical_enabled:
        if plugin_id not in installed_ids:
            failures.append(f"{plugin_id} is enabled canonically but not installed in ~/.codex/plugins/cache")
        if not plugin_enabled_in_config(live_global_config, plugin_id):
            failures.append(f"{plugin_id} is not enabled in live ~/.codex/config.toml")

    for entry in registry_enabled:
        plugin_id = entry["id"]
        scope = entry["scope"]
        if scope == "global":
            if plugin_id not in installed_ids:
                failures.append(f"{plugin_id} is enabled globally but not installed in ~/.codex/plugins/cache")
            if not plugin_enabled_in_config(live_global_config, plugin_id):
                failures.append(f"{plugin_id} is not enabled in live ~/.codex/config.toml")
            continue
        if scope != "repo":
            continue

        existing_targets: list[Path] = []
        skipped_non_git_targets: list[str] = []
        github_root = entry["github_root"]
        if not isinstance(github_root, Path):
            continue
        for repo_ref in entry["repos"]:
            repo_root = resolve_registry_repo(str(repo_ref), github_root, home)
            if not repo_root.exists():
                continue
            git_root = git_worktree_root(repo_root)
            if git_root is None:
                skipped_non_git_targets.append(str(repo_root))
                continue
            existing_targets.append(git_root)
        if not existing_targets:
            if skipped_non_git_targets:
                details.setdefault("skipped_non_git_repo_plugin_targets", {})[plugin_id] = skipped_non_git_targets
            continue

        repo_targets[plugin_id] = [str(path) for path in existing_targets]
        if plugin_id not in installed_ids:
            failures.append(f"{plugin_id} is enabled for repo scope but not installed in ~/.codex/plugins/cache")
        for repo_root in existing_targets:
            repo_config = repo_root / ".codex" / "config.toml"
            if not plugin_enabled_in_config(repo_config, plugin_id):
                failures.append(f"{plugin_id} is not enabled in {repo_config}")

    details["repo_plugin_targets"] = repo_targets

    if failures:
        return check_result(
            "codex_required_plugins",
            "error",
            "required Codex plugin availability check failed",
            details={**details, "failures": failures},
            hint="Re-run ~/GitHub/agents/codex/scripts/sync-config.sh --apply or install the missing Codex plugin.",
            error_code="E_REQUIRED_CODEX_PLUGIN_UNAVAILABLE",
        )

    return check_result(
        "codex_required_plugins",
        "ok",
        "required Codex plugins are installed and enabled",
        details=details,
    )


def audit_claude_session_archiver(
    agents_repo: Path,
    home: Path,
    *,
    timeout_sec: int = 60,
    support_dir: Path | None = None,
    handshake_glob: str | None = None,
) -> dict[str, Any]:
    """Exercise the Claude session archiver schema guard via a safe dry-run.

    The archiver fails fast (no writes) when Claude Desktop's session metadata shape
    changes. Surfacing that here means the daily health check / Slack notification path
    flags the drift so the archiver can be updated, instead of silently no-op'ing.
    """
    name = "claude_session_archiver"
    base = support_dir if support_dir is not None else (home / "Library/Application Support/Claude")
    sessions_present = (base / "claude-code-sessions").is_dir() or (base / "local-agent-mode-sessions").is_dir()
    if not sessions_present:
        return check_result(name, "skipped", "Claude Desktop session store not found on this machine")

    script = agents_repo / "codex" / "scripts" / "archive-stale-claude-sessions.py"
    if not script.is_file() or not os.access(script, os.X_OK):
        return check_result(
            name,
            "error",
            f"missing executable: {script}",
            hint="Restore codex/scripts/archive-stale-claude-sessions.py before the archiver can run.",
            error_code="E_MISSING_CLAUDE_ARCHIVER",
        )

    cmd = [str(script), "--dry-run", "--no-input", "--json", "--support-dir", str(base)]
    if handshake_glob is not None:
        cmd += ["--handshake-glob", handshake_glob]
    if support_dir is not None:
        cmd += [
            "--lock",
            str(base.parent / "archive-stale-claude-sessions.lock"),
            "--backup-root",
            str(base.parent / "archive-stale-claude-sessions-backups"),
        ]
    try:
        completed = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_sec, check=False
        )
    except subprocess.TimeoutExpired:
        return check_result(
            name,
            "error",
            f"claude session archiver dry-run timed out after {timeout_sec}s",
            error_code="E_CLAUDE_ARCHIVER_TIMEOUT",
            hint="Run ~/GitHub/agents/codex/scripts/archive-stale-claude-sessions.py --plain and inspect.",
        )

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return check_result(
            name,
            "error",
            "claude session archiver emitted non-JSON output",
            details={"output_tail": tail_text("\n".join([completed.stdout, completed.stderr]))},
            error_code="E_CLAUDE_ARCHIVER_OUTPUT",
            hint="Run the archiver manually; its output envelope changed unexpectedly.",
        )

    error = payload.get("error") or {}
    if completed.returncode == 0 and payload.get("status") == "ok":
        data = payload.get("data", {})
        return check_result(
            name,
            "ok",
            f"claude session archiver dry-run healthy (scanned={data.get('scanned')})",
            details={"scanned": data.get("scanned"), "archivable": data.get("archived_count")},
        )

    if error.get("code") == "E_SCHEMA":
        return check_result(
            name,
            "error",
            f"Claude session metadata schema changed: {error.get('message')}",
            details={"output_tail": tail_text(completed.stdout)},
            error_code="E_CLAUDE_SESSION_SCHEMA_DRIFT",
            hint=(
                "Claude Desktop changed its session metadata shape. Update "
                "codex/scripts/archive-stale-claude-sessions.py (REQUIRED_SESSION_KEYS / load_session) "
                "to match, then re-run."
            ),
        )

    return check_result(
        name,
        "error",
        f"claude session archiver dry-run failed: {error.get('message') or completed.returncode}",
        details={"exit_code": completed.returncode, "output_tail": tail_text(completed.stdout or completed.stderr)},
        error_code=error.get("code") or "E_CLAUDE_ARCHIVER_FAILED",
        hint="Run ~/GitHub/agents/codex/scripts/archive-stale-claude-sessions.py --plain and fix the first failure.",
    )


def run_runtime_drift_checks(args: argparse.Namespace, agents_repo: Path, home: Path) -> list[dict[str, Any]]:
    return [
        run_control_plane_check(agents_repo, args.timeout_sec, skip=args.skip_control_plane_check),
        audit_codex_plugins(agents_repo, home),
        audit_required_codex_plugins(agents_repo, home),
        audit_claude_session_archiver(agents_repo, home, timeout_sec=args.timeout_sec),
    ]


def has_required_plugin_drift(checks: list[dict[str, Any]]) -> bool:
    return any(
        check["name"] == "codex_required_plugins"
        and check["status"] == "error"
        and check.get("error_code") == "E_REQUIRED_CODEX_PLUGIN_UNAVAILABLE"
        for check in checks
    )


def repair_managed_plugin_drift(agents_repo: Path, home: Path, timeout_sec: int) -> dict[str, Any]:
    script = agents_repo / "codex" / "scripts" / "sync-config.sh"
    name = "managed_plugin_repair"
    if not script.is_file() or not os.access(script, os.X_OK):
        return check_result(
            name,
            "error",
            f"missing executable: {script}",
            hint="Restore codex/scripts/sync-config.sh before managed plugin drift can self-heal.",
            error_code="E_MISSING_REPAIR_SCRIPT",
        )

    start = time.monotonic()
    try:
        completed = subprocess.run(
            [
                str(script),
                "--apply",
                "--global-only",
                "--github-root",
                str(home / "GitHub"),
            ],
            cwd=str(agents_repo),
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        duration_ms = int((time.monotonic() - start) * 1000)
        output = "\n".join(part for part in [exc.stdout or "", exc.stderr or ""] if part)
        return check_result(
            name,
            "error",
            f"managed plugin repair timed out after {timeout_sec}s",
            details={"duration_ms": duration_ms, "output_tail": tail_text(output)},
            hint="Run ~/GitHub/agents/codex/scripts/sync-config.sh --apply --global-only manually.",
            error_code="E_MANAGED_PLUGIN_REPAIR_TIMEOUT",
        )

    duration_ms = int((time.monotonic() - start) * 1000)
    combined_output = "\n".join(part for part in [completed.stdout, completed.stderr] if part)
    details = {
        "duration_ms": duration_ms,
        "exit_code": completed.returncode,
        "output_tail": tail_text(combined_output),
    }
    if completed.returncode == 0:
        return check_result(
            name,
            "ok",
            "managed Codex plugin drift repaired with sync-config.sh",
            details=details,
        )
    return check_result(
        name,
        "error",
        f"managed plugin repair failed with exit code {completed.returncode}",
        details=details,
        hint="Run ~/GitHub/agents/codex/scripts/sync-config.sh --apply --global-only and inspect the first failure.",
        error_code="E_MANAGED_PLUGIN_REPAIR_FAILED",
    )


def build_payload(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    start = time.monotonic()
    request_id = str(uuid.uuid4())
    agents_repo = Path(args.agents_repo).expanduser().resolve()
    home = Path(args.home).expanduser().resolve()

    checks = run_runtime_drift_checks(args, agents_repo, home)
    repair_check: dict[str, Any] | None = None
    if args.repair_managed_plugin_drift and has_required_plugin_drift(checks):
        repair_check = repair_managed_plugin_drift(agents_repo, home, args.timeout_sec)
        if repair_check["status"] == "ok":
            checks = run_runtime_drift_checks(args, agents_repo, home)
        checks.append(repair_check)

    error_checks = [check for check in checks if check["status"] == "error"]
    warning_checks = [check for check in checks if check["status"] == "warning"]
    exit_code = 1 if error_checks else 0
    status = "error" if error_checks else "ok"

    payload = {
        "schema_version": SCHEMA_VERSION,
        "command": COMMAND,
        "status": status,
        "data": {
            "summary": {
                "checks": len(checks),
                "errors": len(error_checks),
                "warnings": len(warning_checks),
                "skipped": sum(1 for check in checks if check["status"] == "skipped"),
            },
            "checks": checks,
        },
        "error": None
        if not error_checks
        else {
            "code": "E_AGENT_RUNTIME_DRIFT",
            "message": f"{len(error_checks)} agent runtime drift check(s) failed",
            "retryable": False,
            "hint": "Run ~/GitHub/agents/scripts/audit-agent-runtime-drift.py --plain and fix the failing checks.",
        },
        "meta": {
            "request_id": request_id,
            "duration_ms": int((time.monotonic() - start) * 1000),
            "timestamp_utc": utc_now(),
            "agents_repo": str(agents_repo),
            "home": str(home),
        },
    }
    return payload, exit_code


def print_plain(payload: dict[str, Any]) -> None:
    summary = payload["data"]["summary"]
    heading = "OK" if payload["status"] == "ok" else "FAILED"
    print(f"Agent runtime drift audit: {heading}")
    print(
        "checks: "
        f"total={summary['checks']} errors={summary['errors']} "
        f"warnings={summary['warnings']} skipped={summary['skipped']}"
    )
    for check in payload["data"]["checks"]:
        label = {
            "ok": "OK",
            "warning": "WARN",
            "error": "FAIL",
            "skipped": "SKIP",
        }.get(check["status"], check["status"].upper())
        print(f"- {label} {check['name']}: {check['summary']}")
        if check.get("hint") and check["status"] != "ok":
            print(f"  hint: {check['hint']}")
        if check["status"] == "error" and check.get("details"):
            failures = check["details"].get("failures")
            if isinstance(failures, list):
                for failure in failures[:8]:
                    print(f"  - {failure}")
            unknown = check["details"].get("unknown_plugins")
            if isinstance(unknown, list):
                for plugin in unknown[:8]:
                    print(f"  - {plugin.get('id')} at {plugin.get('path')}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit local agent runtime drift against the ~/GitHub/agents control plane."
    )
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true", help="Emit the stable JSON result contract (default).")
    output.add_argument("--plain", action="store_true", help="Emit concise plain text for operator inspection.")
    parser.add_argument("--no-input", action="store_true", help="Accepted for non-interactive callers; this command never prompts.")
    parser.add_argument("--agents-repo", default=str(Path(__file__).resolve().parents[1]), help="Path to the agents control-plane repo.")
    parser.add_argument("--home", default=str(Path.home()), help="Home directory whose agent runtimes should be audited.")
    parser.add_argument("--timeout-sec", type=int, default=600, help="Timeout for the shared control-plane check.")
    parser.add_argument(
        "--skip-control-plane-check",
        action="store_true",
        help="Skip the full shared control-plane check; intended for focused tests only.",
    )
    parser.add_argument(
        "--repair-managed-plugin-drift",
        action="store_true",
        help=(
            "Repair required managed Codex plugin config/cache drift by running "
            "codex/scripts/sync-config.sh --apply --global-only once, then re-audit."
        ),
    )
    args = parser.parse_args(argv)
    if args.timeout_sec < 1:
        parser.error("--timeout-sec must be >= 1")
    return args


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    payload, exit_code = build_payload(args)
    if args.plain:
        print_plain(payload)
    else:
        print(json.dumps(payload, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
