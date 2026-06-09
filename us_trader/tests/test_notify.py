from unittest.mock import patch, MagicMock
from us_trader.pipeline import notify


def test_digest_has_sections():
    st = {
        "cash": 50000,
        "positions": {"A": {"shares": 100, "cost": 10, "high_watermark": 12}},
        "nav_history": [
            {"nav": 100000, "ret": 0, "cum_ret": 0, "drawdown": 0},
            {"nav": 101000, "ret": 0.01, "cum_ret": 0.01, "drawdown": 0},
        ]
    }
    md = notify.build_digest(
        st,
        [{"date": "20260602", "code": "A", "side": "buy", "shares": 100,
          "price": 10, "reason": "new"}],
        [{"ts_code": "A", "momentum_score": 0.3, "passed": True}],
        {"success": True},
        "20260602"
    )
    for kw in ["复盘", "持仓", "变动", "选股", "风险"]:
        assert kw in md, f"Missing section keyword: {kw}"


def test_send_checks_returncode():
    with patch.object(notify.subprocess, "run",
                      return_value=MagicMock(returncode=1, stderr="x")):
        assert notify.send_weixin("s", "b", {"weixin_target": "weixin"}) is False
