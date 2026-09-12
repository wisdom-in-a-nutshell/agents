from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import tomllib

ROOT = Path(__file__).resolve().parents[2]


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "codex/scripts" / filename)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


class AzureAstraProfileTests(unittest.TestCase):
    def test_profile_is_opt_in_and_read_only(self):
        data = tomllib.loads((ROOT / "codex/config/azure-astra.config.toml").read_text())
        self.assertEqual(data["model_provider"], "azure_astra")
        self.assertEqual(data["sandbox_mode"], "read-only")
        self.assertEqual(data["model_reasoning_effort"], "medium")
        self.assertEqual(data["service_tier"], "default")
        self.assertEqual(data["web_search"], "disabled")
        self.assertFalse(data["features"]["hooks"])
        self.assertFalse(data["agents"]["enabled"])
        provider = data["model_providers"]["azure_astra"]
        self.assertEqual(provider["wire_api"], "responses")
        self.assertFalse(provider["requires_openai_auth"])
        self.assertNotIn("env_key", provider)
        global_config = tomllib.loads((ROOT / "codex/config/global.config.toml").read_text())
        self.assertNotEqual(global_config.get("model_provider"), "azure_astra")

    def test_token_parser_and_closed_failures(self):
        helper = module("astra_token", "azure-astra-token.py")
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as tmp:
            path = Path(tmp) / "env"
            path.write_text("OTHER=x\nexport LLM_API_KEY='test-token' # comment\n")
            self.assertEqual(helper.read_token(path), "test-token")
            for value in ("", "LLM_API_KEY=\n", "LLM_API_KEY=x\nLLM_API_KEY=y\n",
                          'LLM_API_KEY="two words"\n', "LLM_API_KEY='broken"):
                path.write_text(value)
                with self.assertRaises(ValueError):
                    helper.read_token(path)
            # Shell content is data only, never expanded or executed.
            path.write_text("LLM_API_KEY='$(not-executed)'\n")
            self.assertEqual(helper.read_token(path), "$(not-executed)")

    def test_helper_missing_secret_does_not_leak_or_fallback(self):
        import os
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as tmp:
            result = subprocess.run(
                [sys.executable, str(ROOT / "codex/scripts/azure-astra-token.py")],
                env={**os.environ, "HOME": tmp, "LLM_API_KEY": "must-not-fallback"},
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, "")
            self.assertNotIn("must-not-fallback", result.stderr)

