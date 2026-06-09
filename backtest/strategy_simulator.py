from __future__ import annotations

import copy
import math
from numbers import Real
from typing import Dict, Iterable, List

import pandas as pd


SUPPORTED_PARAMETER_FIELDS = {
    "breakout_lookback",
    "take_profit_pct",
    "stop_loss_pct",
    "trailing_stop_pct",
    "time_stop_days",
    "first_take_profit_pct",
    "first_take_profit_ratio",
    "trailing_stop_for_remaining",
    "max_single_position",
    "min_single_position",
    "base_position_target",
    "position_floor",
    "min_single_batch",
    "max_single_batch",
}

SUPPORTED_POSITION_SIZING_FIELDS = {
    "max_single_position",
    "min_single_position",
    "initial_position",
    "total_position_limit",
    "total_position_floor",
}


def _is_number(value) -> bool:
    return isinstance(value, Real) and not isinstance(value, bool)


def _field_name(path: str) -> str:
    return path.split(".")[-1]


def _is_parameter_path(path: str) -> bool:
    return path.startswith("parameters.") or ".parameters." in path


def _is_position_sizing_path(path: str) -> bool:
    return path.startswith("rules.position_sizing.") or ".rules.position_sizing." in path


def _is_supported_diff(item: Dict) -> bool:
    path = item.get("path", "")
    field = _field_name(path)
    if not _is_number(item.get("new")):
        return False
    if _is_parameter_path(path) and field in SUPPORTED_PARAMETER_FIELDS:
        return True
    if _is_position_sizing_path(path) and field in SUPPORTED_POSITION_SIZING_FIELDS:
        return True
    return False


def unsupported_diff_paths(diff: Iterable[Dict]) -> List[str]:
    unsupported = []
    for item in diff:
        if not _is_supported_diff(item):
            unsupported.append(item.get("path", ""))
    return unsupported


def apply_supported_diff(strategy: Dict, diff: Iterable[Dict]) -> Dict:
    updated = copy.deepcopy(strategy)
    updated.setdefault("parameters", {})
    updated.setdefault("rules", {}).setdefault("position_sizing", {})

    for item in diff:
        path = item.get("path", "")
        field = _field_name(path)
        if _is_parameter_path(path):
            updated["parameters"][field] = item["new"]
        elif _is_position_sizing_path(path):
            updated["rules"]["position_sizing"][field] = item["new"]

    return updated


def code_to_ticker(code: str) -> str:
    if code.startswith(("5", "6")):
        return f"{code}.SS"
    return f"{code}.SZ"


def _watchlist_codes(watchlist: Dict, account: str) -> List[str]:
    rows = watchlist.get("stocks", [])
    account_tag = "main" if account == "main" else "lab"
    codes = []
    for row in rows:
        if row.get("status") == "stopped_out":
            continue
        if row.get("tag") in {account, account_tag} and row.get("code"):
            codes.append(row["code"])
    return codes[:8]


def _pct_to_decimal(value, default: float) -> float:
    numeric = float(value if value is not None else default)
    return numeric / 100 if numeric > 1 else numeric


def _position_weight(strategy: Dict) -> float:
    params = strategy.get("parameters", {})
    sizing = strategy.get("rules", {}).get("position_sizing", {})
    initial = float(sizing.get("initial_position", params.get("max_single_position", 0.10)))
    max_single = float(sizing.get("max_single_position", params.get("max_single_position", initial)))
    min_single = float(sizing.get("min_single_position", params.get("min_single_position", 0.0)))
    batch_max = float(params.get("max_single_batch", max_single))
    target = min(initial, max_single, batch_max)
    if target <= 0:
        return 0.0
    return max(target, min_single)


def _portfolio_limit(strategy: Dict) -> float:
    params = strategy.get("parameters", {})
    sizing = strategy.get("rules", {}).get("position_sizing", {})
    return float(sizing.get("total_position_limit", params.get("base_position_target", 1.0)))


def _metrics(equity: pd.Series) -> Dict:
    if equity.empty:
        return {"total_ret": 0.0, "sharpe": 0.0, "max_dd": 0.0}

    returns = equity.pct_change().dropna()
    total_ret = float(equity.iloc[-1] / equity.iloc[0] - 1)
    std = float(returns.std()) if len(returns) > 1 else 0.0
    sharpe = float(returns.mean() / std * math.sqrt(252)) if std > 0 else 0.0
    peak = equity.expanding().max()
    max_dd = float(((equity - peak) / peak).min())
    return {"total_ret": total_ret, "sharpe": sharpe, "max_dd": max_dd}


