from __future__ import annotations

import os
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch

from hooks.scripts import codex_turn_changes
from hooks.scripts.codex_shell_paths import command_repository_paths
from hooks.scripts import stop
from tests.control_plane.support import (
    REPO_ROOT,
    TempDirTestCase,
    init_git_repo,
    run_command,
    write_executable,
)


class FakeAppServerClient:
    def __init__(self, responses, **_kwargs):  # noqa: ANN001
        self.responses = responses

    def __enter__(self):  # noqa: ANN201
        return self

    def __exit__(self, *_args):  # noqa: ANN001, ANN201
        return None

    def request(self, method, params):  # noqa: ANN001, ANN201
        key = (method, params.get("threadId") or params.get("cursor") or "first")
        return self.responses[key]


class CodexTurnChangesTests(TempDirTestCase):
    def test_collects_parent_and_subagent_paths(self) -> None:
        root_path = str(self.temp_path / "root.txt")
        child_path = str(self.temp_path / "child.txt")
        grandchild_path = str(self.temp_path / "grandchild.txt")
        moved_path = str(self.temp_path / "moved.txt")
        responses = {
            ("thread/read", "root"): {
                "thread": {
                    "id": "root",
                    "sessionId": "tree",
                    "turns": [
                        {
                            "id": "turn-root",
                            "startedAt": 100,
                            "items": [
                                {
                                    "type": "fileChange",
                                    "status": "completed",
                                    "changes": [{"path": root_path, "kind": {"type": "update"}}],
                                }
                            ],
                        }
                    ],
                }
            },
            ("thread/list", "first"): {
                "data": [
                    {"id": "root", "sessionId": "tree", "createdAt": 50, "status": {"type": "idle"}},
                    {
                        "id": "child",
                        "sessionId": "child-session",
                        "parentThreadId": "root",
                        "createdAt": 110,
                        "status": {"type": "idle"},
                    },
                    {
                        "id": "old-child",
                        "sessionId": "old-child-session",
                        "parentThreadId": "root",
                        "createdAt": 90,
                        "status": {"type": "idle"},
                    },
                    {
                        "id": "grandchild",
                        "sessionId": "grandchild-session",
                        "parentThreadId": "child",
                        "createdAt": 120,
                        "status": {"type": "idle"},
                    },
                    {
                        "id": "competitor",
                        "sessionId": "other-tree",
                        "createdAt": 120,
                        "cwd": str(self.temp_path),
                        "status": {"type": "active"},
                    },
                    {
                        "id": "stale-child",
                        "sessionId": "stale-child-session",
                        "parentThreadId": "root",
                        "createdAt": 10,
                        "updatedAt": 99,
                        "status": {"type": "idle"},
                    },
                ],
                "nextCursor": None,
            },
            ("thread/read", "child"): {
                "thread": {
                    "id": "child",
                    "turns": [
                        {
                            "id": "turn-child",
                            "startedAt": 115,
                            "items": [
                                {
                                    "type": "fileChange",
                                    "changes": [
                                        {
                                            "path": child_path,
                                            "kind": {"type": "move", "move_path": moved_path},
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                }
            },
            ("thread/read", "old-child"): {
                "thread": {
                    "id": "old-child",
                    "turns": [
                        {
                            "id": "old-turn",
                            "startedAt": 90,
                            "items": [
                                {
                                    "type": "fileChange",
                                    "changes": [
                                        {
                                            "path": str(self.temp_path / "old.txt"),
                                            "kind": {"type": "update"},
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                }
            },
            ("thread/read", "grandchild"): {
                "thread": {
                    "id": "grandchild",
                    "turns": [
                        {
                            "id": "grandchild-turn",
                            "startedAt": 125,
                            "items": [
                                {
                                    "type": "fileChange",
                                    "changes": [
                                        {
                                            "path": grandchild_path,
                                            "kind": {"type": "update"},
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                }
            },
        }

        with patch.object(
            codex_turn_changes,
            "AppServerClient",
            side_effect=lambda **kwargs: FakeAppServerClient(responses, **kwargs),
        ):
            result = codex_turn_changes.collect_codex_turn_changes("root")

        self.assertEqual(result.session_id, "tree")
        self.assertEqual(result.parent_thread_id, "")
        self.assertEqual(
            set(result.descendant_thread_ids),
            {"child", "grandchild"},
        )
        self.assertEqual(
            set(result.touched_paths),
            {root_path, child_path, moved_path, grandchild_path},
        )


class CodexMultiRepoStopTests(TempDirTestCase):
    def make_published_repo(self, name: str):  # noqa: ANN201
        remote = init_git_repo(self.temp_path / f"{name}-remote.git")
        run_command(
            ["git", "-C", str(remote), "config", "receive.denyCurrentBranch", "updateInstead"]
        )
        repo = init_git_repo(self.temp_path / name, with_initial_commit=True)
        run_command(["git", "-C", str(repo), "remote", "add", "origin", str(remote)])
        run_command(["git", "-C", str(repo), "push", "-u", "origin", "main"])
        return repo, remote

    def changes(self, paths):  # noqa: ANN001, ANN201
        return SimpleNamespace(
            touched_paths=tuple(str(path) for path in paths),
            parent_thread_id="",
            descendant_thread_ids=(),
        )

    def test_shell_python_edit_publishes_sibling_but_not_unreferenced_repo(self) -> None:
        first, first_remote = self.make_published_repo("backend")
        second, second_remote = self.make_published_repo("frontend with spaces")
        untouched, _ = self.make_published_repo("unreferenced")
        clean, _ = self.make_published_repo("clean")
        (first / "backend.txt").write_text("backend\n", encoding="utf-8")
        (untouched / "unrelated.txt").write_text("unrelated\n", encoding="utf-8")
        script = (
            "from pathlib import Path; "
            f"root = Path({str(second)!r}); "
            "(root / 'frontend.txt').write_text('frontend\\n'); "
            "raise SystemExit(1)"
        )
        result = subprocess.run([sys.executable, "-c", script], cwd=first, check=False)
        self.assertEqual(result.returncode, 1)
        changes = self.changes([])
        changes.shell_paths = tuple(command_repository_paths({"items": [
            {"type": "commandExecution", "cwd": str(first),
             "command": f"python - <<'PY'\n{script}\nPY", "status": "completed", "exitCode": 1},
            {"type": "commandExecution", "cwd": str(clean),
             "command": "git status --short", "status": "completed", "exitCode": 0},
        ]}))
        notified = []
        with patch.dict(os.environ, {"HOME": str(self.temp_path / "home")}):
            with patch.object(stop, "collect_codex_turn_changes", return_value=changes), patch.object(
                stop, "notify_local_production", side_effect=lambda runtime, root, sha: notified.append(root)
            ):
                output = stop.process_codex_repositories(
                    str(first), {"session_id": "thread", "hook_event_name": "Stop"}
                )
        self.assertIsNone(output)
        for repo, remote, name in ((first, first_remote, "backend"), (second, second_remote, "frontend")):
            self.assertEqual(run_command(["git", "-C", str(repo), "status", "--porcelain"]).stdout, "")
            self.assertEqual(run_command(["git", "-C", str(remote), "show", f"HEAD:{name}.txt"]).stdout, name + "\n")
        self.assertEqual(set(notified), {str(first.resolve()), str(second.resolve())})
        self.assertIn("unrelated.txt", run_command(["git", "-C", str(untouched), "status", "--porcelain"]).stdout)

    def test_shell_cwd_failure_preflights_all_repos_before_any_commit(self) -> None:
        first, _ = self.make_published_repo("first")
        second, _ = self.make_published_repo("second")
        (first / "first.txt").write_text("first\n", encoding="utf-8")
        write_executable(second / "scripts/check-fast.sh", "#!/bin/sh\necho shell-repo-check-failed >&2\nexit 1\n")
        before = {str(repo): run_command(["git", "-C", str(repo), "rev-parse", "HEAD"]).stdout for repo in (first, second)}
        changes = self.changes([])
        changes.shell_paths = (str(second),)
        with patch.dict(os.environ, {"HOME": str(self.temp_path / "home")}):
            with patch.object(stop, "collect_codex_turn_changes", return_value=changes):
                output = stop.process_codex_repositories(
                    str(first), {"session_id": "thread", "hook_event_name": "Stop"}
                )
            pending = stop.load_codex_transaction("thread")
        self.assertEqual(output["decision"], "block")
        self.assertIn("shell-repo-check-failed", output["reason"])
        self.assertEqual(set(pending), {str(first.resolve()), str(second.resolve())})
        for repo in (first, second):
            self.assertEqual(run_command(["git", "-C", str(repo), "rev-parse", "HEAD"]).stdout, before[str(repo)])

    def test_shell_discovery_limit_does_not_silently_finalize_primary_only(self) -> None:
        first, _ = self.make_published_repo("first")
        (first / "first.txt").write_text("first\n", encoding="utf-8")
        with patch.dict(os.environ, {"HOME": str(self.temp_path / "home")}):
            with patch.object(stop, "collect_codex_turn_changes", side_effect=codex_turn_changes.CodexShellDiscoveryError("limit")):
                output = stop.process_codex_repositories(
                    str(first), {"session_id": "thread", "hook_event_name": "Stop"}
                )
        self.assertEqual(output["decision"], "block")
        self.assertIn("shell repository discovery is incomplete", output["reason"])
        self.assertIn("first.txt", run_command(["git", "-C", str(first), "status", "--porcelain"]).stdout)

    def test_commits_and_pushes_two_attributed_repositories(self) -> None:
        first, first_remote = self.make_published_repo("first")
        second, second_remote = self.make_published_repo("second")
        first_file = first / "first.txt"
        second_file = second / "second.txt"
        first_file.write_text("first\n", encoding="utf-8")
        second_file.write_text("second\n", encoding="utf-8")

        home = self.temp_path / "home"
        with patch.dict(os.environ, {"HOME": str(home)}):
            with patch.object(
                stop,
                "collect_codex_turn_changes",
                return_value=self.changes([first_file, second_file]),
            ):
                output = stop.process_codex_repositories(
                    str(first),
                    {"session_id": "thread", "hook_event_name": "Stop"},
                )

        self.assertIsNone(output)
        self.assertEqual(run_command(["git", "-C", str(first), "status", "--porcelain"]).stdout, "")
        self.assertEqual(run_command(["git", "-C", str(second), "status", "--porcelain"]).stdout, "")
        self.assertEqual(
            run_command(["git", "-C", str(first_remote), "show", "HEAD:first.txt"]).stdout,
            "first\n",
        )
        self.assertEqual(
            run_command(["git", "-C", str(second_remote), "show", "HEAD:second.txt"]).stdout,
            "second\n",
        )
        self.assertFalse(stop.codex_transaction_path("thread").exists())

    def test_parent_stop_adopts_registered_descendant_paths(self) -> None:
        parent, parent_remote = self.make_published_repo("parent")
        child, child_remote = self.make_published_repo("child")
        parent_file = parent / "parent.txt"
        child_file = child / "child.txt"
        parent_file.write_text("parent\n", encoding="utf-8")
        child_file.write_text("child\n", encoding="utf-8")
        changes = self.changes([parent_file])
        changes.descendant_thread_ids = ("child-thread",)

        with patch.dict(os.environ, {"HOME": str(self.temp_path / "home")}):
            stop.register_codex_transaction_paths("child-thread", [child_file])
            with patch.object(
                stop,
                "collect_codex_turn_changes",
                return_value=changes,
            ):
                output = stop.process_codex_repositories(
                    str(parent),
                    {"session_id": "parent-thread", "hook_event_name": "Stop"},
                )

            self.assertFalse(stop.codex_transaction_path("child-thread").exists())
            self.assertFalse(stop.codex_transaction_path("parent-thread").exists())

        self.assertIsNone(output)
        self.assertEqual(
            run_command(["git", "-C", str(parent_remote), "show", "HEAD:parent.txt"]).stdout,
            "parent\n",
        )
        self.assertEqual(
            run_command(["git", "-C", str(child_remote), "show", "HEAD:child.txt"]).stdout,
            "child\n",
        )

    def test_consolidates_unattributed_pre_staged_file(self) -> None:
        repo, remote = self.make_published_repo("repo")
        attributed = repo / "attributed.txt"
        unrelated = repo / "unrelated.txt"
        attributed.write_text("mine\n", encoding="utf-8")
        unrelated.write_text("other\n", encoding="utf-8")
        run_command(["git", "-C", str(repo), "add", "unrelated.txt"])

        with patch.dict(os.environ, {"HOME": str(self.temp_path / "home")}):
            with patch.object(
                stop,
                "collect_codex_turn_changes",
                return_value=self.changes([attributed]),
            ):
                output = stop.process_codex_repositories(
                    str(repo),
                    {"session_id": "thread", "hook_event_name": "Stop"},
                )

        self.assertIsNone(output)
        self.assertEqual(
            run_command(["git", "-C", str(repo), "rev-list", "--count", "HEAD"]).stdout.strip(),
            "2",
        )
        self.assertEqual(
            run_command(["git", "-C", str(remote), "show", "HEAD:unrelated.txt"]).stdout,
            "other\n",
        )

    def test_preflights_every_repo_before_any_commit(self) -> None:
        first, _first_remote = self.make_published_repo("first")
        second, _second_remote = self.make_published_repo("second")
        first_file = first / "first.txt"
        second_file = second / "second.txt"
        first_file.write_text("first\n", encoding="utf-8")
        second_file.write_text("second\n", encoding="utf-8")
        write_executable(
            first / "scripts/check-fast.sh",
            "#!/usr/bin/env bash\nprintf 'formatted\\n' > first.txt\nexit 1\n",
        )
        write_executable(
            second / "scripts/check-fast.sh",
            "#!/usr/bin/env bash\nprintf 'second repo failed\\n' >&2\nexit 1\n",
        )
        before = {
            str(repo): run_command(["git", "-C", str(repo), "rev-parse", "HEAD"]).stdout.strip()
            for repo in (first, second)
        }

        with patch.dict(os.environ, {"HOME": str(self.temp_path / "home")}):
            with patch.object(
                stop,
                "collect_codex_turn_changes",
                return_value=self.changes([first_file, second_file, second / "scripts/check-fast.sh"]),
            ), patch.object(stop, "preflight_repo_check", wraps=stop.preflight_repo_check) as checks:
                output = stop.process_codex_repositories(
                    str(first),
                    {"session_id": "thread", "hook_event_name": "Stop"},
                )
            pending = stop.load_codex_transaction("thread")

        self.assertEqual(output["decision"], "block")
        self.assertIn("second repo failed", output["reason"])
        self.assertEqual(checks.call_count, 2)  # Do not retry the unchanged failure.
        for repo in (first, second):
            self.assertEqual(
                run_command(["git", "-C", str(repo), "rev-parse", "HEAD"]).stdout.strip(),
                before[str(repo)],
            )
        self.assertEqual(set(pending), {str(first.resolve()), str(second.resolve())})

    def test_autofix_failure_is_rechecked_and_published_without_feedback(self) -> None:
        self.assert_autofix_failure_recovers(stage_fix=False)

    def test_self_staging_autofix_is_rechecked_before_publication(self) -> None:
        self.assert_autofix_failure_recovers(stage_fix=True)

    def assert_autofix_failure_recovers(self, *, stage_fix: bool) -> None:
        repo, remote = self.make_published_repo("repo")
        path = repo / "formatted.txt"
        path.write_text("unformatted\n", encoding="utf-8")
        stage_command = "  git add -- formatted.txt\n" if stage_fix else ""
        write_executable(
            repo / "scripts/check-fast.sh",
            "#!/usr/bin/env bash\n"
            "if [[ $(cat formatted.txt) != formatted ]]; then\n"
            "  printf 'formatted\\n' > formatted.txt\n"
            f"{stage_command}"
            "  printf 'formatter changed files; rerun\\n' >&2\n"
            "  exit 1\n"
            "fi\n"
            "[[ $(git show :formatted.txt) == formatted ]]\n",
        )
        with (
            patch.object(stop, "collect_codex_turn_changes", return_value=self.changes([path])),
            patch.object(stop, "preflight_repo_check", wraps=stop.preflight_repo_check) as checks,
        ):
            output = stop.process_codex_repositories(
                str(repo), {"session_id": "thread", "hook_event_name": "Stop"}
            )

        self.assertIsNone(output)
        self.assertEqual(checks.call_count, 2)
        self.assertEqual(
            run_command(["git", "-C", str(remote), "show", "HEAD:formatted.txt"]).stdout,
            "formatted\n",
        )
        self.assertEqual(run_command(["git", "-C", str(repo), "status", "--porcelain"]).stdout, "")
        self.assertEqual(stop.load_codex_transaction("thread"), {})

    def test_autofix_does_not_hide_remaining_check_failure(self) -> None:
        first, first_remote = self.make_published_repo("first")
        second, second_remote = self.make_published_repo("second")
        (first / "formatted.txt").write_text("unformatted\n", encoding="utf-8")
        (second / "ready.txt").write_text("ready\n", encoding="utf-8")
        write_executable(
            first / "scripts/check-fast.sh",
            "#!/usr/bin/env bash\nprintf 'formatted\\n' > formatted.txt\n"
            "printf 'type error still remains\\n' >&2\nexit 1\n",
        )
        before = {
            str(root): run_command(["git", "-C", str(root), "rev-parse", "HEAD"]).stdout
            for root in (first, first_remote, second, second_remote)
        }
        with (
            patch.object(stop, "collect_codex_turn_changes", return_value=self.changes([
                first / "formatted.txt", second / "ready.txt",
            ])),
            patch.object(stop, "preflight_repo_check", wraps=stop.preflight_repo_check) as checks,
        ):
            output = stop.process_codex_repositories(
                str(first), {"session_id": "thread", "hook_event_name": "Stop"}
            )

        self.assertEqual(output["decision"], "block")
        self.assertIn("type error still remains", output["reason"])
        self.assertEqual(checks.call_count, 4)
        for root in (first, first_remote, second, second_remote):
            self.assertEqual(
                run_command(["git", "-C", str(root), "rev-parse", "HEAD"]).stdout,
                before[str(root)],
            )
        self.assertEqual(
            run_command(["git", "-C", str(first), "show", ":formatted.txt"]).stdout,
            "formatted\n",
        )

    def test_repeated_mutating_failure_stops_at_consolidation_limit(self) -> None:
        repo, remote = self.make_published_repo("repo")
        path = repo / "unstable.txt"
        path.write_text("initial\n", encoding="utf-8")
        write_executable(
            repo / "scripts/check-fast.sh",
            "#!/usr/bin/env bash\nprintf 'another fix\\n' >> unstable.txt\n"
            "printf 'formatter did not settle\\n' >&2\nexit 1\n",
        )
        before = run_command(["git", "-C", str(remote), "rev-parse", "HEAD"]).stdout
        with (
            patch.object(stop, "collect_codex_turn_changes", return_value=self.changes([path])),
            patch.object(stop, "preflight_repo_check", wraps=stop.preflight_repo_check) as checks,
        ):
            output = stop.process_codex_repositories(
                str(repo), {"session_id": "thread", "hook_event_name": "Stop"}
            )

        self.assertEqual(output["decision"], "block")
        self.assertIn("formatter did not settle", output["reason"])
        self.assertIn("automatic restage/recheck passes", output["reason"])
        self.assertEqual(checks.call_count, stop.MAX_CONSOLIDATION_PASSES)
        for root in (repo, remote):
            self.assertEqual(
                run_command(["git", "-C", str(root), "rev-parse", "HEAD"]).stdout, before
            )

    def test_stale_competitor_transaction_does_not_block_consolidation(self) -> None:
        repo, remote = self.make_published_repo("repo")
        path = repo / "shared.txt"
        path.write_text("shared\n", encoding="utf-8")
        other_state = {
            str(repo.resolve()): stop.RepoFinalization(
                root=str(repo.resolve()),
                paths={"shared.txt"},
            )
        }

        with patch.dict(os.environ, {"HOME": str(self.temp_path / "home")}):
            stop.save_codex_transaction("other-thread", other_state)
            with patch.object(
                stop,
                "collect_codex_turn_changes",
                return_value=self.changes([path]),
            ):
                output = stop.process_codex_repositories(
                    str(repo),
                    {"session_id": "thread", "hook_event_name": "Stop"},
                )

        self.assertIsNone(output)
        self.assertEqual(
            run_command(["git", "-C", str(remote), "show", "HEAD:shared.txt"]).stdout,
            "shared\n",
        )

    def test_subagent_stop_defers_to_parent_turn(self) -> None:
        repo, _remote = self.make_published_repo("repo")
        path = repo / "child.txt"
        path.write_text("child\n", encoding="utf-8")
        changes = self.changes([path])
        changes.parent_thread_id = "parent-thread"

        with patch.dict(os.environ, {"HOME": str(self.temp_path / "home")}):
            with patch.object(stop, "collect_codex_turn_changes", return_value=changes):
                output = stop.process_codex_repositories(
                    str(repo),
                    {"session_id": "child-thread", "hook_event_name": "Stop"},
                )

        self.assertIsNone(output)
        self.assertIn(
            "child.txt",
            run_command(["git", "-C", str(repo), "status", "--porcelain"]).stdout,
        )

    def test_clean_attributed_repo_skips_fast_checks(self) -> None:
        repo, _remote = self.make_published_repo("repo")
        tracked_path = repo / "README.md"

        with patch.dict(os.environ, {"HOME": str(self.temp_path / "home")}):
            with patch.object(
                stop,
                "collect_codex_turn_changes",
                return_value=self.changes([tracked_path]),
            ):
                with patch.object(
                    stop,
                    "preflight_repo_check",
                    side_effect=AssertionError("clean repo should not run checks"),
                ):
                    output = stop.process_codex_repositories(
                        str(repo),
                        {"session_id": "thread", "hook_event_name": "Stop"},
                    )
            pending = stop.load_codex_transaction("thread")

        self.assertIsNone(output)
        self.assertEqual(pending, {})

    def test_stale_missing_untracked_attribution_does_not_block_staging(self) -> None:
        repo, remote = self.make_published_repo("repo")
        changed = repo / "changed.txt"
        stale = repo / "tmp/deleted-before-stop.txt"
        changed.write_text("changed\n", encoding="utf-8")

        with patch.dict(os.environ, {"HOME": str(self.temp_path / "home")}):
            with patch.object(
                stop,
                "collect_codex_turn_changes",
                return_value=self.changes([changed, stale]),
            ):
                output = stop.process_codex_repositories(
                    str(repo),
                    {"session_id": "thread", "hook_event_name": "Stop"},
                )

        self.assertIsNone(output)
        self.assertEqual(
            run_command(["git", "-C", str(remote), "show", "HEAD:changed.txt"]).stdout,
            "changed\n",
        )

    def test_existing_ignored_untracked_attribution_does_not_block_staging(self) -> None:
        repo, remote = self.make_published_repo("repo")
        ignore_file = repo / ".gitignore"
        ignore_file.write_text("tmp/\n", encoding="utf-8")
        run_command(["git", "-C", str(repo), "add", ".gitignore"])
        run_command(["git", "-C", str(repo), "commit", "-m", "ignore temp files"])
        run_command(["git", "-C", str(repo), "push", "origin", "HEAD"])
        changed = repo / "changed.txt"
        ignored = repo / "tmp/ignored.txt"
        ignored.parent.mkdir()
        changed.write_text("changed\n", encoding="utf-8")
        ignored.write_text("ignored\n", encoding="utf-8")

        with patch.dict(os.environ, {"HOME": str(self.temp_path / "home")}):
            with patch.object(
                stop,
                "collect_codex_turn_changes",
                return_value=self.changes([changed, ignored]),
            ):
                output = stop.process_codex_repositories(
                    str(repo),
                    {"session_id": "thread", "hook_event_name": "Stop"},
                )

        self.assertIsNone(output)
        self.assertEqual(
            run_command(["git", "-C", str(remote), "show", "HEAD:changed.txt"]).stdout,
            "changed\n",
        )
        self.assertNotEqual(
            run_command(
                ["git", "-C", str(remote), "cat-file", "-e", "HEAD:tmp/ignored.txt"],
                check=False,
            ).returncode,
            0,
        )

    def test_stale_attribution_filter_preserves_tracked_deletion(self) -> None:
        repo, remote = self.make_published_repo("repo")
        deleted = repo / "delete-me.txt"
        stale = repo / "tmp/deleted-before-stop.txt"
        deleted.write_text("delete me\n", encoding="utf-8")
        run_command(["git", "-C", str(repo), "add", "delete-me.txt"])
        run_command(["git", "-C", str(repo), "commit", "-m", "seed deletion"])
        run_command(["git", "-C", str(repo), "push", "origin", "HEAD"])
        deleted.unlink()

        with patch.dict(os.environ, {"HOME": str(self.temp_path / "home")}):
            with patch.object(
                stop,
                "collect_codex_turn_changes",
                return_value=self.changes([deleted, stale]),
            ):
                output = stop.process_codex_repositories(
                    str(repo),
                    {"session_id": "thread", "hook_event_name": "Stop"},
                )

        self.assertIsNone(output)
        self.assertNotEqual(
            run_command(
                ["git", "-C", str(remote), "cat-file", "-e", "HEAD:delete-me.txt"],
                check=False,
            ).returncode,
            0,
        )

    def test_retries_a_committed_repository_after_partial_push_failure(self) -> None:
        first, _first_remote = self.make_published_repo("first")
        second, _second_remote = self.make_published_repo("second")
        first_file = first / "first.txt"
        second_file = second / "second.txt"
        first_file.write_text("first\n", encoding="utf-8")
        second_file.write_text("second\n", encoding="utf-8")
        success = SimpleNamespace(returncode=0, stdout="", stderr="")
        failure = SimpleNamespace(returncode=1, stdout="", stderr="temporary failure")
        push_results = [
            (success, ["git", "push", "origin", "HEAD"], "git push failed"),
            (failure, ["git", "push", "origin", "HEAD"], "git push failed"),
            (success, ["git", "push", "origin", "HEAD"], "git push failed"),
            (success, ["git", "push", "origin", "HEAD"], "git push failed"),
        ]
        payload = {"session_id": "thread", "hook_event_name": "Stop"}

        with patch.dict(os.environ, {"HOME": str(self.temp_path / "home")}):
            with patch.object(
                stop,
                "collect_codex_turn_changes",
                return_value=self.changes([first_file, second_file]),
            ):
                with patch.object(stop, "push_committed_repo", side_effect=push_results):
                    first_output = stop.process_codex_repositories(str(first), payload)
                    state_after_failure = stop.load_codex_transaction("thread")
                    with patch.object(
                        stop,
                        "collect_codex_turn_changes",
                        return_value=self.changes([]),
                    ):
                        second_output = stop.process_codex_repositories(str(first), payload)

            final_state = stop.load_codex_transaction("thread")

        self.assertEqual(first_output["decision"], "block")
        self.assertEqual(len(state_after_failure), 1)
        remaining = next(iter(state_after_failure.values()))
        self.assertEqual(remaining.phase, "committed")
        self.assertIsNone(second_output)
        self.assertEqual(final_state, {})

    def test_same_path_fix_after_partial_push_is_committed_before_retry(self) -> None:
        first, _first_remote = self.make_published_repo("first")
        second, _second_remote = self.make_published_repo("second")
        first_file = first / "first.txt"
        second_file = second / "second.txt"
        first_file.write_text("first\n", encoding="utf-8")
        second_file.write_text("second\n", encoding="utf-8")
        success = SimpleNamespace(returncode=0, stdout="", stderr="")
        failure = SimpleNamespace(returncode=1, stdout="", stderr="temporary failure")
        payload = {"session_id": "thread", "hook_event_name": "Stop"}

        with patch.dict(os.environ, {"HOME": str(self.temp_path / "home")}):
            with patch.object(
                stop,
                "collect_codex_turn_changes",
                return_value=self.changes([first_file, second_file]),
            ):
                with patch.object(
                    stop,
                    "push_committed_repo",
                    side_effect=[
                        (success, ["git", "push", "origin", "HEAD"], "git push failed"),
                        (failure, ["git", "push", "origin", "HEAD"], "git push failed"),
                    ],
                ):
                    first_output = stop.process_codex_repositories(str(first), payload)

                previous_head = run_command(
                    ["git", "-C", str(second), "rev-parse", "HEAD"]
                ).stdout.strip()
                second_file.write_text("fixed\n", encoding="utf-8")
                with patch.object(
                    stop,
                    "collect_codex_turn_changes",
                    return_value=self.changes([second_file]),
                ):
                    with patch.object(
                        stop,
                        "push_committed_repo",
                        return_value=(
                            success,
                            ["git", "push", "origin", "HEAD"],
                            "git push failed",
                        ),
                    ):
                        second_output = stop.process_codex_repositories(str(second), payload)
                final_state = stop.load_codex_transaction("thread")

        self.assertEqual(first_output["decision"], "block")
        self.assertIsNone(second_output)
        self.assertNotEqual(
            run_command(["git", "-C", str(second), "rev-parse", "HEAD"]).stdout.strip(),
            previous_head,
        )
        self.assertEqual(
            run_command(["git", "-C", str(second), "show", "HEAD:second.txt"]).stdout,
            "fixed\n",
        )
        self.assertEqual(final_state, {})

    def test_clean_rediscovered_path_preserves_pending_push(self) -> None:
        repo, remote = self.make_published_repo("repo")
        path = repo / "pending.txt"
        path.write_text("pending\n", encoding="utf-8")
        run_command(["git", "-C", str(repo), "add", "pending.txt"])
        run_command(["git", "-C", str(repo), "commit", "-m", "pending"])
        pending_commit = run_command(["git", "-C", str(repo), "rev-parse", "HEAD"]).stdout.strip()
        state = {
            str(repo.resolve()): stop.RepoFinalization(
                root=str(repo.resolve()),
                paths={"pending.txt"},
                phase="committed",
                commit=pending_commit,
            )
        }

        with patch.dict(os.environ, {"HOME": str(self.temp_path / "home")}):
            stop.save_codex_transaction("thread", state)
            with patch.object(
                stop,
                "collect_codex_turn_changes",
                return_value=self.changes([path]),
            ):
                output = stop.process_codex_repositories(
                    str(repo),
                    {"session_id": "thread", "hook_event_name": "Stop"},
                )
            final_state = stop.load_codex_transaction("thread")

        self.assertIsNone(output)
        self.assertEqual(final_state, {})
        self.assertEqual(
            run_command(["git", "-C", str(remote), "show", "HEAD:pending.txt"]).stdout,
            "pending\n",
        )

    def test_pushes_precommitted_primary_repo_without_attributed_files(self) -> None:
        repo, remote = self.make_published_repo("repo")
        path = repo / "already-committed.txt"
        path.write_text("committed\n", encoding="utf-8")
        run_command(["git", "-C", str(repo), "add", "already-committed.txt"])
        run_command(["git", "-C", str(repo), "commit", "-m", "already committed"])

        with patch.dict(os.environ, {"HOME": str(self.temp_path / "home")}):
            with patch.object(
                stop,
                "collect_codex_turn_changes",
                return_value=self.changes([]),
            ):
                output = stop.process_codex_repositories(
                    str(repo),
                    {"session_id": "thread", "hook_event_name": "Stop"},
                )

        self.assertIsNone(output)
        self.assertEqual(
            run_command(
                ["git", "-C", str(remote), "show", "HEAD:already-committed.txt"]
            ).stdout,
            "committed\n",
        )

    def test_does_not_push_only_primary_when_attribution_is_unavailable(self) -> None:
        repo, remote = self.make_published_repo("repo")
        published = run_command(["git", "-C", str(remote), "rev-parse", "HEAD"]).stdout
        path = repo / "fallback-commit.txt"
        path.write_text("fallback\n", encoding="utf-8")
        run_command(["git", "-C", str(repo), "add", "fallback-commit.txt"])
        run_command(["git", "-C", str(repo), "commit", "-m", "fallback commit"])

        with patch.dict(os.environ, {"HOME": str(self.temp_path / "home")}):
            with patch.object(
                stop,
                "collect_codex_turn_changes",
                side_effect=stop.CodexTurnChangesError("app server unavailable"),
            ):
                output = stop.process_codex_repositories(
                    str(repo),
                    {"session_id": "thread", "hook_event_name": "Stop"},
                )

        self.assertEqual(output["decision"], "block")
        self.assertIn("repository discovery is incomplete", output["reason"])
        self.assertEqual(
            run_command(["git", "-C", str(remote), "rev-parse", "HEAD"]).stdout,
            published,
        )

    def test_preserves_pending_push_until_complete_discovery_recovers(self) -> None:
        repo, remote = self.make_published_repo("repo")
        sibling, sibling_remote = self.make_published_repo("sibling")
        (sibling / "frontend.txt").write_text("frontend\n", encoding="utf-8")
        published = run_command(["git", "-C", str(remote), "rev-parse", "HEAD"]).stdout
        path = repo / "pending-without-server.txt"
        path.write_text("pending\n", encoding="utf-8")
        run_command(["git", "-C", str(repo), "add", "pending-without-server.txt"])
        run_command(["git", "-C", str(repo), "commit", "-m", "pending without server"])
        commit = run_command(["git", "-C", str(repo), "rev-parse", "HEAD"]).stdout.strip()
        state = {
            str(repo.resolve()): stop.RepoFinalization(
                root=str(repo.resolve()),
                paths={"pending-without-server.txt"},
                phase="committed",
                commit=commit,
            )
        }

        with patch.dict(os.environ, {"HOME": str(self.temp_path / "home")}):
            stop.save_codex_transaction("thread", state)
            with patch.object(
                stop,
                "collect_codex_turn_changes",
                side_effect=stop.CodexTurnChangesError("app server unavailable"),
            ):
                output = stop.process_codex_repositories(
                    str(repo),
                    {"session_id": "thread", "hook_event_name": "Stop"},
                )
            preserved = stop.load_codex_transaction("thread")
            self.assertEqual(output["decision"], "block")
            self.assertEqual(set(preserved), set(state))
            self.assertEqual(preserved[str(repo.resolve())].commit, commit)
            self.assertEqual(preserved[str(repo.resolve())].phase, "committed")
            self.assertEqual(
                run_command(["git", "-C", str(remote), "rev-parse", "HEAD"]).stdout,
                published,
            )
            changes = self.changes([])
            changes.shell_paths = (str(sibling),)
            with patch.object(stop, "collect_codex_turn_changes", return_value=changes):
                recovered = stop.process_codex_repositories(
                    str(repo), {"session_id": "thread", "hook_event_name": "Stop"}
                )
            final_state = stop.load_codex_transaction("thread")

        self.assertIsNone(recovered)
        self.assertEqual(final_state, {})
        self.assertEqual(
            run_command(
                ["git", "-C", str(remote), "show", "HEAD:pending-without-server.txt"]
            ).stdout,
            "pending\n",
        )
        self.assertEqual(
            run_command(["git", "-C", str(sibling_remote), "show", "HEAD:frontend.txt"]).stdout,
            "frontend\n",
        )

    def test_missing_task_id_does_not_fall_back_to_single_repo(self) -> None:
        with patch.object(stop, "process_repo") as single_repo:
            output = stop.process_codex_repositories(str(self.temp_path), {"hook_event_name": "Stop"})
        single_repo.assert_not_called()
        self.assertEqual(output["decision"], "block")
        self.assertIn("missing session_id", output["reason"])

    def test_repeated_discovery_failure_reports_incomplete_without_retry_loop(self) -> None:
        with patch.dict(os.environ, {"HOME": str(self.temp_path / "home")}):
            with patch.object(stop, "collect_codex_turn_changes", side_effect=stop.CodexTurnChangesError("unavailable")), patch.object(stop, "process_repo") as single_repo:
                output = stop.process_codex_repositories(
                    str(self.temp_path), {"session_id": "thread", "stop_hook_active": True}
                )
        single_repo.assert_not_called()
        self.assertNotIn("decision", output)
        self.assertIn("repository discovery is incomplete", output["systemMessage"])
        self.assertIn("No repositories were finalized", output["systemMessage"])

    def test_pushes_rewritten_equivalent_head_from_pending_transaction(self) -> None:
        repo, remote = self.make_published_repo("repo")
        path = repo / "rebased.txt"
        path.write_text("preserved\n", encoding="utf-8")
        run_command(["git", "-C", str(repo), "add", "rebased.txt"])
        run_command(["git", "-C", str(repo), "commit", "-m", "pending commit"])
        old_commit = run_command(["git", "-C", str(repo), "rev-parse", "HEAD"]).stdout.strip()
        run_command(
            ["git", "-C", str(repo), "commit", "--amend", "--no-edit"],
            env={"GIT_COMMITTER_DATE": "2030-01-01T00:00:00Z"},
        )
        rewritten_commit = run_command(
            ["git", "-C", str(repo), "rev-parse", "HEAD"]
        ).stdout.strip()
        self.assertNotEqual(old_commit, rewritten_commit)
        state = {
            str(repo.resolve()): stop.RepoFinalization(
                root=str(repo.resolve()),
                paths={"rebased.txt"},
                phase="committed",
                commit=old_commit,
            )
        }

        with patch.dict(os.environ, {"HOME": str(self.temp_path / "home")}):
            stop.save_codex_transaction("thread", state)
            with patch.object(
                stop,
                "collect_codex_turn_changes",
                return_value=self.changes([]),
            ):
                output = stop.process_codex_repositories(
                    str(repo),
                    {"session_id": "thread", "hook_event_name": "Stop"},
                )

        self.assertIsNone(output)
        self.assertEqual(
            run_command(["git", "-C", str(remote), "show", "HEAD:rebased.txt"]).stdout,
            "preserved\n",
        )

    def test_restages_concurrent_changes_and_reruns_fast_check(self) -> None:
        repo, remote = self.make_published_repo("repo")
        attributed = repo / "attributed.txt"
        attributed.write_text("mine\n", encoding="utf-8")
        write_executable(
            repo / "scripts/check-fast.sh",
            "\n".join(
                [
                    "#!/usr/bin/env bash",
                    "set -euo pipefail",
                    "if [[ ! -f .git/concurrent-change-added ]]; then",
                    "  touch .git/concurrent-change-added",
                    "  printf 'concurrent\\n' > concurrent.txt",
                    "fi",
                    "",
                ]
            ),
        )

        with patch.dict(os.environ, {"HOME": str(self.temp_path / "home")}):
            with patch.object(
                stop,
                "collect_codex_turn_changes",
                return_value=self.changes([attributed, repo / "scripts/check-fast.sh"]),
            ):
                output = stop.process_codex_repositories(
                    str(repo),
                    {"session_id": "thread", "hook_event_name": "Stop"},
                )

        self.assertIsNone(output)
        self.assertEqual(
            run_command(["git", "-C", str(remote), "show", "HEAD:concurrent.txt"]).stdout,
            "concurrent\n",
        )

    def test_reruns_fast_check_when_staged_content_changes_at_the_same_path(self) -> None:
        repo, remote = self.make_published_repo("repo")
        same_path = repo / "same-path.txt"
        same_path.write_text("before validation\n", encoding="utf-8")
        check_script = repo / "scripts/check-fast.sh"
        write_executable(
            check_script,
            "\n".join(
                [
                    "#!/usr/bin/env bash",
                    "set -euo pipefail",
                    "count=0",
                    "if [[ -f .git/check-count ]]; then count=$(<.git/check-count); fi",
                    "count=$((count + 1))",
                    "printf '%s\\n' \"$count\" > .git/check-count",
                    "if [[ \"$count\" -eq 1 ]]; then",
                    "  printf 'changed during validation\\n' > same-path.txt",
                    "  git add same-path.txt",
                    "fi",
                    "",
                ]
            ),
        )

        with patch.dict(os.environ, {"HOME": str(self.temp_path / "home")}):
            with patch.object(
                stop,
                "collect_codex_turn_changes",
                return_value=self.changes([same_path, check_script]),
            ):
                output = stop.process_codex_repositories(
                    str(repo),
                    {"session_id": "thread", "hook_event_name": "Stop"},
                )

        self.assertIsNone(output)
        self.assertEqual((repo / ".git/check-count").read_text(encoding="utf-8"), "2\n")
        self.assertEqual(
            run_command(["git", "-C", str(remote), "show", "HEAD:same-path.txt"]).stdout,
            "changed during validation\n",
        )

    def test_revalidates_rebased_commit_before_retrying_push(self) -> None:
        repo, remote = self.make_published_repo("repo")
        check_script = repo / "scripts/check-fast.sh"
        write_executable(
            check_script,
            "\n".join(
                [
                    "#!/usr/bin/env bash",
                    "set -euo pipefail",
                    "if [[ -f remote-break-marker.txt ]]; then",
                    "  printf 'rebased tree failed validation\\n' >&2",
                    "  exit 41",
                    "fi",
                    "",
                ]
            ),
        )
        run_command(["git", "-C", str(repo), "add", "scripts/check-fast.sh"])
        run_command(["git", "-C", str(repo), "commit", "-m", "add fast check"])
        run_command(["git", "-C", str(repo), "push", "origin", "HEAD"])

        competitor = self.temp_path / "competitor"
        run_command(["git", "clone", "-q", str(remote), str(competitor)])
        run_command(["git", "-C", str(competitor), "config", "user.email", "tests@example.com"])
        run_command(["git", "-C", str(competitor), "config", "user.name", "Control Plane Tests"])
        (competitor / "remote-break-marker.txt").write_text("break local check\n", encoding="utf-8")
        run_command(["git", "-C", str(competitor), "add", "remote-break-marker.txt"])
        run_command(["git", "-C", str(competitor), "commit", "-m", "advance remote"])
        run_command(["git", "-C", str(competitor), "push", "origin", "HEAD"])

        local_change = repo / "local-change.txt"
        local_change.write_text("local\n", encoding="utf-8")
        with patch.dict(os.environ, {"HOME": str(self.temp_path / "home")}):
            with patch.object(
                stop,
                "collect_codex_turn_changes",
                return_value=self.changes([local_change]),
            ):
                output = stop.process_codex_repositories(
                    str(repo),
                    {"session_id": "thread", "hook_event_name": "Stop"},
                )

        self.assertIsNotNone(output)
        assert output is not None
        self.assertEqual(output["decision"], "block")
        self.assertIn("rebased tree failed validation", output["reason"])
        self.assertNotEqual(
            run_command(
                ["git", "-C", str(remote), "cat-file", "-e", "HEAD:local-change.txt"],
                check=False,
            ).returncode,
            0,
        )

    def test_consolidates_repo_changes_without_running_mutable_precommit_hook(self) -> None:
        repo, remote = self.make_published_repo("repo")
        attributed = repo / "attributed.txt"
        unrelated = repo / "unrelated.txt"
        attributed.write_text("mine\n", encoding="utf-8")
        unrelated.write_text("other\n", encoding="utf-8")
        hook_marker = repo / ".git/precommit-ran"
        write_executable(
            repo / ".git/hooks/pre-commit",
            f"#!/usr/bin/env bash\ntouch {hook_marker}\n",
        )

        with patch.dict(os.environ, {"HOME": str(self.temp_path / "home")}):
            with patch.object(
                stop,
                "collect_codex_turn_changes",
                return_value=self.changes([attributed]),
            ):
                output = stop.process_codex_repositories(
                    str(repo),
                    {"session_id": "thread", "hook_event_name": "Stop"},
                )

        self.assertIsNone(output)
        self.assertEqual(
            run_command(["git", "-C", str(remote), "show", "HEAD:attributed.txt"]).stdout,
            "mine\n",
        )
        self.assertEqual(
            run_command(["git", "-C", str(remote), "show", "HEAD:unrelated.txt"]).stdout,
            "other\n",
        )
        self.assertFalse(hook_marker.exists())

    def test_tracked_file_symlink_maps_to_its_repository_path(self) -> None:
        repo, _remote = self.make_published_repo("repo")
        outside = self.temp_path / "outside.txt"
        outside.write_text("outside\n", encoding="utf-8")
        link = repo / "linked.txt"
        link.symlink_to(outside)

        resolved = stop.attributed_repo_path(str(link))

        self.assertEqual(resolved, (str(repo.resolve()), "linked.txt"))

    def test_consolidates_rename_delete_and_literal_pathspec_names(self) -> None:
        repo, remote = self.make_published_repo("repo")
        renamed_source = repo / "rename-source.txt"
        deleted = repo / "delete-me.txt"
        renamed_source.write_text("renamed\n", encoding="utf-8")
        deleted.write_text("deleted\n", encoding="utf-8")
        run_command(
            ["git", "-C", str(repo), "add", "rename-source.txt", "delete-me.txt"]
        )
        run_command(["git", "-C", str(repo), "commit", "-m", "seed edge paths"])
        run_command(["git", "-C", str(repo), "push", "origin", "HEAD"])
        literal_name = repo / ":(literal)-renamed.txt"
        renamed_source.rename(literal_name)
        deleted.unlink()

        with patch.dict(os.environ, {"HOME": str(self.temp_path / "home")}):
            with patch.object(
                stop,
                "collect_codex_turn_changes",
                return_value=self.changes([literal_name, deleted]),
            ):
                output = stop.process_codex_repositories(
                    str(repo),
                    {"session_id": "thread", "hook_event_name": "Stop"},
                )

        self.assertIsNone(output)
        tree_paths = set(
            run_command(
                ["git", "-C", str(remote), "ls-tree", "--name-only", "HEAD"]
            ).stdout.splitlines()
        )
        self.assertIn(":(literal)-renamed.txt", tree_paths)
        self.assertNotIn("rename-source.txt", tree_paths)
        self.assertNotIn("delete-me.txt", tree_paths)

    def test_consolidates_already_staged_rename(self) -> None:
        repo, remote = self.make_published_repo("repo")
        old_bundle = repo / "bundle-old.js"
        new_bundle = repo / "bundle-new.js"
        stable_bundle = "export const stable = true;\n" * 20
        old_bundle.write_text(stable_bundle + "export const version = 'old';\n", encoding="utf-8")
        run_command(["git", "-C", str(repo), "add", old_bundle.name])
        run_command(["git", "-C", str(repo), "commit", "-m", "seed bundle"])
        run_command(["git", "-C", str(repo), "push", "origin", "HEAD"])

        old_bundle.rename(new_bundle)
        new_bundle.write_text(stable_bundle + "export const version = 'new';\n", encoding="utf-8")
        run_command(
            ["git", "-C", str(repo), "add", "-A", "--", old_bundle.name, new_bundle.name]
        )
        self.assertEqual(
            run_command(["git", "-C", str(repo), "status", "--short"]).stdout,
            "R  bundle-old.js -> bundle-new.js\n",
        )

        with patch.dict(os.environ, {"HOME": str(self.temp_path / "home")}):
            with patch.object(
                stop,
                "collect_codex_turn_changes",
                return_value=self.changes([old_bundle, new_bundle]),
            ):
                output = stop.process_codex_repositories(
                    str(repo),
                    {"session_id": "thread", "hook_event_name": "Stop"},
                )

        self.assertIsNone(output)
        tree_paths = set(
            run_command(
                ["git", "-C", str(remote), "ls-tree", "--name-only", "HEAD"]
            ).stdout.splitlines()
        )
        self.assertIn(new_bundle.name, tree_paths)
        self.assertNotIn(old_bundle.name, tree_paths)
        self.assertEqual(
            run_command(
                ["git", "-C", str(remote), "show", f"HEAD:{new_bundle.name}"]
            ).stdout,
            stable_bundle + "export const version = 'new';\n",
        )

    def test_repository_lock_serializes_competing_stop_processes(self) -> None:
        repo, _remote = self.make_published_repo("repo")
        home = self.temp_path / "home"
        holder = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "\n".join(
                    [
                        "import time",
                        "from hooks.scripts.stop import lock_codex_repositories",
                        f"with lock_codex_repositories([{str(repo.resolve())!r}]):",
                        "    print('locked', flush=True)",
                        "    time.sleep(0.6)",
                    ]
                ),
            ],
            cwd=str(REPO_ROOT),
            env={**os.environ, "HOME": str(home)},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            assert holder.stdout is not None
            self.assertEqual(holder.stdout.readline().strip(), "locked")
            started = time.monotonic()
            with patch.dict(os.environ, {"HOME": str(home)}):
                with stop.lock_codex_repositories([str(repo.resolve())]):
                    waited = time.monotonic() - started
            self.assertGreaterEqual(waited, 0.35)
        finally:
            holder.wait(timeout=5)
            if holder.stdout is not None:
                holder.stdout.close()
            if holder.stderr is not None:
                holder.stderr.close()
