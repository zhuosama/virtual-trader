import os
import sys
import tempfile
import unittest
import subprocess
from pathlib import Path
from unittest.mock import patch


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "agents"))


class TestLLMConfigFallback(unittest.TestCase):
    def test_loads_api_key_from_hermes_config_when_agent_config_empty(self):
        from llm_client import LLMClient

        tmp_home = tempfile.mkdtemp()
        hermes_dir = Path(tmp_home) / ".hermes"
        hermes_dir.mkdir()
        (hermes_dir / "config.yaml").write_text(
            "providers:\n"
            "  deepseek:\n"
            "    api_key: hermes-key-123\n",
            encoding="utf-8",
        )

        with tempfile.NamedTemporaryFile("w", suffix=".json") as cfg:
            cfg.write('{"llm": {"api_key": ""}}')
            cfg.flush()
            with patch.dict(os.environ, {}, clear=True), patch("pathlib.Path.home", return_value=Path(tmp_home)):
                client = LLMClient(config_path=cfg.name)

        self.assertEqual(client.api_key, "hermes-key-123")

    def test_loads_api_key_from_hermes_env_when_yaml_key_empty(self):
        from llm_client import LLMClient

        tmp_home = tempfile.mkdtemp()
        hermes_dir = Path(tmp_home) / ".hermes"
        hermes_dir.mkdir()
        (hermes_dir / "config.yaml").write_text("llm:\n  api_key: ''\n", encoding="utf-8")
        (hermes_dir / ".env").write_text("DEEPSEEK_API_KEY=env-key-456\n", encoding="utf-8")

        with tempfile.NamedTemporaryFile("w", suffix=".json") as cfg:
            cfg.write('{"llm": {"api_key": ""}}')
            cfg.flush()
            with patch.dict(os.environ, {}, clear=True), patch("pathlib.Path.home", return_value=Path(tmp_home)):
                client = LLMClient(config_path=cfg.name)

        self.assertEqual(client.api_key, "env-key-456")

    def test_call_falls_back_to_hermes_cli_when_http_response_empty(self):
        from llm_client import LLMClient

        client = LLMClient(api_key="test-key")

        calls = []

        def fake_run(args, **kwargs):
            calls.append(args)
            if args[0] == "curl":
                return subprocess.CompletedProcess(args, 0, stdout=b"", stderr=b"")
            return subprocess.CompletedProcess(args, 0, stdout="session_id: abc\n{\"ok\": true}\n", stderr="")

        with patch("subprocess.run", side_effect=fake_run):
            response = client.call("review_agent", "Return JSON", "ping", max_tokens=16)

        self.assertEqual(response, "{\"ok\": true}")
        self.assertEqual(calls[0][0], "curl")
        self.assertEqual(calls[1][:4], ["hermes", "chat", "-Q", "-q"])


if __name__ == "__main__":
    unittest.main()
