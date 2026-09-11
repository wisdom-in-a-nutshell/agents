from __future__ import annotations

import json
import os
import plistlib
import sys
from pathlib import Path

from tests.control_plane.support import (
    REPO_ROOT,
    TempDirTestCase,
    copy_repo_file,
    run_command,
    write_executable,
    write_text,
)


class ThreadFinalizerFixture(TempDirTestCase):
    def _python_fixture(self, installed: str | None) -> tuple[Path, Path]:
        site = self.temp_path / "python-site"
        package = site / "websockets"
        write_text(package / "__init__.py", "" if installed else "raise ImportError('missing fixture')\n")
        write_text(package / "sync/__init__.py", "")
        write_text(package / "sync/client.py", "def unix_connect(): pass\n")
        metadata = site / "websockets.dist-info/METADATA"
        write_text(metadata, f"Name: websockets\nVersion: {installed or '0.0'}\n")
        pip_log = self.temp_path / "pip.jsonl"
        python = write_executable(
            self.temp_path / "fixture-python",
            f"""#!{sys.executable}
import json, os, pathlib, subprocess, sys
site = pathlib.Path({str(site)!r})
if sys.argv[1:2] == ['-c']:
    print(pathlib.Path(__file__).resolve())
    sys.exit(0)
if sys.argv[1:4] == ['-m', 'pip', 'install']:
    if '--help' in sys.argv:
        print('--break-system-packages')
        sys.exit(0)
    with pathlib.Path({str(pip_log)!r}).open('a') as log:
        log.write(json.dumps(sys.argv[1:]) + '\\n')
    if os.environ.get('FIXTURE_PIP_FAIL'):
        sys.exit(7)
    (site / 'websockets/__init__.py').write_text('')
    (site / 'websockets.dist-info/METADATA').write_text('Name: websockets\\nVersion: 16.0\\n')
    sys.exit(0)
env = dict(os.environ, PYTHONPATH=str(site), PYTHONNOUSERSITE='1')
sys.exit(subprocess.run([{sys.executable!r}, *sys.argv[1:]], env=env).returncode)
""",
        )
        return python, pip_log


class ThreadFinalizerDependencyTests(ThreadFinalizerFixture):
    def test_default_dry_run_checks_missing_and_mismatched_versions_without_installing(self) -> None:
        for installed in (None, "15.0", "16.0"):
            with self.subTest(installed=installed):
                python, pip_log = self._python_fixture(installed)
                result = run_command([
                    str(REPO_ROOT / "codex/scripts/install-thread-finalizer-deps.sh"),
                    "--python", str(python),
                ])
                self.assertIn("OK:" if installed == "16.0" else "WOULD INSTALL:", result.stdout)
                self.assertFalse(pip_log.exists())

    def test_apply_installs_exact_version_once_and_rechecks_import(self) -> None:
        python, pip_log = self._python_fixture("15.0")
        command = [
            str(REPO_ROOT / "codex/scripts/install-thread-finalizer-deps.sh"),
            "--apply", "--python", str(python),
        ]
        first = run_command(command)
        second = run_command(command)
        self.assertIn("OK: websockets==16.0", first.stdout)
        self.assertIn("OK: websockets==16.0", second.stdout)
        self.assertEqual(
            [["-m", "pip", "install", "--user", "--break-system-packages", "websockets==16.0"]],
            [json.loads(line) for line in pip_log.read_text().splitlines()],
        )

    def test_failed_install_is_reported(self) -> None:
        python, _ = self._python_fixture(None)
        result = run_command([
            str(REPO_ROOT / "codex/scripts/install-thread-finalizer-deps.sh"),
            "--apply", "--python", str(python),
        ], env={"FIXTURE_PIP_FAIL": "1"}, check=False)
        self.assertEqual(7, result.returncode)
        self.assertNotIn("OK:", result.stdout)

    def test_python_flag_requires_value(self) -> None:
        result = run_command([
            str(REPO_ROOT / "codex/scripts/install-thread-finalizer-deps.sh"),
            "--python",
        ], check=False)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("--python requires a path", result.stderr)


