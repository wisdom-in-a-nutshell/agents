from __future__ import annotations

import json
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


class ThreadFinalizerDependencyTests(TempDirTestCase):
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


class ThreadFinalizerBootstrapTests(TempDirTestCase):
    def test_dependencies_run_before_launchagent_and_failure_stops_bootstrap(self) -> None:
        root = self.temp_path / "agents"
        bootstrap = copy_repo_file("codex/scripts/bootstrap-machine-codex.sh", root)
        log = self.temp_path / "bootstrap.log"
        scripts = (
            "sync-config.sh", "sync-global-agents-md.sh", "sync-trusted-projects.sh",
            "sync-repo-codex-configs.sh", "sync-hook-trust-state.py", "install-pdf-skill-deps.sh",
            "install-thread-finalizer-deps.sh", "install-finalize-stale-codex-threads-launchagent.sh",
            "install-archive-stale-claude-sessions-launchagent.sh", "check-codex-control-plane.sh",
        )
        stub = """#!/usr/bin/env bash
set -euo pipefail
printf '%s|%s\\n' "$(basename "$0")" "$*" >> "${LOG_FILE:?}"
if [[ "$(basename "$0")" == install-thread-finalizer-deps.sh && "${FIXTURE_DEPS_FAIL:-0}" == 1 ]]; then
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
                self.assertLess(
                    calls.index(f"install-thread-finalizer-deps.sh|{mode}"),
                    calls.index(f"install-finalize-stale-codex-threads-launchagent.sh|{mode}"),
                )
        log.unlink()
        result = run_command([str(bootstrap), "--apply"], env={
            "LOG_FILE": str(log), "FIXTURE_DEPS_FAIL": "1",
        }, check=False)
        self.assertEqual(7, result.returncode)
        self.assertNotIn("install-finalize-stale-codex-threads-launchagent.sh", log.read_text())
