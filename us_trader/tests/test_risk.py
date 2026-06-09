from us_trader.pipeline.risk import position_exit_signals, portfolio_halted, update_watermark
from us_trader.config import load_config

c = load_config()


def test_stop_loss():
    pos = {"A": {"shares": 100, "cost": 10, "high_watermark": 10}}
    sig = position_exit_signals(pos, {"A": 8.9}, c)   # -11% < -10%
    assert sig["A"]["action"] == "sell" and sig["A"]["reason"] == "stop_loss"


def test_trailing_tp():
    pos = {"A": {"shares": 100, "cost": 10, "high_watermark": 14}}  # 曾 +40% 已 arm(>30%)
    sig = position_exit_signals(pos, {"A": 12.5}, c)  # 自 14 回落 ~10.7% > 10%
    assert sig["A"]["action"] == "sell" and sig["A"]["reason"] == "trailing_take_profit"


def test_tp_not_armed():
    pos = {"A": {"shares": 100, "cost": 10, "high_watermark": 11}}  # 仅 +10%,未 arm
    assert position_exit_signals(pos, {"A": 10.2}, c)["A"]["action"] is None


def test_portfolio_halt():
    assert portfolio_halted([{"nav": 100}, {"nav": 120}, {"nav": 95}], c) is True   # 自 120 回撤 -20.8%
    assert portfolio_halted([{"nav": 100}, {"nav": 110}], c) is False
