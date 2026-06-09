from us_trader.pipeline.simulate import step
from us_trader.config import load_config

c = load_config()
c["holdings_n"] = 2
c["max_new_buys_per_day"] = 5


def test_buy_then_stop_loss_sell():
    st = {"cash": 100000, "positions": {}, "nav_history": []}
    st, tr = step(st, ["A", "B"], {"A": 10, "B": 20}, {}, c, "20260601")
    assert set(st["positions"]) == {"A", "B"}
    # 单仓不超 18%
    assert st["positions"]["A"]["shares"] * 10 <= 100000 * 0.18 + 1
    # 次日 A 触发止损卖出
    st, tr = step(
        st, ["A", "B"], {"A": 8.5, "B": 20},
        {"A": {"action": "sell", "reason": "stop_loss"}},
        c, "20260602"
    )
    assert "A" not in st["positions"]
    assert any(t["side"] == "sell" and t["reason"] == "stop_loss" for t in tr)


def test_nav_recorded():
    st = {"cash": 100000, "positions": {}, "nav_history": []}
    st, _ = step(st, ["A"], {"A": 10}, {}, c, "20260601")
    assert abs(
        st["nav_history"][-1]["nav"] - (st["cash"] + st["positions"]["A"]["shares"] * 10)
    ) < 1e-6
