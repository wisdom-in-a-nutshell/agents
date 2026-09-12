from __future__ import annotations

import json

from tests.control_plane.support import (
    REPO_ROOT,
    TempDirTestCase,
    make_control_plane_root,
    run_command,
    write_executable,
    write_json,
    write_text,
)


class AgentRuntimeDriftAuditTests(TempDirTestCase):
    def _write_live_codex_config(  # noqa: ANN001
        self,
        home,
        *,
        include_chrome: bool = True,
        include_computer_use: bool = True,
    ):
        sections = [
            '[plugins."browser@openai-bundled"]\n'
            "enabled = true\n",
        ]
        if include_chrome:
            sections.append(
                '[plugins."chrome@openai-bundled"]\n'
                "enabled = true\n"
            )
        if include_computer_use:
            sections.append(
                '[plugins."computer-use@openai-bundled"]\n'
                "enabled = true\n"
            )
        write_text(home / ".codex/config.toml", "\n".join(sections))

    def _write_plugin(self, home, marketplace: str, name: str, version: str = "1.0.0") -> None:  # noqa: ANN001
        write_json(
            home / ".codex/plugins/cache" / marketplace / name / version / ".codex-plugin/plugin.json",
            {
                "name": name,
                "version": version,
                "interface": {
                    "displayName": name,
                },
            },
        )

    def _write_required_plugins(self, home, *, include_chrome: bool = True, include_computer_use: bool = True) -> None:  # noqa: ANN001
        self._write_plugin(home, "openai-bundled", "browser")
        if include_chrome:
            self._write_plugin(home, "openai-bundled", "chrome")
        if include_computer_use:
            self._write_plugin(home, "openai-bundled", "computer-use")

    def test_audit_passes_for_known_required_codex_plugin(self) -> None:
        home = self.temp_path / "home"
        self._write_live_codex_config(home)
        self._write_required_plugins(home)

        result = run_command(
            [
                str(REPO_ROOT / "scripts/audit-agent-runtime-drift.py"),
                "--json",
                "--skip-control-plane-check",
                "--home",
                str(home),
            ]
        )

        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["data"]["summary"]["errors"], 0)

    def test_audit_runs_control_plane_check_when_not_skipped(self) -> None:
        home = self.temp_path / "home"
        agents_repo = make_control_plane_root(self.temp_path)
        write_executable(
            agents_repo / "scripts/check-agent-control-planes.sh",
            "#!/usr/bin/env bash\nset -euo pipefail\necho control-plane-ok\n",
        )
        write_text(
            home / ".codex/config.toml",
            '[plugins."computer-use@openai-bundled"]\n'
            "enabled = true\n",
        )
        self._write_plugin(home, "openai-bundled", "computer-use")

        result = run_command(
            [
                str(REPO_ROOT / "scripts/audit-agent-runtime-drift.py"),
                "--json",
                "--agents-repo",
                str(agents_repo),
                "--home",
                str(home),
            ]
        )

        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["data"]["summary"]["errors"], 0)
        self.assertEqual(payload["data"]["checks"][0]["name"], "agent_control_plane")
        self.assertEqual(payload["data"]["checks"][0]["status"], "ok")

    def test_audit_fails_for_unknown_openai_plugin(self) -> None:
        home = self.temp_path / "home"
        self._write_live_codex_config(home)
        self._write_required_plugins(home)
        self._write_plugin(home, "openai-curated", "surprise-plugin")

        result = run_command(
            [
                str(REPO_ROOT / "scripts/audit-agent-runtime-drift.py"),
                "--plain",
                "--skip-control-plane-check",
                "--home",
                str(home),
            ],
            check=False,
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("unclassified OpenAI Codex plugin", result.stdout)
        self.assertIn("surprise-plugin@openai-curated", result.stdout)

    def test_audit_allows_primary_runtime_artifact_plugins(self) -> None:
        home = self.temp_path / "home"
        self._write_live_codex_config(home)
        self._write_required_plugins(home)
        self._write_plugin(home, "openai-primary-runtime", "documents")
        self._write_plugin(home, "openai-primary-runtime", "pdf")
        self._write_plugin(home, "openai-primary-runtime", "presentations")
        self._write_plugin(home, "openai-primary-runtime", "spreadsheets")
        self._write_plugin(home, "openai-primary-runtime", "template-creator")

        result = run_command(
            [
                str(REPO_ROOT / "scripts/audit-agent-runtime-drift.py"),
                "--json",
                "--skip-control-plane-check",
                "--home",
                str(home),
            ]
        )

        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["data"]["summary"]["errors"], 0)

    def test_audit_allows_app_managed_openai_review_plugins(self) -> None:
        home = self.temp_path / "home"
        self._write_live_codex_config(home)
        self._write_required_plugins(home)
        self._write_plugin(home, "openai-curated-remote", "plugin-management")
        self._write_plugin(home, "openai-curated-remote", "sites", version="0.1.62")

        result = run_command(
            [
                str(REPO_ROOT / "scripts/audit-agent-runtime-drift.py"),
                "--json",
                "--skip-control-plane-check",
                "--home",
                str(home),
            ]
        )

        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["data"]["summary"]["errors"], 0)

    def test_audit_fails_when_required_plugin_is_not_enabled_live(self) -> None:
        home = self.temp_path / "home"
        self._write_live_codex_config(home, include_computer_use=False)
        self._write_required_plugins(home)

        result = run_command(
            [
                str(REPO_ROOT / "scripts/audit-agent-runtime-drift.py"),
                "--plain",
                "--skip-control-plane-check",
                "--home",
                str(home),
            ],
            check=False,
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("required Codex plugin availability check failed", result.stdout)
        self.assertIn("computer-use@openai-bundled is not enabled", result.stdout)

    def test_audit_repairs_managed_plugin_drift_when_requested(self) -> None:
        home = self.temp_path / "home"
        agents_repo = make_control_plane_root(self.temp_path)
        repair_script = agents_repo / "codex/scripts/sync-config.sh"
        write_executable(
            repair_script,
            f"""#!/usr/bin/env bash
set -euo pipefail
mkdir -p {home}/.codex
cat > {home}/.codex/config.toml <<'CONFIG'
[plugins."computer-use@openai-bundled"]
enabled = true
CONFIG
mkdir -p {home}/.codex/plugins/cache/openai-bundled/computer-use/1.0.0/.codex-plugin
cat > {home}/.codex/plugins/cache/openai-bundled/computer-use/1.0.0/.codex-plugin/plugin.json <<'JSON'
{{"name":"computer-use","version":"1.0.0"}}
JSON
""",
        )

        result = run_command(
            [
                str(REPO_ROOT / "scripts/audit-agent-runtime-drift.py"),
                "--json",
                "--skip-control-plane-check",
                "--repair-managed-plugin-drift",
                "--agents-repo",
                str(agents_repo),
                "--home",
                str(home),
            ]
        )

        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["data"]["summary"]["errors"], 0)
        checks = {check["name"]: check for check in payload["data"]["checks"]}
        self.assertEqual(checks["managed_plugin_repair"]["status"], "ok")
        self.assertEqual(checks["codex_required_plugins"]["status"], "ok")
