from __future__ import annotations

import shlex
from unittest.mock import patch

from hooks.scripts import codex_shell_paths, codex_turn_changes
from tests.control_plane.support import TempDirTestCase


def execution(command: object, *, cwd: object = "/work/main", status: str = "completed", **extra):
    return {
        "type": "commandExecution",
        "status": status,
        "cwd": cwd,
        "command": command,
        **extra,
    }


class CodexShellPathsTests(TempDirTestCase):
    def paths(self, *items):
        return codex_shell_paths.command_repository_paths({"items": list(items)})

    def test_command_cwd_is_a_candidate_without_literal_paths(self) -> None:
        self.assertEqual(
            self.paths(execution("python edit.py", cwd="/work/sibling/src/..")),
            {"/work/sibling"},
        )

    def test_decodes_shell_wrapped_python_absolute_path_literal(self) -> None:
        script = "\n".join(
            [
                "python - <<'PY'",
                "from pathlib import Path",
                "root = Path('/work/frontend')",
                "(root / 'form.tsx').write_text('changed')",
                "PY",
            ]
        )
        self.assertEqual(
            self.paths(execution("/bin/zsh -lc " + shlex.quote(script))),
            {"/work/main", "/work/frontend"},
        )

    def test_resolves_quoted_relative_paths_without_space_prefix_candidates(self) -> None:
        self.assertEqual(
            self.paths(
                execution("python ../scripts/change.py '../app extra/src/form.tsx'")
            ),
            {"/work/main", "/work/scripts/change.py", "/work/app extra/src/form.tsx"},
        )

    def test_absolute_path_with_spaces_does_not_select_prefix_repository(self) -> None:
        self.assertEqual(
            self.paths(execution('python -c \'p = Path("/work/app extra")\'')),
            {"/work/main", "/work/app extra"},
        )

    def test_escaped_spaces_do_not_select_prefix_repository(self) -> None:
        for command in (r"python /work/app\ extra/change.py", r"python ../app\ extra/change.py"):
            with self.subTest(command=command):
                self.assertEqual(
                    self.paths(execution(command)),
                    {"/work/main", "/work/app extra/change.py"},
                )

    def test_expands_literal_home_path(self) -> None:
        self.assertEqual(
            self.paths(execution("python '~/GitHub/frontend/update.py'")),
            {"/work/main", str(self.temp_path / "home/GitHub/frontend/update.py")},
        )

    def test_completed_nonzero_and_failed_commands_can_have_partial_writes(self) -> None:
        for status in ("completed", "failed"):
            with self.subTest(status=status):
                self.assertEqual(
                    self.paths(
                        execution(
                            "python /work/frontend/update.py",
                            status=status,
                            exitCode=1,
                        )
                    ),
                    {"/work/main", "/work/frontend/update.py"},
                )

    def test_skips_commands_that_did_not_complete_execution(self) -> None:
        for status in ("declined", "inProgress", "pending", "", None):
            with self.subTest(status=status):
                self.assertEqual(
                    self.paths(execution("python /work/frontend/update.py", status=status)),
                    set(),
                )

    def test_never_inspects_output_messages_or_command_action_descriptions(self) -> None:
        self.assertEqual(
            self.paths(
                execution(
                    "true",
                    aggregatedOutput="/work/output-only",
                    commandActions=[{"type": "read", "path": "/work/action-only"}],
                ),
                {"type": "userMessage", "text": "/work/user-only"},
                {"type": "agentMessage", "text": "/work/assistant-only"},
                {"type": "fileChange", "changes": [{"path": "/work/patch-only"}]},
            ),
            {"/work/main"},
        )

    def test_does_not_execute_or_expand_shell_substitutions(self) -> None:
        marker = self.temp_path / "must-not-exist"
        self.assertEqual(
            self.paths(
                execution(f'echo "/work/$(touch {marker})" "/work/$REPO" "/work/`pwd`"')
            ),
            {"/work/main"},
        )
        self.assertFalse(marker.exists())

    def test_ignores_non_execution_items_and_invalid_working_directories(self) -> None:
        self.assertEqual(
            self.paths(
                None,
                "malformed",
                {},
                execution("python /work/frontend", cwd="../relative"),
                execution("python /work/frontend", cwd=None),
                execution("python /work/frontend", cwd=42),
            ),
            set(),
        )
        for items in (None, "malformed", {}):
            with self.subTest(items=items):
                self.assertEqual(
                    codex_shell_paths.command_repository_paths({"items": items}), set()
                )

    def test_missing_command_and_malformed_quotes_preserve_cwd_evidence(self) -> None:
        self.assertEqual(
            self.paths(execution(None), execution('python "unterminated')),
            {"/work/main"},
        )

    def test_rejects_single_command_text_limit(self) -> None:
        with patch.object(codex_shell_paths, "MAX_COMMAND_CHARS", 4):
            with self.assertRaisesRegex(ValueError, "command text limits"):
                self.paths(execution("12345"))

    def test_rejects_cumulative_turn_command_text_limit(self) -> None:
        with patch.object(codex_shell_paths, "MAX_TURN_COMMAND_CHARS", 8):
            with self.assertRaisesRegex(ValueError, "command text limits"):
                self.paths(execution("12345"), execution("67890"))

    def test_rejects_unique_path_limit(self) -> None:
        with patch.object(codex_shell_paths, "MAX_SHELL_PATHS", 2):
            with self.assertRaisesRegex(ValueError, "path limits"):
                self.paths(execution("python /work/first /work/second"))

    def test_missing_command_records_cannot_bypass_cwd_path_limit(self) -> None:
        with patch.object(codex_shell_paths, "MAX_SHELL_PATHS", 1):
            with self.assertRaisesRegex(ValueError, "path limits"):
                self.paths(execution(None, cwd="/one"), execution(None, cwd="/two"))


