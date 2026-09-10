from __future__ import annotations

import json
import importlib.util
import os
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from hooks.control_plane import (
    HookRegistryError,
    load_hooks_registry,
    render_copilot_hooks,
    render_codex_hooks,
)
from hooks.scripts.codex_turn_changes import CodexTurnChanges
from tests.control_plane.support import (
    REPO_ROOT,
    TempDirTestCase,
    default_mcp_registry,
    init_git_repo,
    make_control_plane_root,
    read_json,
    run_command,
    write_executable,
    write_json,
    write_text,
)


class HooksControlPlaneTests(TempDirTestCase):
    def load_stop_module(self):  # noqa: ANN201
        stop_path = REPO_ROOT / "hooks/scripts/stop.py"
        spec = importlib.util.spec_from_file_location("hooks_stop", stop_path)
        if spec is None or spec.loader is None:
            raise AssertionError(f"Failed to load Stop hook module from {stop_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_stop_publication_notifies_local_production_asynchronously(self) -> None:
        module = self.load_stop_module()
        notifier = self.temp_path / "GitHub/scripts/sync/local-production-notify.sh"
        write_executable(notifier, "#!/usr/bin/env bash\nexit 0\n")
        result = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps({"data": {"outcome": "queued"}}),
            stderr="",
        )
        messages: list[str] = []
        with (
            patch.object(module.Path, "home", return_value=self.temp_path),
            patch.object(module, "current_branch_name", return_value="main"),
            patch.object(module, "run", return_value=result) as run_mock,
            patch.object(module, "log", side_effect=lambda _runtime, message: messages.append(message)),
        ):
            module.notify_local_production("codex", "/tmp/example", "abc123")

        command = run_mock.call_args.args[0]
        self.assertEqual(command[0], str(notifier))
        self.assertIn("--apply", command)
        self.assertEqual(command[command.index("--repo") + 1], "/tmp/example")
        self.assertEqual(command[command.index("--sha") + 1], "abc123")
        self.assertIn("outcome=queued", messages[-1])

    def test_stop_publication_skips_non_main_branch(self) -> None:
        module = self.load_stop_module()
        messages: list[str] = []
        with (
            patch.object(module, "current_branch_name", return_value="codex/example"),
            patch.object(module, "run") as run_mock,
            patch.object(module, "log", side_effect=lambda _runtime, message: messages.append(message)),
        ):
            module.notify_local_production("codex", "/tmp/example", "abc123")

        run_mock.assert_not_called()
        self.assertIn("branch=codex/example", messages[-1])

    def test_stop_publication_notify_failure_does_not_fail_git_finalization(self) -> None:
        module = self.load_stop_module()
        notifier = self.temp_path / "GitHub/scripts/sync/local-production-notify.sh"
        write_executable(notifier, "#!/usr/bin/env bash\nexit 4\n")
        result = subprocess.CompletedProcess(args=[], returncode=4, stdout="", stderr="unavailable")
        messages: list[str] = []
        with (
            patch.object(module.Path, "home", return_value=self.temp_path),
            patch.object(module, "current_branch_name", return_value="main"),
            patch.object(module, "run", return_value=result),
            patch.object(module, "log", side_effect=lambda _runtime, message: messages.append(message)),
        ):
            self.assertIsNone(module.notify_local_production("codex", "/tmp/example", "abc123"))

        self.assertIn("warn local-production-notify", messages[-1])

    def test_registry_renders_codex_hooks(self) -> None:
        registry = load_hooks_registry(REPO_ROOT / "hooks/registry.json")

        global_codex_hooks = render_codex_hooks(registry)
        self.assertEqual(set(global_codex_hooks["hooks"].keys()), {"Stop"})
        self.assertEqual(
            global_codex_hooks["hooks"]["Stop"][0]["hooks"][0]["command"],
            'python3 "$HOME/GitHub/agents/hooks/scripts/stop.py" --runtime codex',
        )
        self.assertEqual(
            global_codex_hooks["hooks"]["Stop"][0]["hooks"][0]["timeout"],
            900,
        )

        codex_hooks = render_codex_hooks(registry, repo_name="adi")
        self.assertEqual(
            set(codex_hooks["hooks"].keys()),
            {"SessionStart"},
        )
        self.assertEqual(
            codex_hooks["hooks"]["SessionStart"][0]["matcher"],
            "startup|clear|compact",
        )
        self.assertEqual(
            set(render_codex_hooks(registry, repo_name="win")["hooks"].keys()),
            set(),
        )

    def test_registry_renders_copilot_user_hooks_with_repo_filters(self) -> None:
        registry = load_hooks_registry(REPO_ROOT / "hooks/registry.json")

        copilot_hooks = render_copilot_hooks(registry)

        self.assertEqual(
            set(copilot_hooks["hooks"].keys()),
            {"SessionStart", "UserPromptSubmit", "Stop"},
        )
        self.assertEqual(copilot_hooks["version"], 1)
        session_command = copilot_hooks["hooks"]["SessionStart"][0]["bash"]
        self.assertIn("--runtime copilot", session_command)
        self.assertIn("--no-input", session_command)
        self.assertIn("--repos adi,angie", session_command)
        self.assertEqual(copilot_hooks["hooks"]["SessionStart"][0]["timeoutSec"], 5)
        self.assertNotIn("matcher", copilot_hooks["hooks"]["SessionStart"][0])
        stop_command = copilot_hooks["hooks"]["Stop"][0]["bash"]
        self.assertIn("--runtime copilot", stop_command)
        self.assertNotIn("--repos", stop_command)

        filtered = render_copilot_hooks(
            registry,
            disabled_repo_names={"adi"},
        )
        filtered_session = filtered["hooks"]["SessionStart"][0]["bash"]
        self.assertIn("--repos angie", filtered_session)
        self.assertNotIn("--repos adi,angie", filtered_session)

    def test_registry_rejects_unsupported_runtime(self) -> None:
        registry_path = self.temp_path / "hooks/registry.json"
        write_json(
            registry_path,
            {
                "managed_hooks": [
                    {
                        "command": "python3 hook.py --runtime {runtime}",
                        "enabled": True,
                        "event": "Stop",
                        "id": "bad-runtime",
                        "runtimes": ["unknown"],
                        "scope": "global",
                        "timeout": 5,
                    }
                ],
                "version": 1,
            },
        )

        with self.assertRaises(HookRegistryError):
            load_hooks_registry(registry_path)

    def test_registry_rejects_matchers_for_events_without_matchers(self) -> None:
        registry_path = self.temp_path / "hooks/registry.json"
        write_json(
            registry_path,
            {
                "managed_hooks": [
                    {
                        "command": "python3 hook.py --runtime {runtime}",
                        "enabled": True,
                        "event": "Stop",
                        "id": "bad-stop-matcher",
                        "matchers": {
                            "codex": "anything",
                        },
                        "runtimes": ["codex"],
                        "scope": "global",
                        "timeout": 5,
                    }
                ],
                "version": 1,
            },
        )

        with self.assertRaises(HookRegistryError):
            load_hooks_registry(registry_path)

    def test_hook_runners_are_silent_on_success(self) -> None:
        script_by_event = {
            "SessionStart": REPO_ROOT / "hooks/scripts/session_start.py",
            "UserPromptSubmit": REPO_ROOT / "hooks/scripts/user_prompt_submit.py",
        }
        home = self.temp_path / "home"
        for event in (
            "SessionStart",
            "UserPromptSubmit",
        ):
            payload = {
                "cwd": str(self.temp_path),
                "hook_event_name": event,
                "model": "gpt-5.5",
                "session_id": "session",
                "transcript_path": None,
            }
            result = subprocess.run(
                [
                    sys.executable,
                    str(script_by_event[event]),
                    "--runtime",
                    "codex",
                ],
                input=json.dumps(payload),
                env={**os.environ, "HOME": str(home)},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr, "")

    def test_stop_runner_is_silent_after_successful_discovery(self) -> None:
        module = self.load_stop_module()
        payload = {
            "cwd": str(self.temp_path),
            "hook_event_name": "Stop",
            "session_id": "session",
        }
        changes = CodexTurnChanges(
            thread_id="session", session_id="session", parent_thread_id="",
            descendant_thread_ids=(), turn_id="turn", turn_started_at=100,
            touched_paths=(),
        )
        stdout, stderr = StringIO(), StringIO()
        # A successful runner requires successful activity discovery. Keep this
        # fixture independent of the machine's live Codex App Server.
        with (
            patch.object(module, "collect_codex_turn_changes", return_value=changes),
            patch.object(sys, "argv", ["stop.py", "--runtime", "codex"]),
            patch.object(sys, "stdin", StringIO(json.dumps(payload))),
            redirect_stdout(stdout), redirect_stderr(stderr),
        ):
            self.assertEqual(module.main(), 0)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")

    def test_session_start_runs_repo_script_from_git_root(self) -> None:
        repo = init_git_repo(self.temp_path / "repo")
        nested = repo / "nested"
        nested.mkdir()
        write_executable(
            repo / "scripts/hooks/session_start.py",
            "\n".join(
                [
                    "#!/usr/bin/env python3",
                    "import json",
                    "import os",
                    "payload = json.load(__import__('sys').stdin)",
                    'print("repo=" + os.environ["AGENT_REPO_ROOT"])',
                    'print("runtime=" + os.environ["AGENT_HOOK_RUNTIME"])',
                    'print("cwd=" + os.getcwd())',
                    'print("event=" + payload["hook_event_name"])',
                    'print("schema=" + payload["schema_version"])',
                    'print("repo_root=" + payload["repo_root"])',
                    'print("raw_cwd=" + payload["raw_payload"]["cwd"])',
                    "",
                ]
            ),
        )
        payload = {
            "cwd": str(nested),
            "hook_event_name": "SessionStart",
            "model": "gpt-5.5",
            "session_id": "session",
            "source": "startup",
            "transcript_path": None,
        }

        result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "hooks/scripts/session_start.py"),
                "--runtime",
                "codex",
            ],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        expected_repo = repo.resolve()
        output = json.loads(result.stdout)
        self.assertEqual(
            output,
            {
                "hookSpecificOutput": {
                    "additionalContext": (
                        f"repo={expected_repo}\nruntime=codex\ncwd={expected_repo}\nevent=SessionStart\n"
                        f"schema=1.0\nrepo_root={expected_repo}\nraw_cwd={nested}\n"
                    ),
                    "hookEventName": "SessionStart",
                }
            },
        )

    def test_session_start_renders_copilot_additional_context(self) -> None:
        repo = init_git_repo(self.temp_path / "repo")
        write_executable(
            repo / "scripts/hooks/session_start.py",
            "\n".join(
                [
                    "#!/usr/bin/env python3",
                    "print('copilot context')",
                    "",
                ]
            ),
        )
        payload = {
            "cwd": str(repo),
            "hook_event_name": "SessionStart",
            "session_id": "session",
        }

        result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "hooks/scripts/session_start.py"),
                "--runtime",
                "copilot",
                "--repos",
                "repo",
            ],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), {"additionalContext": "copilot context\n"})

    def test_repo_filter_skips_unlisted_copilot_repo(self) -> None:
        repo = init_git_repo(self.temp_path / "repo")
        marker = repo / "tmp/session-start-ran.txt"
        write_executable(
            repo / "scripts/hooks/session_start.py",
            "\n".join(
                [
                    "#!/usr/bin/env python3",
                    "import pathlib",
                    "pathlib.Path('tmp').mkdir(exist_ok=True)",
                    "pathlib.Path('tmp/session-start-ran.txt').write_text('ran', encoding='utf-8')",
                    "",
                ]
            ),
        )
        payload = {
            "cwd": str(repo),
            "hook_event_name": "SessionStart",
            "session_id": "session",
        }

        result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "hooks/scripts/session_start.py"),
                "--runtime",
                "copilot",
                "--repos",
                "other-repo",
            ],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")
        self.assertFalse(marker.exists())

    def test_session_start_is_silent_when_repo_script_is_absent(self) -> None:
        repo = init_git_repo(self.temp_path / "repo")
        payload = {
            "cwd": str(repo),
            "hook_event_name": "SessionStart",
            "model": "gpt-5.5",
            "session_id": "session",
            "source": "startup",
            "transcript_path": None,
        }

        result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "hooks/scripts/session_start.py"),
                "--runtime",
                "codex",
            ],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_user_prompt_submit_runs_repo_script_from_git_root(self) -> None:
        repo = init_git_repo(self.temp_path / "repo")
        nested = repo / "nested"
        nested.mkdir()
        write_executable(
            repo / "scripts/hooks/user_prompt_submit.py",
            "\n".join(
                [
                    "#!/usr/bin/env python3",
                    "import json",
                    "import os",
                    "payload = json.load(__import__('sys').stdin)",
                    'print("repo=" + os.environ["AGENT_REPO_ROOT"])',
                    'print("runtime=" + os.environ["AGENT_HOOK_RUNTIME"])',
                    'print("cwd=" + os.getcwd())',
                    'print("event=" + payload["hook_event_name"])',
                    'print("prompt=" + payload["prompt"])',
                    'print("schema=" + payload["schema_version"])',
                    'print("repo_root=" + payload["repo_root"])',
                    'print("raw_turn=" + payload["raw_payload"]["turn_id"])',
                    "",
                ]
            ),
        )
        payload = {
            "cwd": str(nested),
            "hook_event_name": "UserPromptSubmit",
            "model": "gpt-5.5",
            "prompt": "ship it",
            "session_id": "session",
            "transcript_path": None,
            "turn_id": "turn",
        }

        result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "hooks/scripts/user_prompt_submit.py"),
                "--runtime",
                "codex",
            ],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        expected_repo = repo.resolve()
        output = json.loads(result.stdout)
        self.assertEqual(
            output,
            {
                "hookSpecificOutput": {
                    "additionalContext": (
                        f"repo={expected_repo}\nruntime=codex\ncwd={expected_repo}\nevent=UserPromptSubmit\nprompt=ship it\n"
                        f"schema=1.0\nrepo_root={expected_repo}\nraw_turn=turn\n"
                    ),
                    "hookEventName": "UserPromptSubmit",
                }
            },
        )

    def test_user_prompt_submit_ignores_mismatched_event_payload(self) -> None:
        repo = init_git_repo(self.temp_path / "repo")
        marker = repo / "tmp/user-prompt-ran.txt"
        write_executable(
            repo / "scripts/hooks/user_prompt_submit.py",
            "\n".join(
                [
                    "#!/usr/bin/env python3",
                    "import pathlib",
                    "pathlib.Path('tmp').mkdir(exist_ok=True)",
                    "pathlib.Path('tmp/user-prompt-ran.txt').write_text('ran', encoding='utf-8')",
                    "",
                ]
            ),
        )
        payload = {
            "cwd": str(repo),
            "hook_event_name": "SessionStart",
            "prompt": "wrong event",
        }

        result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "hooks/scripts/user_prompt_submit.py"),
                "--runtime",
                "codex",
            ],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")
        self.assertFalse(marker.exists())

    def test_codex_sync_config_preserves_picker_defaults_and_renders_global_stop_hook(self) -> None:
        root = make_control_plane_root(self.temp_path)
        home = self.temp_path / "home"
        bundled_marketplace = self.temp_path / "ChatGPT.app/Contents/Resources/plugins/openai-bundled"
        write_json(root / "mcp/config/presets.json", default_mcp_registry())
        write_json(
            root / "plugins/registry.json",
            {
                "version": 1,
                "paths": {
                    "github_root": str(self.temp_path),
                },
                "managed_plugins": [
                    {
                        "plugin": "computer-use",
                        "marketplace": "openai-bundled",
                        "enabled": True,
                        "scope": "global",
                        "repos": [],
                        "category": "Productivity",
                    },
                    {
                        "plugin": "build-ios-apps",
                        "marketplace": "openai-curated",
                        "enabled": True,
                        "scope": "repo",
                        "repos": ["adi"],
                        "category": "Coding",
                    },
                ],
                "unmanaged_repo_local_plugins": [],
            },
        )
        write_json(
            bundled_marketplace / "plugins/computer-use/.codex-plugin/plugin.json",
            {"name": "computer-use", "version": "1.0.0"},
        )
        write_text(
            home / ".codex/config.toml",
            'model = "gpt-5.6-sol"\n'
            'model_reasoning_effort = "xhigh"\n'
            'plan_mode_reasoning_effort = "max"\n'
            'service_tier = "fast"\n'
            'profile = "autofix"\n'
            '[profiles.autofix]\nmodel = "gpt-5.4"\n'
            '[profiles.autofix.features]\napps = false\n'
            '[plugins."build-ios-apps@openai-curated"]\nenabled = true\n',
        )

        run_command(
            [
                str(REPO_ROOT / "codex/scripts/sync-config.sh"),
                "--apply",
                "--global-only",
                "--global-config",
                str(home / ".codex/config.toml"),
                "--global-hooks",
                str(home / ".codex/hooks.json"),
                "--canonical-dir",
                str(root / "codex/config"),
                "--mcp-registry",
                str(root / "mcp/config/presets.json"),
                "--plugin-registry",
                str(root / "plugins/registry.json"),
                "--hooks-registry",
                str(root / "hooks/registry.json"),
            ],
            env={
                "HOME": str(home),
                "CODEX_BUNDLED_MARKETPLACE": str(bundled_marketplace),
            },
        )

        rendered_config = (home / ".codex/config.toml").read_text(encoding="utf-8")
        self.assertNotIn('\nmodel = ', rendered_config)
        self.assertIn('model_reasoning_effort = "xhigh"', rendered_config)
        self.assertIn('plan_mode_reasoning_effort = "max"', rendered_config)
        self.assertNotIn('\nservice_tier = ', rendered_config)
        self.assertIn("hooks = true", rendered_config)
        self.assertIn('[plugins."computer-use@openai-bundled"]', rendered_config)
        self.assertNotIn("build-ios-apps@openai-curated", rendered_config)
        self.assertIn(
            f'path = "{home}/.codex/skills/.system/plugin-creator/SKILL.md"',
            rendered_config,
        )
        self.assertNotIn("notify =", rendered_config)
        self.assertNotIn('profile = "autofix"', rendered_config)
        self.assertNotIn("[profiles.autofix]", rendered_config)

        hooks = read_json(home / ".codex/hooks.json")
        self.assertEqual(
            hooks["hooks"]["Stop"][0]["hooks"][0]["command"],
            'python3 "$HOME/GitHub/agents/hooks/scripts/stop.py" --runtime codex',
        )

    def test_codex_sync_config_uses_native_bundled_marketplace_and_caches_enabled_plugins(self) -> None:
        root = make_control_plane_root(self.temp_path)
        home = self.temp_path / "home"
        bundled_marketplace = self.temp_path / "ChatGPT.app/Contents/Resources/plugins/openai-bundled"
        stale_marketplace_mirror = home / ".codex/.tmp/bundled-marketplaces/openai-bundled"
        write_json(root / "mcp/config/presets.json", default_mcp_registry())
        write_json(
            root / "plugins/registry.json",
            {
                "version": 1,
                "paths": {
                    "github_root": str(self.temp_path),
                },
                "managed_plugins": [
                    {
                        "plugin": "chrome",
                        "marketplace": "openai-bundled",
                        "enabled": True,
                        "scope": "global",
                        "repos": [],
                        "category": "Productivity",
                    },
                    {
                        "plugin": "browser",
                        "marketplace": "openai-bundled",
                        "enabled": False,
                        "scope": "global",
                        "repos": [],
                        "category": "Engineering",
                    },
                ],
                "unmanaged_repo_local_plugins": [],
            },
        )
        write_json(
            bundled_marketplace / ".agents/plugins/marketplace.json",
            {
                "name": "openai-bundled",
                "plugins": [
                    {"name": "chrome", "source": {"source": "local", "path": "./plugins/chrome"}},
                    {"name": "browser", "source": {"source": "local", "path": "./plugins/browser"}},
                ],
            },
        )
        write_json(
            bundled_marketplace / "plugins/chrome/.codex-plugin/plugin.json",
            {"name": "chrome", "version": "0.1.7"},
        )
        write_json(
            bundled_marketplace / "plugins/browser/.codex-plugin/plugin.json",
            {"name": "browser", "version": "0.1.0-alpha2"},
        )
        write_json(
            home / ".codex/plugins/cache/openai-bundled/browser-use/0.1.0-alpha2/.codex-plugin/plugin.json",
            {"name": "browser-use", "version": "0.1.0-alpha2"},
        )
        write_text(stale_marketplace_mirror / "plugins/chrome/stale.txt", "stale\n")

        run_command(
            [
                str(REPO_ROOT / "codex/scripts/sync-config.sh"),
                "--apply",
                "--global-only",
                "--global-config",
                str(home / ".codex/config.toml"),
                "--global-hooks",
                str(home / ".codex/hooks.json"),
                "--canonical-dir",
                str(root / "codex/config"),
                "--mcp-registry",
                str(root / "mcp/config/presets.json"),
                "--plugin-registry",
                str(root / "plugins/registry.json"),
                "--hooks-registry",
                str(root / "hooks/registry.json"),
            ],
            env={
                "HOME": str(home),
                "CODEX_BUNDLED_MARKETPLACE": str(bundled_marketplace),
            },
        )

        rendered_config = (home / ".codex/config.toml").read_text(encoding="utf-8")
        self.assertIn("[marketplaces.openai-bundled]", rendered_config)
        self.assertIn(f'source = "{bundled_marketplace}"', rendered_config)
        self.assertIn('[plugins."chrome@openai-bundled"]', rendered_config)
        self.assertIn('[plugins."browser@openai-bundled"]', rendered_config)
        self.assertTrue(
            (home / ".codex/plugins/cache/openai-bundled/chrome/0.1.7/.codex-plugin/plugin.json").is_file()
        )
        self.assertFalse((home / ".codex/plugins/cache/openai-bundled/browser-use").exists())
        self.assertFalse(stale_marketplace_mirror.exists())

    def test_stop_hook_has_tracking_upstream_false_for_new_local_branch(self) -> None:
        module = self.load_stop_module()
        remote = init_git_repo(self.temp_path / "remote.git")
        run_command(["git", "-C", str(remote), "config", "receive.denyCurrentBranch", "updateInstead"])
        repo = init_git_repo(self.temp_path / "repo", with_initial_commit=True)
        run_command(["git", "-C", str(repo), "remote", "add", "origin", str(remote)])
        run_command(["git", "-C", str(repo), "push", "-u", "origin", "main"])
        run_command(["git", "-C", str(repo), "checkout", "-b", "feature/test"])

        self.assertFalse(module.has_tracking_upstream(str(repo)))

    def test_stop_hook_uses_initial_push_for_branch_without_upstream(self) -> None:
        module = self.load_stop_module()
        remote = init_git_repo(self.temp_path / "remote.git")
        run_command(["git", "-C", str(remote), "config", "receive.denyCurrentBranch", "updateInstead"])
        repo = init_git_repo(self.temp_path / "repo", with_initial_commit=True)
        run_command(["git", "-C", str(repo), "remote", "add", "origin", str(remote)])
        run_command(["git", "-C", str(repo), "push", "-u", "origin", "main"])
        run_command(["git", "-C", str(repo), "checkout", "-b", "feature/test"])
        (repo / "note.txt").write_text("hello\n", encoding="utf-8")

        with patch.dict(os.environ, {"HOME": str(self.temp_path / "test-home")}):
            with patch.object(module, "log"):
                output = module.process_repo(str(repo), {"hook_event_name": "Stop"}, runtime="codex")

        self.assertIsNone(output)
        upstream = run_command(
            [
                "git",
                "-C",
                str(repo),
                "rev-parse",
                "--abbrev-ref",
                "--symbolic-full-name",
                "@{upstream}",
            ]
        )
        self.assertEqual(upstream.stdout.strip(), "origin/feature/test")

    def test_stop_hook_uses_optimistic_push_for_branch_with_upstream(self) -> None:
        module = self.load_stop_module()
        repo = init_git_repo(self.temp_path / "repo", with_initial_commit=True)
        captured_commands: list[list[str]] = []

        def fake_run(args, cwd, *, timeout, env=None):  # noqa: ANN001, ARG001
            captured_commands.append(list(args))
            if args[:3] == ["git", "status", "--porcelain"]:
                return SimpleNamespace(returncode=0, stdout=" M file.txt\n", stderr="")
            if args[:4] == ["git", "symbolic-ref", "--quiet", "--short"]:
                return SimpleNamespace(returncode=0, stdout="main\n", stderr="")
            if args[:3] == ["git", "config", "--get"]:
                return SimpleNamespace(returncode=0, stdout="origin\n", stderr="")
            if args[:4] == ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name"]:
                return SimpleNamespace(returncode=0, stdout="origin/main\n", stderr="")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with patch.object(module, "run", side_effect=fake_run):
            with patch.object(module, "is_git_repo", return_value=True):
                with patch.object(module, "has_in_progress_ops", return_value=False):
                    with patch.object(module, "clear_stale_index_lock", return_value=True):
                        with patch.object(module, "log"):
                            output = module.process_repo(
                                str(repo),
                                {"hook_event_name": "Stop"},
                                runtime="codex",
                            )

        self.assertIsNone(output)
        self.assertIn(["git", "push", "origin", "HEAD"], captured_commands)
        self.assertNotIn(["git", "pull", "--rebase"], captured_commands)
        self.assertNotIn(["git", "push", "-u", "origin", "HEAD"], captured_commands)

    def test_stop_hook_rebases_and_retries_push_when_remote_is_ahead(self) -> None:
        module = self.load_stop_module()
        repo = init_git_repo(self.temp_path / "repo", with_initial_commit=True)
        write_executable(repo / "scripts/check-fast.sh", "#!/bin/sh\nexit 0\n")
        captured_commands: list[list[str]] = []
        push_attempts = 0
        rebased = False

        def fake_run(args, cwd, *, timeout, env=None):  # noqa: ANN001, ARG001
            nonlocal push_attempts, rebased
            captured_commands.append(list(args))
            if args[:3] == ["git", "status", "--porcelain"]:
                status = "" if rebased else " M file.txt\n"
                return SimpleNamespace(returncode=0, stdout=status, stderr="")
            if args[:4] == ["git", "symbolic-ref", "--quiet", "--short"]:
                return SimpleNamespace(returncode=0, stdout="main\n", stderr="")
            if args[:3] == ["git", "config", "--get"]:
                return SimpleNamespace(returncode=0, stdout="origin\n", stderr="")
            if args[:4] == ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name"]:
                return SimpleNamespace(returncode=0, stdout="origin/main\n", stderr="")
            if args == ["git", "push", "origin", "HEAD"]:
                push_attempts += 1
                if push_attempts == 1:
                    return SimpleNamespace(
                        returncode=1,
                        stdout="",
                        stderr="! [rejected] HEAD -> main (fetch first)\n"
                        "hint: Updates were rejected because the remote contains work.\n",
                    )
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            if args == ["git", "pull", "--rebase"]:
                rebased = True
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with patch.object(module, "run", side_effect=fake_run):
            with patch.object(module, "is_git_repo", return_value=True):
                with patch.object(module, "has_in_progress_ops", return_value=False):
                    with patch.object(module, "clear_stale_index_lock", return_value=True):
                        with patch.object(module, "log"):
                            output = module.process_repo(
                                str(repo),
                                {"hook_event_name": "Stop"},
                                runtime="codex",
                            )

        self.assertIsNone(output)
        self.assertEqual(push_attempts, 2)
        self.assertIn(["git", "pull", "--rebase"], captured_commands)
        self.assertIn(["bash", "scripts/check-fast.sh"], captured_commands)

    def test_stop_hook_blocks_when_post_rebase_fast_check_fails(self) -> None:
        module = self.load_stop_module()
        repo = init_git_repo(self.temp_path / "repo", with_initial_commit=True)
        write_executable(repo / "scripts/check-fast.sh", "#!/bin/sh\nexit 1\n")
        push_attempts = 0

        def fake_run(args, cwd, *, timeout, env=None):  # noqa: ANN001, ARG001
            nonlocal push_attempts
            if args[:3] == ["git", "status", "--porcelain"]:
                return SimpleNamespace(returncode=0, stdout=" M file.txt\n", stderr="")
            if args[:4] == ["git", "symbolic-ref", "--quiet", "--short"]:
                return SimpleNamespace(returncode=0, stdout="main\n", stderr="")
            if args[:3] == ["git", "config", "--get"]:
                return SimpleNamespace(returncode=0, stdout="origin\n", stderr="")
            if args[:4] == ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name"]:
                return SimpleNamespace(returncode=0, stdout="origin/main\n", stderr="")
            if args == ["git", "push", "origin", "HEAD"]:
                push_attempts += 1
                return SimpleNamespace(
                    returncode=1,
                    stdout="",
                    stderr="! [rejected] HEAD -> main (fetch first)\n",
                )
            if args == ["bash", "scripts/check-fast.sh"]:
                return SimpleNamespace(
                    returncode=17,
                    stdout="",
                    stderr="rebased tree failed validation\n",
                )
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with patch.object(module, "run", side_effect=fake_run):
            with patch.object(module, "is_git_repo", return_value=True):
                with patch.object(module, "has_in_progress_ops", return_value=False):
                    with patch.object(module, "clear_stale_index_lock", return_value=True):
                        with patch.object(module, "log"):
                            output = module.process_repo(
                                str(repo),
                                {"hook_event_name": "Stop"},
                                runtime="claude",
                            )

        self.assertIsNotNone(output)
        assert output is not None
        self.assertEqual(output["decision"], "block")
        self.assertIn("scripts/check-fast.sh after git pull --rebase", output["reason"])
        self.assertIn("rebased tree failed validation", output["reason"])
        self.assertEqual(push_attempts, 1)

    def test_stop_hook_blocks_on_pre_commit_failure(self) -> None:
        module = self.load_stop_module()
        repo = init_git_repo(self.temp_path / "repo", with_initial_commit=True)
        write_executable(
            repo / ".git/hooks/pre-commit",
            "#!/bin/sh\nprintf 'repo check failed\\n' >&2\nexit 1\n",
        )
        (repo / "note.txt").write_text("hello\n", encoding="utf-8")

        with patch.dict(os.environ, {"HOME": str(self.temp_path / "test-home")}):
            with patch.object(module, "log"):
                output = module.process_repo(str(repo), {"hook_event_name": "Stop"}, runtime="codex")

        self.assertIsNotNone(output)
        assert output is not None
        self.assertEqual(output["decision"], "block")
        self.assertIn("git commit / pre-commit checks", output["reason"])
        self.assertIn("repo check failed", output["reason"])
        self.assertIn("Please fix the issue", output["reason"])

    def test_stop_feedback_turn_starts_app_server_turn(self) -> None:
        fake_bin = self.temp_path / "bin"
        fake_bin.mkdir()
        calls_path = self.temp_path / "calls.jsonl"
        reason_file = self.temp_path / "reason.txt"
        reason_file.write_text("repo check failed\n", encoding="utf-8")
        write_executable(
            fake_bin / "codex",
            "\n".join(
                [
                    "#!/usr/bin/env python3",
                    "import json",
                    "import os",
                    "import sys",
                    "calls_path = os.environ['FAKE_CODEX_CALLS']",
                    "def emit(value):",
                    "    print(json.dumps(value), flush=True)",
                    "for line in sys.stdin:",
                    "    message = json.loads(line)",
                    "    with open(calls_path, 'a', encoding='utf-8') as handle:",
                    "        handle.write(json.dumps(message, sort_keys=True) + '\\n')",
                    "    method = message.get('method')",
                    "    request_id = message.get('id')",
                    "    if request_id is None:",
                    "        continue",
                    "    if method == 'turn/start':",
                    "        params = message.get('params') or {}",
                    "        text = params['input'][0]['text']",
                    "        if not text.startswith('Hook feedback\\n\\nrepo check failed'):",
                    "            emit({'id': request_id, 'error': {'message': 'bad prompt'}})",
                    "            continue",
                    "        emit({'id': request_id, 'result': {'turn': {'id': 'turn-1'}}})",
                    "        emit({'method': 'turn/completed', 'params': {'threadId': params['threadId'], 'turnId': 'turn-1', 'status': 'completed'}})",
                    "        continue",
                    "    emit({'id': request_id, 'result': {}})",
                    "",
                ]
            ),
        )

        result = run_command(
            [
                sys.executable,
                str(REPO_ROOT / "hooks/scripts/stop_feedback_turn.py"),
                "--thread-id",
                "thread-1",
                "--cwd",
                str(self.temp_path),
                "--reason-file",
                str(reason_file),
                "--initial-delay-seconds",
                "0",
                "--timeout-seconds",
                "2",
                "--turn-timeout-seconds",
                "2",
            ],
            env={
                "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
                "FAKE_CODEX_CALLS": str(calls_path),
            },
        )

        self.assertEqual(result.returncode, 0)
        calls = [json.loads(line) for line in calls_path.read_text(encoding="utf-8").splitlines()]
        methods = [call.get("method") for call in calls]
        self.assertIn("initialize", methods)
        self.assertIn("thread/resume", methods)
        self.assertIn("turn/start", methods)
        self.assertFalse(reason_file.exists())
