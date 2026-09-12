from __future__ import annotations

import hashlib

from tests.control_plane.support import (
    REPO_ROOT,
    TempDirTestCase,
    commit_all,
    init_git_repo,
    make_control_plane_root,
    make_skill_source,
    read_json,
    run_command,
    write_json,
)


class ManagedSkillsRegistrySyncTests(TempDirTestCase):
    def test_syncs_managed_skill_links_and_prunes_stale_links(self) -> None:
        root = make_control_plane_root(self.temp_path)
        home = self.temp_path / "home"
        github_root = home / "GitHub"
        adi = init_git_repo(github_root / "adi")
        # A retained source checkout must not turn global runtime links into
        # repository changes (including when those links are ignored).
        runtime = init_git_repo(home / ".agents", with_initial_commit=True)
        (runtime / ".gitignore").write_text("/skills/\n", encoding="utf-8")
        commit_all(runtime, "ignore generated user-scope skills")

        global_source = make_skill_source(
            root / "skills-source/owned/global-helper",
            "global-helper",
        )
        repo_source = make_skill_source(
            root / "skills-source/owned/repo-helper",
            "repo-helper",
        )
        dormant_source = make_skill_source(
            root / "skills-source/owned/dormant-helper",
            "dormant-helper",
        )
        make_skill_source(adi / ".agents/skills/local-review", "local-review")

        stale_source = make_skill_source(
            root / "skills-source/owned/stale-helper",
            "stale-helper",
        )
        user_skills_dir = home / ".agents/skills"
        stale_link = user_skills_dir / "stale-helper"
        stale_link.parent.mkdir(parents=True, exist_ok=True)
        stale_link.symlink_to(stale_source)
        dormant_global_link = user_skills_dir / "dormant-helper"
        dormant_global_link.parent.mkdir(parents=True, exist_ok=True)
        dormant_global_link.symlink_to(dormant_source)
        dormant_repo_link = adi / ".agents/skills/dormant-helper"
        dormant_repo_link.parent.mkdir(parents=True, exist_ok=True)
        dormant_repo_link.symlink_to(dormant_source)

        registry_path = root / "skills/registry.json"
        write_json(
            registry_path,
            {
                "managed_skills": [
                    {
                        "skill": "global-helper",
                        "origin": "owned",
                        "scope": "global",
                        "source_path": "skills-source/owned/global-helper",
                    },
                    {
                        "skill": "repo-helper",
                        "origin": "owned",
                        "scope": "repo",
                        "repos": ["adi"],
                        "source_path": "skills-source/owned/repo-helper",
                    },
                    {
                        "skill": "dormant-helper",
                        "origin": "owned",
                        "scope": "dormant",
                        "repos": [],
                        "source_path": "skills-source/owned/dormant-helper",
                    },
                ],
                "paths": {
                    "github_root": str(github_root),
                },
                "unmanaged_repo_local_skills": [
                    {
                        "repo": "adi",
                        "skill": "local-review",
                    }
                ],
            },
        )

        run_command(
            [
                "python3",
                str(REPO_ROOT / "scripts/sync-skills-registry.py"),
                "--apply",
                str(registry_path),
            ],
            env={"HOME": str(home), "CODEX_THREAD_ID": "global-runtime-test"},
        )

        global_link = user_skills_dir / "global-helper"
        repo_link = adi / ".agents/skills/repo-helper"

        self.assertTrue(global_link.is_symlink())
        self.assertEqual(global_source.resolve(), global_link.resolve())
        self.assertTrue(repo_link.is_symlink())
        self.assertEqual(repo_source.resolve(), repo_link.resolve())
        self.assertFalse(stale_link.exists())
        self.assertFalse(dormant_global_link.exists())
        self.assertFalse(dormant_repo_link.exists())
        self.assertEqual("", run_command(["git", "status", "--porcelain"], cwd=runtime).stdout)
        digest = hashlib.sha256(b"global-runtime-test").hexdigest()
        transaction = read_json(home / ".local/state/agents-control-plane/codex-stop-transactions" / f"{digest}.json")
        self.assertEqual([str(adi.resolve())], [item["root"] for item in transaction["repositories"]])

        self.assertFalse((root / "docs/references/registry").exists())

    def test_stages_repo_skill_link_additions_and_pruned_stale_links(self) -> None:
        root = make_control_plane_root(self.temp_path)
        home = self.temp_path / "home"
        github_root = home / "GitHub"
        adi = init_git_repo(github_root / "adi", with_initial_commit=True)

        repo_source = make_skill_source(
            root / "skills-source/owned/repo-helper",
            "repo-helper",
        )
        stale_source = make_skill_source(
            root / "skills-source/owned/stale-helper",
            "stale-helper",
        )
        stale_link = adi / ".agents/skills/stale-helper"
        stale_link.parent.mkdir(parents=True, exist_ok=True)
        stale_link.symlink_to(stale_source)
        commit_all(adi, "track stale generated skill link")

        registry_path = root / "skills/registry.json"
        write_json(
            registry_path,
            {
                "managed_skills": [
                    {
                        "skill": "repo-helper",
                        "origin": "owned",
                        "scope": "repo",
                        "repos": ["adi"],
                        "source_path": "skills-source/owned/repo-helper",
                    }
                ],
                "paths": {
                    "github_root": str(github_root),
                },
                "unmanaged_repo_local_skills": [],
            },
        )

        run_command(
            [
                "python3",
                str(REPO_ROOT / "scripts/sync-skills-registry.py"),
                "--apply",
                str(registry_path),
            ],
            env={
                "HOME": str(home),
                "CODEX_THREAD_ID": "test-thread",
            },
        )

        repo_link = adi / ".agents/skills/repo-helper"
        self.assertTrue(repo_link.is_symlink())
        self.assertEqual(repo_source.resolve(), repo_link.resolve())
        self.assertFalse(stale_link.exists())

        status = run_command(
            ["git", "-C", str(adi), "status", "--short", "--", ".agents/skills"],
        ).stdout
        self.assertIn("A  .agents/skills/repo-helper", status)
        self.assertIn("D  .agents/skills/stale-helper", status)
        transaction_path = (
            home
            / ".local/state/agents-control-plane/codex-stop-transactions"
            / f"{hashlib.sha256(b'test-thread').hexdigest()}.json"
        )
        transaction = read_json(transaction_path)
        self.assertEqual("test-thread", transaction["thread_id"])
        self.assertEqual(
            [
                {
                    "commit": "",
                    "paths": [
                        ".agents/skills/repo-helper",
                        ".agents/skills/stale-helper",
                    ],
                    "phase": "pending",
                    "root": str(adi.resolve()),
                }
            ],
            transaction["repositories"],
        )

    def test_rejects_missing_repo_local_skill_source_for_existing_repo(self) -> None:
        root = make_control_plane_root(self.temp_path)
        home = self.temp_path / "home"
        github_root = home / "GitHub"
        adi = init_git_repo(github_root / "adi")

        make_skill_source(root / "skills-source/owned/global-helper", "global-helper")
        (adi / ".agents/skills/missing-local").mkdir(parents=True, exist_ok=True)

        registry_path = root / "skills/registry.json"
        write_json(
            registry_path,
            {
                "managed_skills": [
                    {
                        "skill": "global-helper",
                        "origin": "owned",
                        "scope": "global",
                        "source_path": "skills-source/owned/global-helper",
                    }
                ],
                "paths": {
                    "github_root": str(github_root),
                },
                "unmanaged_repo_local_skills": [
                    {
                        "repo": "adi",
                        "skill": "missing-local",
                    }
                ],
            },
        )

        result = run_command(
            [
                "python3",
                str(REPO_ROOT / "scripts/sync-skills-registry.py"),
                str(registry_path),
            ],
            env={"HOME": str(home)},
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "missing SKILL.md for adi/missing-local",
            result.stderr,
        )