def simulate_strategy(strategy: Dict, watchlist: Dict, prices: pd.DataFrame, account: str) -> Dict:
    prices = prices.sort_index()
    params = strategy.get("parameters", {})
    capital = 1_000_000.0 if account == "main" else 300_000.0
    cash = capital
    positions = {}
    entry_prices = {}
    entry_days = {}
    peak_prices = {}
    equity = []

    breakout = max(1, int(params.get("breakout_lookback", 20)))
    take_profit = _pct_to_decimal(params.get("take_profit_pct"), 0.15)
    stop_loss = _pct_to_decimal(params.get("stop_loss_pct"), 0.07)
    trailing_stop = _pct_to_decimal(params.get("trailing_stop_pct"), 0.0)
    first_take_profit = _pct_to_decimal(params.get("first_take_profit_pct"), take_profit)
    first_take_profit_ratio = float(params.get("first_take_profit_ratio", 0.0))
    trailing_remaining = _pct_to_decimal(params.get("trailing_stop_for_remaining"), trailing_stop)
    time_stop = max(1, int(params.get("time_stop_days", 20)))
    target_weight = _position_weight(strategy)
    total_position_limit = max(0.0, _portfolio_limit(strategy))

    tickers = [
        ticker
        for ticker in (code_to_ticker(code) for code in _watchlist_codes(watchlist, account))
        if ticker in prices.columns
    ]

    for day_index, (_, row) in enumerate(prices.iterrows()):
        for ticker in list(positions):
            px = row.get(ticker)
            if pd.isna(px):
                continue
            peak_prices[ticker] = max(peak_prices[ticker], px)
            ret = px / entry_prices[ticker] - 1
            trail_ret = px / peak_prices[ticker] - 1
            held_days = day_index - entry_days[ticker]
            exit_position = (
                ret >= take_profit
                or ret <= -stop_loss
                or held_days >= time_stop
                or (trailing_stop > 0 and trail_ret <= -trailing_stop)
                or (
                    first_take_profit_ratio > 0
                    and ret >= first_take_profit
                    and trailing_remaining <= 0
                )
            )
            if exit_position:
                cash += positions.pop(ticker) * px
                entry_prices.pop(ticker, None)
                entry_days.pop(ticker, None)
                peak_prices.pop(ticker, None)

        invested_value = 0.0
        for ticker, shares in positions.items():
            px = row.get(ticker)
            invested_value += shares * (px if not pd.isna(px) else entry_prices[ticker])

        if day_index >= breakout and target_weight > 0:
            for ticker in tickers:
                if ticker in positions:
                    continue
                px = row.get(ticker)
                if pd.isna(px) or px <= 0:
                    continue
                recent_high = prices[ticker].iloc[day_index - breakout:day_index].max()
                if px <= recent_high:
                    continue
                budget = capital * target_weight
                if (invested_value + budget) / capital > total_position_limit:
                    continue
                if cash < budget:
                    continue
                shares = math.floor(budget / px / 100) * 100
                if shares <= 0:
                    continue
                positions[ticker] = shares
                entry_prices[ticker] = px
                entry_days[ticker] = day_index
                peak_prices[ticker] = px
                cash -= shares * px
                invested_value += shares * px

        total = cash
        for ticker, shares in positions.items():
            px = row.get(ticker)
            total += shares * (px if not pd.isna(px) else entry_prices[ticker])
        equity.append(total)

    equity_series = pd.Series(equity, index=prices.index)
    return {"equity": equity_series, "metrics": _metrics(equity_series)}


def build_oos_evidence(
    current_strategy: Dict,
    proposal: Dict,
    watchlist: Dict,
    prices: pd.DataFrame,
    window: Dict,
    data_meta: Dict | None = None,
) -> Dict:
    if window.get("status") != "OK":
        return {
            "status": "INFRA_ERROR",
            "reason": window.get("reason", "BAD_OOS_WINDOW"),
            "window": window,
        }

    diff = proposal.get("diff", [])
    unsupported = unsupported_diff_paths(diff)
    if unsupported:
        return {
            "status": "INFRA_ERROR",
            "reason": "UNSUPPORTED_STRATEGY_DIFF",
            "unsupported_paths": unsupported,
        }

    account = proposal.get("account", "main")
    proposed_strategy = apply_supported_diff(current_strategy, diff)
    current = simulate_strategy(current_strategy, watchlist, prices, account)
    proposed = simulate_strategy(proposed_strategy, watchlist, prices, account)
    return {
        "status": "OK",
        "window": {
            "start": window["start"],
            "end": window["end"],
            "trading_days": window["trading_days"],
        },
        "current": current["metrics"],
        "proposed": proposed["metrics"],
        "data": data_meta or {},
    }
