from unittest.mock import patch
from us_trader.pipeline import run_daily as rd


def test_happy_path_writes_health(tmp_path, monkeypatch):
    monkeypatch.setattr(rd, "STATE_DIR", str(tmp_path))
    with patch.object(rd.fetch_prices, "is_trading_day", return_value=True), \
         patch.object(rd.fetch_prices, "get_trading_days",
                      return_value=["20260101", "20260605"]), \
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
         patch.object(rd.fetch_prices, "get_trading_days",
                      return_value=["20260101", "20260605"]), \
         patch.object(rd.fetch_prices, "fetch_price_panel",
                      side_effect=RuntimeError("net")), \
         patch.object(rd.notify, "send_weixin", return_value=True) as snd:
        rd.run_daily("20260605")
    import json
    import os
    h = json.load(open(os.path.join(str(tmp_path), "health.json")))
    assert h["success"] is False and h["failed_step"] == "fetch_prices"
    assert any("❌" in c.args[0] for c in snd.call_args_list)   # 发了告警


def test_state_roundtrip_preserves_cash(tmp_path, monkeypatch):
    """回归:cash 必须显式持久化,重载后等于存盘值,
    不能用 nav-Σ(shares*cost) 反推(那会被未实现盈亏虚增)。"""
    from us_trader.config import load_config
    monkeypatch.setattr(rd, "STATE_DIR", str(tmp_path))
    cfg = load_config()
    # 现价(15) != 成本(10),持仓有未实现浮盈
    state = {
        "cash": 37000.0,
        "positions": {"AAPL": {"shares": 100, "cost": 10.0, "high_watermark": 15.0}},
        "nav_history": [{"date": "20260601", "nav": 37000.0 + 100 * 15.0,
                         "ret": 0.0, "cum_ret": 0.0, "drawdown": 0.0}],
    }
    rd._save_state(state, [], "20260601")
    loaded = rd._load_state(cfg)
    assert loaded["cash"] == 37000.0          # 不是 37500(= nav - shares*cost)
    assert loaded["positions"]["AAPL"]["shares"] == 100
