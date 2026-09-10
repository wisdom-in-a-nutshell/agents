from __future__ import annotations

from unittest.mock import patch

from hooks.scripts import codex_turn_changes, stop
from tests.control_plane.support import TempDirTestCase, init_git_repo, run_command


def command_turn(turn_id: str, started_at: int, *working_directories: str):
    return {
        "id": turn_id,
        "startedAt": started_at,
        "items": [
            {
                "type": "commandExecution",
                "status": "completed",
                "cwd": directory,
                "command": "python edit.py",
            }
            for directory in working_directories
        ],
    }


class CodexDiscoveryRecoveryTests(TempDirTestCase):
    def make_published_repo(self, name: str):
        remote = init_git_repo(self.temp_path / f"{name}-remote.git")
        run_command(["git", "-C", str(remote), "config", "receive.denyCurrentBranch", "updateInstead"])
        repo = init_git_repo(self.temp_path / name, with_initial_commit=True)
        run_command(["git", "-C", str(repo), "remote", "add", "origin", str(remote)])
        run_command(["git", "-C", str(repo), "push", "-u", "origin", "main"])
        return repo, remote

    def activity(self, turns, *, child=None):
        def request(method, params):
            if method == "thread/read":
                if params["threadId"] == "thread":
                    return {"thread": {"id": "thread", "turns": turns}}
                if child is not None and params["threadId"] == "child":
                    return {"thread": {"id": "child", "parentThreadId": "thread", "turns": child}}
                self.fail(f"Unexpected thread: {params['threadId']}")
            if method == "thread/list":
                return {
                    "data": [] if child is None else [
                        {"id": "child", "parentThreadId": "thread", "updatedAt": 120}
                    ],
                    "nextCursor": None,
                }
            self.fail(f"Unexpected App Server request: {method}")

        return request

    def test_recovers_original_sibling_after_failed_read_and_empty_retry_turn(self) -> None:
        primary, primary_remote = self.make_published_repo("primary")
        sibling, sibling_remote = self.make_published_repo("sibling")
        old_repo, old_remote = self.make_published_repo("old")
        for repo in (primary, sibling, old_repo):
            (repo / "change.txt").write_text(f"{repo.name}\n", encoding="utf-8")
        published = {
            str(remote): run_command(["git", "-C", str(remote), "rev-parse", "HEAD"]).stdout
            for remote in (primary_remote, sibling_remote, old_remote)
        }
        payload = {"session_id": "thread", "hook_event_name": "Stop"}

        with patch.object(codex_turn_changes, "AppServerClient") as client_type:
            client = client_type.return_value.__enter__.return_value
            client.request.side_effect = codex_turn_changes.FeedbackTurnError("App Server unavailable")
            with patch.object(stop.time, "time", return_value=150):
                blocked = stop.process_codex_repositories(str(primary), payload)
            self.assertEqual(blocked["decision"], "block")
            self.assertEqual(stop.load_codex_discovery_checkpoint("thread"), 150)
            self.assertEqual(stop.load_codex_transaction("thread"), {})
            for remote in (primary_remote, sibling_remote, old_remote):
                self.assertEqual(
                    run_command(["git", "-C", str(remote), "rev-parse", "HEAD"]).stdout,
                    published[str(remote)],
                )

            client.request.side_effect = self.activity(
                [
                    command_turn("unrelated-old-turn", 90, str(old_repo)),
                    command_turn("failed-turn", 100, str(primary), str(sibling)),
                    command_turn("retry-turn", 160),
                ]
            )
            recovered = stop.process_codex_repositories(str(primary), payload)

        self.assertIsNone(recovered)
        for repo, remote in ((primary, primary_remote), (sibling, sibling_remote)):
            self.assertEqual(
                run_command(["git", "-C", str(remote), "show", "HEAD:change.txt"]).stdout,
                f"{repo.name}\n",
            )
            self.assertEqual(run_command(["git", "-C", str(repo), "status", "--porcelain"]).stdout, "")
        self.assertEqual(
            run_command(["git", "-C", str(old_remote), "rev-parse", "HEAD"]).stdout,
            published[str(old_remote)],
        )
        self.assertFalse(stop.codex_transaction_path("thread").exists())

    def test_explicit_shell_registration_preserves_discovery_checkpoint(self) -> None:
        repo, _remote = self.make_published_repo("registered")
        changed = repo / "shell-write.txt"
        changed.write_text("registered\n", encoding="utf-8")
        stop.save_codex_transaction("thread", {}, discovery_started_at=150)

        stop.register_codex_transaction_paths("thread", [changed])

        self.assertEqual(stop.load_codex_discovery_checkpoint("thread"), 150)
        pending = stop.load_codex_transaction("thread")
        self.assertEqual(set(pending), {str(repo.resolve())})
        self.assertEqual(pending[str(repo.resolve())].paths, {"shell-write.txt"})

    def test_missing_failed_turn_history_does_not_publish_primary(self) -> None:
        repo, remote = self.make_published_repo("primary")
        (repo / "change.txt").write_text("still pending\n", encoding="utf-8")
        published = run_command(["git", "-C", str(remote), "rev-parse", "HEAD"]).stdout
        stop.save_codex_transaction("thread", {}, discovery_started_at=150)

        with patch.object(codex_turn_changes, "AppServerClient") as client_type:
            client_type.return_value.__enter__.return_value.request.side_effect = self.activity(
                [command_turn("retry-only", 160)]
            )
            blocked = stop.process_codex_repositories(
                str(repo), {"session_id": "thread", "hook_event_name": "Stop"}
            )

        self.assertEqual(blocked["decision"], "block")
        self.assertIn("history does not cover", blocked["reason"])
        self.assertEqual(stop.load_codex_discovery_checkpoint("thread"), 150)
        self.assertEqual(
            run_command(["git", "-C", str(remote), "rev-parse", "HEAD"]).stdout,
            published,
        )

    def test_retry_replays_descendant_that_finished_before_retry_turn(self) -> None:
        with patch.object(codex_turn_changes, "AppServerClient") as client_type:
            client_type.return_value.__enter__.return_value.request.side_effect = self.activity(
                [command_turn("failed-turn", 100, "/work/primary"), command_turn("retry", 160)],
                child=[command_turn("child-edits", 120, "/work/sibling")],
            )
            recovered = codex_turn_changes.collect_codex_turn_changes("thread", replay_before=150)

        self.assertEqual(set(recovered.shell_paths), {"/work/primary", "/work/sibling"})
        self.assertEqual(recovered.descendant_thread_ids, ("child",))
        self.assertEqual(recovered.turn_started_at, 100)

    def test_same_second_retry_does_not_hide_original_turn_evidence(self) -> None:
        with patch.object(codex_turn_changes, "AppServerClient") as client_type:
            client_type.return_value.__enter__.return_value.request.side_effect = self.activity(
                [command_turn("failed-turn", 150, "/work/sibling"), command_turn("retry", 150)]
            )
            recovered = codex_turn_changes.collect_codex_turn_changes("thread", replay_before=150)

        self.assertEqual(recovered.shell_paths, ("/work/sibling",))