class ShellPathsCollectorTests(TempDirTestCase):
    def collect(self, threads, listed):
        def request(method, params):
            if method == "thread/read":
                return {"thread": threads[params["threadId"]]}
            if method == "thread/list":
                return {"data": listed, "nextCursor": None}
            self.fail(f"Unexpected App Server request: {method}")

        with patch.object(codex_turn_changes, "AppServerClient") as client_type:
            client_type.return_value.__enter__.return_value.request.side_effect = request
            return codex_turn_changes.collect_codex_turn_changes("parent")

    def test_current_parent_turn_and_recent_descendants_supply_shell_paths(self) -> None:
        threads = {
            "parent": {
                "id": "parent",
                "turns": [
                    {"id": "old", "startedAt": 90, "items": [execution("true", cwd="/old-parent")]},
                    {"id": "new", "startedAt": 100, "items": [execution("true", cwd="/parent")]},
                ],
            },
            "child": {
                "id": "child",
                "parentThreadId": "parent",
                "turns": [
                    {"id": "old", "startedAt": 99, "items": [execution("true", cwd="/old-child")]},
                    {"id": "new", "startedAt": 100, "items": [execution("true", cwd="/child")]},
                ],
            },
            "grandchild": {
                "id": "grandchild",
                "parentThreadId": "child",
                "turns": [
                    {"id": "new", "startedAt": 101, "items": [execution("true", cwd="/grandchild")]}
                ],
            },
        }
        result = self.collect(
            threads,
            [
                {"id": "child", "parentThreadId": "parent", "updatedAt": 100},
                {"id": "grandchild", "parentThreadId": "child", "updatedAt": 101},
                {"id": "stale", "parentThreadId": "parent", "updatedAt": 99},
                {"id": "unrelated", "updatedAt": 101},
            ],
        )
        self.assertEqual(set(result.shell_paths), {"/parent", "/child", "/grandchild"})
        self.assertEqual(result.touched_paths, ())
        self.assertEqual(result.descendant_thread_ids, ("child", "grandchild"))

    def test_command_limit_is_a_non_fallback_discovery_error(self) -> None:
        threads = {
            "parent": {
                "id": "parent",
                "turns": [{"id": "turn", "startedAt": 100, "items": [execution("12345")]}],
            }
        }
        with patch.object(codex_shell_paths, "MAX_COMMAND_CHARS", 4):
            with self.assertRaises(codex_turn_changes.CodexShellDiscoveryError):
                self.collect(threads, [])

    def test_combined_descendant_path_limit_is_enforced(self) -> None:
        threads = {
            "parent": {
                "id": "parent",
                "turns": [{"id": "turn", "startedAt": 100, "items": [execution("true", cwd="/parent")]}],
            },
            "child": {
                "id": "child",
                "turns": [{"id": "turn", "startedAt": 101, "items": [execution("true", cwd="/child")]}],
            },
        }
        with patch.object(codex_turn_changes, "MAX_SHELL_PATHS", 1):
            with self.assertRaisesRegex(codex_turn_changes.CodexShellDiscoveryError, "turn-tree path limits"):
                self.collect(threads, [{"id": "child", "parentThreadId": "parent", "updatedAt": 101}])
