"""Default delivery is local: no hermes send / WeCom; file is written."""
from unittest.mock import patch, MagicMock

from us_trader.pipeline import notify


def test_default_writes_local_and_never_calls_subprocess(tmp_path, monkeypatch):
    monkeypatch.setenv("VTRADER_HOME", str(tmp_path))
    monkeypatch.delenv("VTRADER_NOTIFY_TARGET", raising=False)
    with patch.object(notify.subprocess, "run") as run:
        assert notify.send_notify("subj", "hello body", {}) is True
        run.assert_not_called()
    files = list((tmp_path / "logs" / "notify" / "us_trader").glob("*.md"))
    assert len(files) == 1
    text = files[0].read_text()
    assert "# subj" in text and "hello body" in text


def test_shipped_config_is_local(tmp_path, monkeypatch):
    from us_trader.config import load_config
    monkeypatch.setenv("VTRADER_HOME", str(tmp_path))
    monkeypatch.delenv("VTRADER_NOTIFY_TARGET", raising=False)
    cfg = load_config()
    with patch.object(notify.subprocess, "run") as run:
        assert notify.send_notify("s", "b", cfg) is True
        run.assert_not_called()


def test_explicit_wecom_still_uses_hermes_send(tmp_path, monkeypatch):
    monkeypatch.setenv("VTRADER_HOME", str(tmp_path))
    monkeypatch.delenv("VTRADER_NOTIFY_TARGET", raising=False)
    with patch.object(notify.subprocess, "run",
                      return_value=MagicMock(returncode=0, stderr="")) as run:
        assert notify.send_notify("s", "b", {"notify_target": "wecom"}) is True
        args = run.call_args[0][0]
        assert args[1:4] == ["send", "--to", "wecom"]
    assert not (tmp_path / "logs" / "notify").exists()


def test_env_overrides_config(tmp_path, monkeypatch):
    monkeypatch.setenv("VTRADER_HOME", str(tmp_path))
    monkeypatch.setenv("VTRADER_NOTIFY_TARGET", "local")
    with patch.object(notify.subprocess, "run") as run:
        assert notify.send_notify("s", "b", {"notify_target": "wecom"}) is True
        run.assert_not_called()
