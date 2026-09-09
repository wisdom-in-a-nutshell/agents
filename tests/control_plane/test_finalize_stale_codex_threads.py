from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests.control_plane.support import (
    REPO_ROOT,
    TempDirTestCase,
    init_git_repo,
    run_command,
    write_executable,
)


def load_stale_finalizer_module():  # noqa: ANN202
    path = REPO_ROOT / "codex/scripts/finalize-stale-codex-threads.py"
    spec = importlib.util.spec_from_file_location("finalize_stale_codex_threads", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_thread_finalizer_module():  # noqa: ANN202
    path = REPO_ROOT / "codex/scripts/finalize-codex-thread.py"
    spec = importlib.util.spec_from_file_location("finalize_codex_thread", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeAppServerClient:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.agent_text_parts: list[str] = []
        self.agent_completed_text: str | None = None

    def __enter__(self) -> "FakeAppServerClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def request(self, method: str, params: dict[str, Any] | None = None, **_kwargs: Any) -> dict[str, Any]:
        self.calls.append((method, params or {}))
        if not self.responses:
            raise AssertionError(f"unexpected request: {method}")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def wait_for_turn_completed(
        self,
        *,
        thread_id: str,
        turn_id: str | None,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        self.calls.append(
            (
                "wait_for_turn_completed",
                {
                    "thread_id": thread_id,
                    "turn_id": turn_id,
                    "timeout_seconds": timeout_seconds,
                },
            )
        )
        return {"params": {"threadId": thread_id, "turnId": turn_id, "status": "completed"}}


class FakeAppServerFactory:
    def __init__(self, responses_by_client: list[list[dict[str, Any]]]) -> None:
        self.responses_by_client = responses_by_client
        self.clients: list[FakeAppServerClient] = []

    def __call__(self, _timeout_seconds: float) -> FakeAppServerClient:
        if not self.responses_by_client:
            raise AssertionError("unexpected app-server client")
        client = FakeAppServerClient(self.responses_by_client.pop(0))
        self.clients.append(client)
        return client


class FinalizeStaleCodexThreadsTests(TempDirTestCase):
    def test_candidate_selection_uses_updated_at_cutoff(self) -> None:
        module = load_stale_finalizer_module()
        client = FakeAppServerClient(
            [
                {
                    "data": [
                        {
                            "id": "old-thread",
                            "name": "Old enough",
                            "cwd": str(self.temp_path),
                            "updatedAt": 100,
                            "source": "vscode",
                            "status": {"type": "notLoaded"},
                            "path": "/tmp/old.jsonl",
                        },
                        {
                            "id": "fresh-thread",
                            "name": "Too fresh",
                            "cwd": str(self.temp_path),
                            "updatedAt": 200,
                            "source": "vscode",
                            "status": {"type": "notLoaded"},
                            "path": "/tmp/fresh.jsonl",
                        },
                    ],
                    "nextCursor": "ignored-after-cutoff",
                }
            ]
        )

        candidates = module.list_candidates(
            client,
            repos=[str(self.temp_path)],
            cutoff_epoch=150,
            page_limit=100,
            source_kinds=None,
            use_state_db_only=False,
        )

        self.assertEqual([candidate.thread_id for candidate in candidates], ["old-thread"])
        self.assertEqual(client.calls[0][0], "thread/list")
        self.assertEqual(client.calls[0][1]["sortKey"], "updated_at")
        self.assertEqual(client.calls[0][1]["sortDirection"], "asc")
        self.assertNotIn("cwd", client.calls[0][1])

    def test_candidate_selection_includes_managed_linked_worktree_only(self) -> None:
        module = load_stale_finalizer_module()
        repo = init_git_repo(self.temp_path / "managed", with_initial_commit=True)
        worktree = self.temp_path / "codex-worktree"
        run_command(
            [
                "git",
                "-C",
                str(repo),
                "worktree",
                "add",
                "-q",
                "-b",
                "codex/test-worktree",
                str(worktree),
            ]
        )
        unmanaged = init_git_repo(self.temp_path / "unmanaged", with_initial_commit=True)
        client = FakeAppServerClient(
            [
                {
                    "data": [
                        {
                            "id": "managed-worktree-thread",
                            "name": "Managed worktree",
                            "cwd": str(worktree),
                            "updatedAt": 100,
                        },
                        {
                            "id": "unmanaged-thread",
                            "name": "Unmanaged",
                            "cwd": str(unmanaged),
                            "updatedAt": 110,
                        },
                    ],
                    "nextCursor": None,
                }
            ]
        )

        candidates = module.list_candidates(
            client,
            repos=[str(repo)],
            cutoff_epoch=150,
            page_limit=100,
            source_kinds=None,
            use_state_db_only=False,
        )

        self.assertEqual(
            [candidate.thread_id for candidate in candidates],
            ["managed-worktree-thread"],
        )

    def test_candidate_selection_matches_deleted_worktree_by_origin(self) -> None:
        module = load_stale_finalizer_module()
        repo = init_git_repo(self.temp_path / "managed", with_initial_commit=True)
        run_command(
            [
                "git",
                "-C",
                str(repo),
                "remote",
                "add",
                "origin",
                "git@github.com:example/managed.git",
            ]
        )
        client = FakeAppServerClient(
            [
                {
                    "data": [
                        {
                            "id": "deleted-worktree-thread",
                            "name": "Deleted worktree",
                            "cwd": str(self.temp_path / "missing-worktree"),
                            "updatedAt": 100,
                            "gitInfo": {
                                "originUrl": "https://github.com/example/managed.git",
                            },
                        }
                    ],
                    "nextCursor": None,
                }
            ]
        )

        candidates = module.list_candidates(
            client,
            repos=[str(repo)],
            cutoff_epoch=150,
            page_limit=100,
            source_kinds=None,
            use_state_db_only=False,
        )

        self.assertEqual(
            [candidate.thread_id for candidate in candidates],
            ["deleted-worktree-thread"],
        )

    def test_help_does_not_claim_loaded_thread_detection(self) -> None:
        result = run_command(
            [
                str(REPO_ROOT / "codex/scripts/finalize-stale-codex-threads.py"),
                "--help",
            ]
        )

        self.assertNotIn("skip-loaded", result.stdout)
        self.assertNotIn("thread/loaded", result.stdout)
        self.assertIn("--no-input", result.stdout)

    def test_stale_apply_calls_finalizer_command_with_thread_id_only(self) -> None:
        module = load_stale_finalizer_module()
        log_path = self.temp_path / "args.json"
        finalizer = write_executable(
            self.temp_path / "finalize-codex-thread.py",
            """#!/usr/bin/env python3
import json, pathlib, sys
path = pathlib.Path(__LOG_PATH__)
path.write_text(json.dumps(sys.argv[1:]))
print(json.dumps({
  "schema_version": "1.0",
  "command": "finalize-codex-thread",
  "status": "ok",
  "data": {"result": {"thread_id": sys.argv[sys.argv.index('--thread-id') + 1], "finalizer_status": "not_found", "archived": True}},
  "error": None,
  "meta": {}
}))
""".replace("__LOG_PATH__", repr(str(log_path))),
        )
        candidate = module.Candidate(
            thread_id="thread-123",
            name="Old",
            cwd="/repo/from/list",
            updated_at=100,
            source="vscode",
            status={"type": "notLoaded"},
            path=None,
        )

        result = module.run_thread_finalizer(
            command=finalizer,
            candidate=candidate,
            timeout_seconds=1,
            finalization_timeout_seconds=1,
        )

        argv = json.loads(log_path.read_text())
        self.assertIn("--thread-id", argv)
        self.assertEqual(argv[argv.index("--thread-id") + 1], "thread-123")
        self.assertNotIn("--cwd", argv)
        self.assertEqual(result["thread_id"], "thread-123")

    def test_processing_continues_after_one_finalizer_failure(self) -> None:
        module = load_stale_finalizer_module()
        candidates = [
            module.Candidate(
                thread_id="locked-thread",
                name="Locked",
                cwd="/repo",
                updated_at=100,
                source="vscode",
                status={"type": "notLoaded"},
                path=None,
            ),
            module.Candidate(
                thread_id="healthy-thread",
                name="Healthy",
                cwd="/repo",
                updated_at=110,
                source="vscode",
                status={"type": "notLoaded"},
                path=None,
            ),
        ]
        calls: list[str] = []

        def fake_runner(**kwargs: Any) -> dict[str, Any]:
            thread_id = kwargs["candidate"].thread_id
            calls.append(thread_id)
            if thread_id == "locked-thread":
                raise module.AppServerError("thread already has an active writer")
            return {
                "thread_id": thread_id,
                "finalizer_status": "not_found",
                "archived": True,
                "skipped_reason": None,
                "error": None,
            }

        result = module.process_candidates(
            candidates=candidates,
            apply=True,
            max_finalize=0,
            command=self.temp_path / "unused-finalizer",
            timeout_seconds=1,
            finalization_timeout_seconds=1,
            finalizer_runner=fake_runner,
        )

        self.assertEqual(calls, ["locked-thread", "healthy-thread"])
        self.assertEqual(result["finalized_count"], 1)
        self.assertEqual(result["skipped_count"], 1)
        self.assertEqual(result["failed_count"], 1)
        self.assertEqual(result["candidates"][0]["skipped_reason"], "finalizer_failed")
        self.assertEqual(result["candidates"][1]["finalized"], True)


class FinalizeCodexThreadTests(TempDirTestCase):
    def test_shared_connection_handles_notifications_and_closes(self) -> None:
        module = load_thread_finalizer_module()
        connection = Mock()
        connection.recv.side_effect = [
            json.dumps({"id": 1, "result": {}}),
            json.dumps({"method": "thread/archived", "params": {"threadId": "another-task"}}),
            json.dumps({"id": 2, "result": {"thread": {"id": "target"}}}),
        ]
        connect = Mock(return_value=connection)
        with patch.dict(sys.modules, {"websockets.sync.client": SimpleNamespace(unix_connect=connect)}), patch.dict(
            os.environ, {"CODEX_HOME": str(self.temp_path)}
        ), patch.object(module.subprocess, "Popen") as spawn:
            with module.AppServerClient(2) as client:
                self.assertEqual(module.thread_read(client, "target"), {"id": "target"})
        self.assertEqual(connect.call_args.args, (str(self.temp_path / "app-server-control/app-server-control.sock"),))
        self.assertIsNone(connect.call_args.kwargs["compression"])
        methods = [json.loads(call.args[0])["method"] for call in connection.send.call_args_list]
        self.assertEqual(methods, ["initialize", "initialized", "thread/read"])
        spawn.assert_not_called()
        connection.close.assert_called_once()

    def test_unavailable_daemon_does_not_spawn_server_or_run_repo_policy(self) -> None:
        module = load_thread_finalizer_module()
        connect = Mock(side_effect=FileNotFoundError("socket missing"))
        with patch.dict(sys.modules, {"websockets.sync.client": SimpleNamespace(unix_connect=connect)}), patch.object(
            module.subprocess, "Popen"
        ) as spawn, patch.object(module, "run_repo_finalizer") as finalizer:
            with self.assertRaisesRegex(module.AppServerError, "Cannot connect to shared app-server"):
                module.finalize_thread(
                    thread_id="target", reason="test", dry_run=False,
                    timeout_seconds=2, finalization_timeout_seconds=2,
                )
        spawn.assert_not_called()
        finalizer.assert_not_called()

    def test_active_task_is_skipped_before_policy(self) -> None:
        module = load_thread_finalizer_module()
        factory = FakeAppServerFactory([[{"thread": {
            "id": "target", "cwd": str(self.temp_path),
            "status": {"type": "active", "activeFlags": ["waitingOnApproval"]},
        }}]])
        with patch.object(module, "run_repo_finalizer") as finalizer:
            result = module.finalize_thread(
                thread_id="target", reason="test", dry_run=False,
                timeout_seconds=2, finalization_timeout_seconds=2, client_factory=factory,
            )
        self.assertEqual(result.skipped_reason, "active_thread")
        self.assertEqual(result.finalizer_status, "not_run")
        self.assertFalse(result.archived)
        finalizer.assert_not_called()

    def test_task_resumed_during_finalization_is_not_archived(self) -> None:
        module = load_thread_finalizer_module()
        write_executable(self.temp_path / "scripts/hooks/finalize_codex_thread.py", "#!/usr/bin/env python3\n")
        factory = FakeAppServerFactory([
            [{"thread": {"id": "target", "cwd": str(self.temp_path), "status": {"type": "idle"}}}, {"data": []}],
            [{"thread": {"id": "target", "status": {"type": "active"}}}],
        ])
        with patch.object(module, "run_repo_finalizer", return_value=(True, None, None)) as finalizer:
            result = module.finalize_thread(
                thread_id="target", reason="test", dry_run=False,
                timeout_seconds=2, finalization_timeout_seconds=2, client_factory=factory,
            )
        self.assertEqual(result.skipped_reason, "active_thread")
        self.assertEqual(result.finalizer_status, "completed")
        self.assertFalse(result.archived)
        finalizer.assert_called_once()
        self.assertEqual([call[0] for call in factory.clients[1].calls], ["thread/read"])

    def test_active_descendant_is_found_across_loaded_pages_and_unloaded_ancestors(self) -> None:
        module = load_thread_finalizer_module()
        root = {"id": "root", "status": {"type": "idle"}}
        def child(thread_id: str, parent_id: str, status: str) -> dict[str, Any]:
            return {"id": thread_id, "status": {"type": status},
                    "parentThreadId": parent_id, "source": "unknown"}
        client = FakeAppServerClient([
            {"data": ["unrelated"], "nextCursor": "next"},
            {"thread": {"id": "unrelated", "status": {"type": "active"}, "source": "cli"}},
            {"data": ["grandchild"]},
            {"thread": child("grandchild", "child", "active")},
            {"thread": child("child", "root", "notLoaded")},
        ])
        self.assertEqual(module.active_related_thread(client, root), "grandchild")
        self.assertEqual(client.calls[2], ("thread/loaded/list", {"limit": 100, "cursor": "next"}))

    def test_thread_finalizer_derives_repo_from_thread_read_and_archives(self) -> None:
        module = load_thread_finalizer_module()
        repo = self.temp_path / "repo"
        repo.mkdir()
        finalizer_payload = self.temp_path / "finalizer-payload.json"
        write_executable(
            repo / "scripts/hooks/finalize_codex_thread.py",
            f"""#!/usr/bin/env python3
import pathlib, sys
pathlib.Path({str(finalizer_payload)!r}).write_text(sys.stdin.read())
print("remember-session ok")
""",
        )
        client_factory = FakeAppServerFactory(
            [
                [
                    {
                        "thread": {
                            "id": "thread-123",
                            "cwd": str(repo),
                            "name": "Needs finalizing",
                            "updatedAt": 100,
                        }
                    },
                    {"data": []},
                ],
                [
                    {"thread": {"id": "thread-123", "status": {"type": "idle"}}},
                    {"data": []},
                    {},
                ],
            ]
        )

        result = module.finalize_thread(
            thread_id="thread-123",
            reason="test",
            dry_run=False,
            timeout_seconds=5,
            finalization_timeout_seconds=5,
            client_factory=client_factory,
        )

        self.assertTrue(result.archived)
        self.assertEqual(result.cwd, str(repo))
        self.assertEqual(result.repo_root, str(repo.resolve()))
        self.assertEqual(result.finalizer_status, "completed")
        self.assertIsNone(result.finalization_turn_id)
        self.assertIsNone(result.finalization_turn_status)
        self.assertEqual([call[0] for call in client_factory.clients[0].calls], ["thread/read", "thread/loaded/list"])
        self.assertEqual(
            [call[0] for call in client_factory.clients[1].calls],
            ["thread/read", "thread/loaded/list", "thread/archive"],
        )
        payload = json.loads(finalizer_payload.read_text())
        self.assertEqual(
            payload,
            {
                "schema_version": "1.0",
                "hook_event_name": "FinalizeCodexThread",
                "thread_id": "thread-123",
                "reason": "test",
            },
        )

    def test_thread_finalizer_dry_run_does_not_archive(self) -> None:
        module = load_thread_finalizer_module()
        repo = self.temp_path / "repo"
        repo.mkdir()
        client_factory = FakeAppServerFactory(
            [
                [
                    {
                        "thread": {
                            "id": "thread-123",
                            "cwd": str(repo),
                            "name": "Needs finalizing",
                            "updatedAt": 100,
                        }
                    },
                ],
            ]
        )

        result = module.finalize_thread(
            thread_id="thread-123",
            reason="test",
            dry_run=True,
            timeout_seconds=5,
            finalization_timeout_seconds=5,
            client_factory=client_factory,
        )

        self.assertFalse(result.archived)
        self.assertEqual(result.skipped_reason, "dry_run")
        self.assertEqual([call[0] for call in client_factory.clients[0].calls], ["thread/read"])
        self.assertEqual(len(client_factory.clients), 1)

    def test_thread_finalizer_treats_missing_archive_rollout_as_nonfatal_after_hook(self) -> None:
        module = load_thread_finalizer_module()
        repo = self.temp_path / "repo"
        repo.mkdir()
        write_executable(
            repo / "scripts/hooks/finalize_codex_thread.py",
            """#!/usr/bin/env python3
print("remember-session ok")
""",
        )
        client_factory = FakeAppServerFactory(
            [
                [
                    {
                        "thread": {
                            "id": "thread-123",
                            "cwd": str(repo),
                            "name": "Needs finalizing",
                            "updatedAt": 100,
                        }
                    },
                    {"data": []},
                ],
                [
                    {"thread": {"id": "thread-123", "status": {"type": "idle"}}},
                    {"data": []},
                    module.AppServerError("thread/archive failed: no rollout found for thread id thread-123"),
                ],
            ]
        )

        result = module.finalize_thread(
            thread_id="thread-123",
            reason="test",
            dry_run=False,
            timeout_seconds=5,
            finalization_timeout_seconds=5,
            client_factory=client_factory,
        )

        self.assertFalse(result.archived)
        self.assertIsNone(result.error)
        self.assertEqual(result.skipped_reason, "archive_unavailable")
        self.assertEqual(result.finalizer_status, "completed")
