from unittest.mock import patch, MagicMock
from us_trader.pipeline import fetch_fundamentals as ff


def test_symbol_mapping():
    assert ff.to_yahoo_symbol("AAPL.O") == "AAPL"
    assert ff.to_yahoo_symbol("BRK.A") == "BRK.A"   # 不误删非交易所后缀


def test_fetch_handles_missing():
    fake = MagicMock()
    fake.summary_detail = {
        "AAPL": {"marketCap": 3.0e12},
        "BAD": "Quote not found"     # yahooquery 缺失返回 str
    }
    with patch.object(ff, "_ticker", return_value=fake), \
         patch.object(ff, "_growth", return_value={"AAPL": (0.2, 0.1), "BAD": (None, None)}):
        out = ff.fetch_fundamentals(["AAPL.O", "BAD"])
    assert out["AAPL"]["market_cap"] == 3.0e12
    assert out["AAPL"]["rev_yoy"] == 0.2
    assert out["BAD"]["market_cap"] is None
