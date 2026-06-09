from unittest.mock import patch
from us_trader.pipeline import run_daily as rd


def test_happy_path_writes_health(tmp_path, monkeypatch):
    monkeypatch.setattr(rd, "STATE_DIR", str(tmp_path))
    with patch.object(rd.fetch_prices, "is_trading_day", return_value=True), \
         patch.object(rd.fetch_prices, "fetch_price_panel"), \
         patch.object(rd.fetch_fundamentals, "fetch_fundamentals", return_value={}), \
         patch.object(rd, "_select", return_value=[]), \
         patch.object(rd.notify, "send_weixin", return_value=True) as snd:
        out = rd.run_daily("20260605")
    import json
    import os
    h = json.load(open(os.path.join(str(tmp_path), "health.json")))
    assert h["success"] is True


def test_fetch_failure_alerts(tmp_path, monkeypatch):
    monkeypatch.setattr(rd, "STATE_DIR", str(tmp_path))
    with patch.object(rd.fetch_prices, "is_trading_day", return_value=True), \
         patch.object(rd.fetch_prices, "fetch_price_panel",
                      side_effect=RuntimeError("net")), \
         patch.object(rd.notify, "send_weixin", return_value=True) as snd:
        rd.run_daily("20260605")
    import json
    import os
    h = json.load(open(os.path.join(str(tmp_path), "health.json")))
    assert h["success"] is False and h["failed_step"] == "fetch_prices"
    assert any("❌" in c.args[0] for c in snd.call_args_list)   # 发了告警
