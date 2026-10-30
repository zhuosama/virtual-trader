"""sync_to_site failure alert defaults to a local file, not WeCom."""
import importlib.util
import os
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("sync_to_site", ROOT / "scripts" / "sync_to_site.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


FAIL = {"success": False, "steps": {"charts": True, "import": False},
        "errors": {"import": "boom"}, "timestamp": "2026-01-01T00:00:00"}


def test_default_local_no_subprocess(tmp_path, monkeypatch):
    m = _load()
    monkeypatch.delenv("VTRADER_NOTIFY_TARGET", raising=False)
    monkeypatch.setattr(m, "NOTIFY_LOCAL_DIR", str(tmp_path / "site_sync"))
    monkeypatch.setattr(m, "LOG_PATH", str(tmp_path / "site_sync.log"))
    with patch.object(m.subprocess, "run") as run:
        m._notify_wecom(FAIL)
        run.assert_not_called()
    files = list((tmp_path / "site_sync").glob("*.md"))
    assert len(files) == 1
    t = files[0].read_text()
    assert "[vtrader-sync] ❌ import" in t and "boom" in t


def test_success_writes_nothing(tmp_path, monkeypatch):
    m = _load()
    monkeypatch.setattr(m, "NOTIFY_LOCAL_DIR", str(tmp_path / "site_sync"))
    with patch.object(m.subprocess, "run") as run:
        m._notify_wecom({"success": True})
        run.assert_not_called()
    assert not (tmp_path / "site_sync").exists()


def test_explicit_wecom_keeps_hermes_send(tmp_path, monkeypatch):
    m = _load()
    monkeypatch.setenv("VTRADER_NOTIFY_TARGET", "wecom")
    monkeypatch.setattr(m, "NOTIFY_LOCAL_DIR", str(tmp_path / "site_sync"))
    monkeypatch.setattr(m, "LOG_PATH", str(tmp_path / "site_sync.log"))
    with patch.object(m.subprocess, "run") as run:
        m._notify_wecom(FAIL)
        args = run.call_args[0][0]
        assert args[1:4] == ["send", "--to", "wecom"]
    assert not (tmp_path / "site_sync").exists()
