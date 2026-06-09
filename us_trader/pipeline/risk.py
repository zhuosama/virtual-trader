"""
风控模块:止损 / 移动止盈 / 组合熔断。
参照 A 股 G3/G4 思路,独立实现,不 import A 股代码。
"""
import copy
import logging
from typing import Dict, List

logger = logging.getLogger(__name__)


def position_exit_signals(
    positions: Dict[str, dict],
    prices: Dict[str, float],
    config: dict,
) -> Dict[str, dict]:
    """
    逐仓位判断止损 / 移动止盈信号。

    Args:
        positions: {code: {shares, cost, high_watermark}}
        prices: {code: 当前价}
        config: 配置

    Returns:
        {code: {"action": "sell"|None, "reason": str}}
    """
    stop_loss_pct = config["stop_loss_pct"]                      # 例: -0.10
    arm_pct = config["take_profit_arm_pct"]                      # 例:  0.30
    giveback_pct = config["take_profit_giveback_pct"]            # 例:  0.10

    result = {}
    for code, pos in positions.items():
        price = prices.get(code)
        if price is None:
            result[code] = {"action": None, "reason": "no price"}
            continue

        cost = pos["cost"]
        hw = pos["high_watermark"]

        # 止损
        ret = (price - cost) / cost
        if ret <= stop_loss_pct:
            result[code] = {"action": "sell", "reason": "stop_loss"}
            continue

        # 移动止盈:先判断是否已 arm
        hw_ret = (hw - cost) / cost
        if hw_ret >= arm_pct:
            # 已 arm:判断自高点回落
            drawdown_from_hw = (hw - price) / hw
            if drawdown_from_hw >= giveback_pct:
                result[code] = {"action": "sell", "reason": "trailing_take_profit"}
                continue

        result[code] = {"action": None, "reason": ""}

    return result


def portfolio_halted(nav_history: List[dict], config: dict) -> bool:
    """
    判断组合是否触发熔断:当前净值从峰值回撤 >= portfolio_dd_halt_pct。
    nav_history: [{"nav": float, ...}](时序,最后一项为当前)。
    """
    if not nav_history:
        return False

    halt_pct = config["portfolio_dd_halt_pct"]  # 例: -0.20

    navs = [r["nav"] for r in nav_history]
    peak = max(navs)
    current = navs[-1]

    if peak <= 0:
        return False

    drawdown = (current - peak) / peak  # 负数
    return drawdown <= halt_pct


def update_watermark(
    positions: Dict[str, dict],
    prices: Dict[str, float],
) -> Dict[str, dict]:
    """
    更新每个持仓的 high_watermark:取 max(high_watermark, current_price)。
    纯函数,返回新 dict(不修改原 positions)。
    """
    new_positions = copy.deepcopy(positions)
    for code, pos in new_positions.items():
        price = prices.get(code)
        if price is not None:
            pos["high_watermark"] = max(pos.get("high_watermark", price), price)
    return new_positions
