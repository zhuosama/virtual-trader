"""
本地 EOD 模拟撮合 + 记账。
与 A 股线完全隔离,零真实资金。
"""
import copy
import logging
import math
from typing import Dict, List, Tuple

from us_trader.pipeline.risk import portfolio_halted, update_watermark

logger = logging.getLogger(__name__)


def _commission(shares: float, config: dict) -> float:
    """计算手续费:max(commission_min, commission_per_share * shares)"""
    fill_cfg = config["fill"]
    per_share = fill_cfg["commission_per_share"]
    min_fee = fill_cfg["commission_min"]
    return max(min_fee, per_share * abs(shares))


def step(
    state: dict,
    target_codes: List[str],
    prices: Dict[str, float],
    exit_signals: Dict[str, dict],
    config: dict,
    date: str,
) -> Tuple[dict, List[dict]]:
    """
    执行单日 EOD 撮合。

    Args:
        state: {"cash", "positions": {code: {shares, cost, high_watermark}}, "nav_history": [...]}
        target_codes: 今日选股管线给出的目标持仓 codes(已排序)
        prices: {code: 收盘价}
        exit_signals: {code: {"action":"sell"|None, "reason":str}}(来自 risk.py)
        config: 配置
        date: YYYYMMDD 字符串

    Returns:
        (new_state, trades) where trades = [{date,code,side,shares,price,amount,commission,realized_pnl,reason}]
    """
    st = copy.deepcopy(state)
    trades: List[dict] = []

    holdings_n = config["holdings_n"]
    max_pos_pct = config["max_position_pct"]
    max_new_buys = config["max_new_buys_per_day"]
    turnover_block = config["turnover_block"]

    target_set = set(target_codes[:holdings_n])

    # ── 步骤 1:先执行 exit_signals 的 sell 与"跌出 target"的卖出 ──────────────
    codes_to_sell = []
    for code in list(st["positions"].keys()):
        sig = exit_signals.get(code, {})
        if sig.get("action") == "sell":
            codes_to_sell.append((code, sig.get("reason", "exit_signal")))
        elif code not in target_set:
            codes_to_sell.append((code, "dropped_from_target"))

    for code, reason in codes_to_sell:
        pos = st["positions"].get(code)
        if pos is None:
            continue
        price = prices.get(code)
        if price is None:
            logger.warning("simulate.step: no price for %s on %s, skip sell", code, date)
            continue

        shares = pos["shares"]
        cost = pos["cost"]
        commission = _commission(shares, config)
        amount = shares * price
        realized_pnl = (price - cost) * shares - commission

        st["cash"] += amount - commission
        del st["positions"][code]

        trades.append({
            "date": date,
            "code": code,
            "side": "sell",
            "shares": shares,
            "price": price,
            "amount": amount,
            "commission": commission,
            "realized_pnl": realized_pnl,
            "reason": reason,
        })

    # ── 步骤 2:更新 watermark ─────────────────────────────────────────────────
    st["positions"] = update_watermark(st["positions"], prices)

    # ── 步骤 3:若未熔断,买入新目标 ──────────────────────────────────────────
    halted = portfolio_halted(st["nav_history"], config)

    if not halted:
        # 计算换手量(用于 turnover_block 检查)
        current_nav = st["cash"] + sum(
            pos["shares"] * prices.get(code, pos["cost"])
            for code, pos in st["positions"].items()
        )
        if current_nav <= 0:
            current_nav = config["initial_capital"]

        new_buys = 0
        sell_volume = sum(t["amount"] for t in trades if t["side"] == "sell")

        # 候选买入:在 target 中但尚未持仓,且未触发 exit_signal(止损/止盈当日不重新买入)
        exited_today = {code for code, _ in codes_to_sell if exit_signals.get(code, {}).get("action") == "sell"}
        candidates = [
            c for c in target_codes[:holdings_n]
            if c not in st["positions"] and c not in exited_today
        ]

        for code in candidates:
            if new_buys >= max_new_buys:
                break

            price = prices.get(code)
            if price is None:
                logger.warning("simulate.step: no price for %s on %s, skip buy", code, date)
                continue

            # 等权目标金额
            target_value = current_nav / holdings_n
            # 单仓上限
            max_value = current_nav * max_pos_pct
            buy_value = min(target_value, max_value, st["cash"])
            buy_value = max(0.0, buy_value)

            shares = math.floor(buy_value / price)
            if shares <= 0:
                continue

            commission = _commission(shares, config)
            total_cost = shares * price + commission

            if total_cost > st["cash"]:
                # 重新计算能买多少股
                shares = math.floor((st["cash"] - commission) / price)
                if shares <= 0:
                    continue
                commission = _commission(shares, config)
                total_cost = shares * price + commission

            # 换手检查
            buy_volume = shares * price
            if (sell_volume + buy_volume) / current_nav > turnover_block:
                logger.warning("simulate.step: turnover_block triggered, skip buy %s", code)
                break

            st["cash"] -= total_cost
            st["positions"][code] = {
                "shares": shares,
                "cost": price,
                "high_watermark": price,
            }
            new_buys += 1
            sell_volume += buy_volume  # 累计换手

            trades.append({
                "date": date,
                "code": code,
                "side": "buy",
                "shares": shares,
                "price": price,
                "amount": shares * price,
                "commission": commission,
                "realized_pnl": 0.0,
                "reason": "new",
            })

    # ── 步骤 4:重算 NAV 并追加 nav_history ────────────────────────────────────
    nav = st["cash"] + sum(
        pos["shares"] * prices.get(code, pos["cost"])
        for code, pos in st["positions"].items()
    )

    prev = st["nav_history"][-1] if st["nav_history"] else None
    prev_nav = prev["nav"] if prev else nav
    cum_start = config["initial_capital"]

    ret = (nav / prev_nav - 1) if prev_nav else 0.0
    cum_ret = (nav / cum_start - 1) if cum_start else 0.0

    # 回撤:从历史峰值算
    all_navs = [h["nav"] for h in st["nav_history"]] + [nav]
    peak = max(all_navs)
    drawdown = (nav / peak - 1) if peak > 0 else 0.0

    st["nav_history"].append({
        "date": date,
        "nav": nav,
        "ret": ret,
        "cum_ret": cum_ret,
        "drawdown": drawdown,
    })

    return st, trades