class ThreadFinalizerLaunchAgentTests(ThreadFinalizerFixture):
    def _runtime(self, installed: str | None) -> tuple[dict[str, str], Path, Path, Path]:
        python, pip_log = self._python_fixture(installed)
        home = self.temp_path / "home"
        resolver = home / "GitHub/scripts/setup/codex/resolve-preferred-homebrew-python.sh"
        write_executable(resolver, f"#!/bin/bash\nprintf '%s\\n' {str(python)!r}\n")
        tools = self.temp_path / "ambient-bin"
        launch_log = self.temp_path / "launchctl.log"
        unexpected_python = self.temp_path / "wrong-python.log"
        write_executable(tools / "python3", f"#!/bin/bash\ntouch {str(unexpected_python)!r}\nexit 9\n")
        write_executable(tools / "launchctl", "#!/bin/bash\nprintf '%s\\n' \"$*\" >> \"$LAUNCH_LOG\"\n")
        write_executable(tools / "plutil", "#!/bin/bash\nexit 0\n")
        return {
            "HOME": str(home),
            "PATH": f"{tools}:{os.environ.get('PATH', '')}",
            "PYTHON_BIN": str(tools / "python3"),
            "AGENTS_CONTROL_PLANE_ROOT": str(REPO_ROOT),
            "LAUNCH_LOG": str(launch_log),
        }, python, pip_log, unexpected_python

    def test_default_dependency_and_launchagent_use_preferred_python_despite_path_skew(self) -> None:
        env, python, pip_log, wrong_python = self._runtime("16.0")
        dependency = run_command([
            str(REPO_ROOT / "codex/scripts/install-thread-finalizer-deps.sh"),
        ], env=env)
        self.assertIn(f"Python: {python}", dependency.stdout)
        result = run_command([
            str(REPO_ROOT / "codex/scripts/install-finalize-stale-codex-threads-launchagent.sh"),
            "--dry-run",
        ], env=env)
        payload = plistlib.loads(result.stdout.encode())
        self.assertEqual(payload["ProgramArguments"][:2], [
            str(python), str(REPO_ROOT / "codex/scripts/finalize-stale-codex-threads.py"),
        ])
        self.assertFalse(wrong_python.exists())
        self.assertFalse(pip_log.exists())

    def test_direct_apply_installs_for_pinned_interpreter_before_loading(self) -> None:
        env, python, pip_log, wrong_python = self._runtime(None)
        run_command([
            str(REPO_ROOT / "codex/scripts/install-finalize-stale-codex-threads-launchagent.sh"),
            "--apply", "--label", "com.test.finalizer", "--no-run-at-load",
        ], env=env)
        payload = plistlib.loads((Path(env["HOME"]) / "Library/LaunchAgents/com.test.finalizer.plist").read_bytes())
        self.assertEqual(str(python), payload["ProgramArguments"][0])
        self.assertIn("websockets==16.0", pip_log.read_text())
        self.assertIn("bootstrap ", Path(env["LAUNCH_LOG"]).read_text())
        self.assertFalse(wrong_python.exists())

    def test_dependency_failure_leaves_existing_job_untouched(self) -> None:
        env, _, _, _ = self._runtime(None)
        plist = Path(env["HOME"]) / "Library/LaunchAgents/com.test.finalizer.plist"
        write_text(plist, "existing job")
        result = run_command([
            str(REPO_ROOT / "codex/scripts/install-finalize-stale-codex-threads-launchagent.sh"),
            "--apply", "--label", "com.test.finalizer",
        ], env={**env, "FIXTURE_PIP_FAIL": "1"}, check=False)
        self.assertEqual(7, result.returncode)
        self.assertEqual("existing job", plist.read_text())
        self.assertFalse(Path(env["LAUNCH_LOG"]).exists())

    def test_missing_resolver_fails_without_ambient_python_fallback(self) -> None:
        env, _, _, wrong_python = self._runtime("16.0")
        (Path(env["HOME"]) / "GitHub/scripts/setup/codex/resolve-preferred-homebrew-python.sh").unlink()
        result = run_command([
            str(REPO_ROOT / "codex/scripts/install-finalize-stale-codex-threads-launchagent.sh"),
            "--dry-run",
        ], env=env, check=False)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("Preferred Python resolver missing", result.stderr)
        self.assertFalse(wrong_python.exists())

    def test_explicit_python_override_is_pinned_without_resolver(self) -> None:
        env, python, _, _ = self._runtime("16.0")
        (Path(env["HOME"]) / "GitHub/scripts/setup/codex/resolve-preferred-homebrew-python.sh").unlink()
        result = run_command([
            str(REPO_ROOT / "codex/scripts/install-finalize-stale-codex-threads-launchagent.sh"),
            "--dry-run", "--python", str(python),
        ], env=env)
        payload = plistlib.loads(result.stdout.encode())
        self.assertEqual(str(python), payload["ProgramArguments"][0])


class ThreadFinalizerBootstrapTests(TempDirTestCase):
    def test_launchagent_installer_owns_dependencies_and_failure_stops_bootstrap(self) -> None:
        root = self.temp_path / "agents"
        bootstrap = copy_repo_file("codex/scripts/bootstrap-machine-codex.sh", root)
        log = self.temp_path / "bootstrap.log"
        scripts = (
            "sync-config.sh", "sync-global-agents-md.sh", "sync-trusted-projects.sh",
            "sync-repo-codex-configs.sh", "sync-hook-trust-state.py", "install-pdf-skill-deps.sh",
            "install-finalize-stale-codex-threads-launchagent.sh",
            "install-archive-stale-claude-sessions-launchagent.sh", "check-codex-control-plane.sh",
        )
        stub = """#!/usr/bin/env bash
set -euo pipefail
printf '%s|%s\\n' "$(basename "$0")" "$*" >> "${LOG_FILE:?}"
if [[ "$(basename "$0")" == install-finalize-stale-codex-threads-launchagent.sh && "${FIXTURE_DEPS_FAIL:-0}" == 1 ]]; then
  exit 7
fi
"""
        for name in scripts:
            write_executable(root / "codex/scripts" / name, stub)
        for mode in ("--dry-run", "--apply"):
            with self.subTest(mode=mode):
                if log.exists():
                    log.unlink()
                run_command([str(bootstrap), mode], env={"LOG_FILE": str(log)})
                calls = log.read_text().splitlines()
                self.assertEqual(1, calls.count(f"install-finalize-stale-codex-threads-launchagent.sh|{mode}"))
                self.assertFalse(any("install-thread-finalizer-deps.sh" in call for call in calls))
        log.unlink()
        result = run_command([str(bootstrap), "--apply"], env={
            "LOG_FILE": str(log), "FIXTURE_DEPS_FAIL": "1",
        }, check=False)
        self.assertEqual(7, result.returncode)
        self.assertNotIn("check-codex-control-plane.sh", log.read_text())
