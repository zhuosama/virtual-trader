import pandas as pd
from unittest.mock import patch, MagicMock
from us_trader.pipeline import fetch_prices as fp


def _fake_pro():
    pro = MagicMock()

    def query(api, **kw):
        if api == "us_tradecal":
            return pd.DataFrame({
                "cal_date": ["20260601", "20260602", "20260603"],
                "is_open": [1, 0, 1]
            })
        if api == "us_daily":
            return pd.DataFrame({
                "trade_date": ["20260601", "20260603"],
                "close": [10.0, 11.0],
                "ts_code": [kw["ts_code"]] * 2
            })
        return pd.DataFrame()

    pro.query.side_effect = query
    return pro


def test_trading_days_filters_open():
    with patch.object(fp, "_pro", return_value=_fake_pro()):
        days = fp.get_trading_days("20260603", 5)
    assert days == ["20260601", "20260603"]  # 02 闭市被滤掉


def test_price_panel_shape():
    with patch.object(fp, "_pro", return_value=_fake_pro()):
        panel = fp.fetch_price_panel(["AAPL", "MSFT"], "20260601", "20260603")
    assert list(panel.columns) == ["AAPL", "MSFT"]
    assert panel.loc["20260603", "AAPL"] == 11.0
