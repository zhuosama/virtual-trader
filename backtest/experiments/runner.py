#!/usr/bin/env python3
"""实验框架 — 策略变体对比引擎

用法:
  python3 backtest/experiments/runner.py --baseline  # 生成基线指标
  python3 backtest/experiments/runner.py --run win_rate  # 运行win-rate实验组
  python3 backtest/experiments/runner.py --compare   # 对比所有已运行实验
  python3 backtest/experiments/runner.py --report    # 生成最终报告
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import os
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd
import yfinance as yf

VT_DIR = os.path.expanduser("~/.hermes/virtual-trader")
RUNS_DIR = os.path.join(VT_DIR, "backtest", "runs")
EXPERIMENTS_DIR = os.path.join(VT_DIR, "backtest", "experiments")

# Add backtest dir for imports
sys.path.insert(0, os.path.join(VT_DIR, "backtest"))
from strategy_simulator import (
    simulate_strategy,
    apply_supported_diff,
    unsupported_diff_paths,
    _metrics,
)

# ---- Data loading ----
def load_active_strategy() -> Dict:
    path = os.path.join(VT_DIR, "strategies", "active.json")
    with open(path) as f:
        return json.load(f)

def load_watchlist() -> Dict:
    path = os.path.join(VT_DIR, "market-data", "watchlist.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {"stocks": []}

def load_performance_history() -> List[Dict]:
    path = os.path.join(VT_DIR, "strategies", "performance_history.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return []

def get_trading_dates() -> List[str]:
    """Get all trading dates from performance history."""
    perf = load_performance_history()
    return sorted([p["date"] for p in perf])


class ExperimentRunner:
    """Run strategy variant experiments and compare results."""

    def __init__(self):
        self.active = load_active_strategy()
        self.watchlist = load_watchlist()
        self.baseline_metrics = self._load_baseline()

    def _load_baseline(self) -> Optional[Dict]:
        path = os.path.join(RUNS_DIR, "baseline.json")
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
        return None

    def run_baseline(self, save: bool = True):
        """Run baseline simulation for both accounts and save."""
        print("=" * 60)
        print("  运行基线策略模拟")
        print("=" * 60)

        dates = get_trading_dates()
        if len(dates) < 2:
            print("  ERROR: 需要至少2个交易日数据")
            sys.exit(1)

        start = (datetime.strptime(dates[0], "%Y-%m-%d") - timedelta(days=30)).strftime("%Y-%m-%d")
        end = (datetime.strptime(dates[-1], "%Y-%m-%d") + timedelta(days=3)).strftime("%Y-%m-%d")

        results = {}
        for account in ["main", "lab"]:
            strat_key = f"{account}_strategy"
            strategy = self.active[strat_key]
            tickers = self._collect_tickers(strategy, account)
            print(f"  获取 {len(tickers)} 只标的价格数据 ({account})...")
            prices = self._fetch_prices(tickers, start, end)
            print(f"  模拟 {account} 策略...")
            sim = simulate_strategy(strategy, self.watchlist, prices, account)
            results[account] = {
                "name": strategy["name"],
                "version": strategy["version"],
                "metrics": sim["metrics"],
                "n_days": len(prices),
            }
            print(f"    累计收益: {sim['metrics']['total_ret']*100:+.2f}%")
            print(f"    夏普比率: {sim['metrics']['sharpe']:+.2f}")

        if save:
            os.makedirs(RUNS_DIR, exist_ok=True)
            with open(os.path.join(RUNS_DIR, "baseline.json"), "w") as f:
                json.dump(results, f, indent=2, ensure_ascii=False)
            print(f"  基线已保存: {os.path.join(RUNS_DIR, 'baseline.json')}")

        return results

    def _collect_tickers(self, strategy: Dict, account: str) -> List[str]:
        """Collect yfinance tickers needed for simulation."""
        from strategy_simulator import code_to_ticker, _watchlist_codes
        codes = _watchlist_codes(self.watchlist, account)
        tickers = [code_to_ticker(c) for c in codes]
        tickers.append("000300.SS")
        return tickers

    def _fetch_prices(self, tickers: List[str], start: str, end: str,
                      provider: Optional[MarketDataProvider] = None) -> pd.DataFrame:
        """Download prices, with fallback."""
        if provider:
            result = provider.get_close_prices(tickers, start, end)
            if result.status == "OK":
                return result.prices
        # Fallback to yfinance
        data = yf.download(tickers, start=start, end=end, progress=False)
        prices = data.get("Close", data)
        if isinstance(prices, pd.Series):
            prices = prices.to_frame(tickers[0])
        return prices

    def run_experiment(self, name: str, variants: Dict[str, Dict],
                       account: str = "main", save: bool = True):
        """Run a set of variants against a baseline.

        variants = {
            "variant_name": {"diff": [...], "hooks": [...]},
            ...
        }
        """
        strat_key = f"{account}_strategy"
        base_strategy = self.active[strat_key]

        dates = get_trading_dates()
        start = (datetime.strptime(dates[0], "%Y-%m-%d") - timedelta(days=30)).strftime("%Y-%m-%d")
        end = (datetime.strptime(dates[-1], "%Y-%m-%d") + timedelta(days=3)).strftime("%Y-%m-%d")
        tickers = self._collect_tickers(base_strategy, account)
        prices = self._fetch_prices(tickers, start, end)

        # Baseline
        print(f"  [{account}] 基线: {base_strategy['name']}")
        base_sim = simulate_strategy(base_strategy, self.watchlist, prices, account)
        base_metrics = base_sim["metrics"]

        results = {
            "experiment": name,
            "account": account,
            "baseline": {
                "name": base_strategy["name"],
                "metrics": base_metrics,
            },
            "variants": {},
        }

        for var_name, var_config in variants.items():
            print(f"  [{account}] 变体: {var_name}")
            diff = var_config.get("diff", [])
            unsupported = unsupported_diff_paths(diff)
            if unsupported:
                print(f"    WARNING: 不支持的diff路径: {unsupported}")
                results["variants"][var_name] = {
                    "error": f"UNSUPPORTED_DIFF: {unsupported}"
                }
                continue

            variant_strategy = apply_supported_diff(base_strategy, diff)
            hooks = var_config.get("hooks", [])

            if hooks:
                sim = simulate_strategy_with_hooks(
                    variant_strategy, self.watchlist, prices, account, hooks
                )
            else:
                sim = simulate_strategy(variant_strategy, self.watchlist, prices, account)

            var_metrics = sim["metrics"]
            delta = {
                k: round(var_metrics[k] - base_metrics[k], 6)
                for k in base_metrics if k in var_metrics
            }
            results["variants"][var_name] = {
                "metrics": var_metrics,
                "delta": delta,
                "n_days": len(prices),
            }
            for k, v in var_metrics.items():
                print(f"    {k}: {v*100 if k in ('total_ret','max_dd') else v:+.4f}"
                      f" (Δ{delta.get(k, 0)*100 if k in ('total_ret','max_dd') else delta.get(k, 0):+.4f})")

        if save:
            os.makedirs(RUNS_DIR, exist_ok=True)
            out_path = os.path.join(RUNS_DIR, f"experiment_{name}.json")
            with open(out_path, "w") as f:
                json.dump(results, f, indent=2, ensure_ascii=False, default=str)
            print(f"  实验已保存: {out_path}")

        return results

    def compare_all(self) -> Dict:
        """Load all experiment runs and produce comparison."""
        runs = {}
        for fname in sorted(os.listdir(RUNS_DIR)):
            if fname.endswith(".json") and fname.startswith("experiment_"):
                path = os.path.join(RUNS_DIR, fname)
                with open(path) as f:
                    runs[fname.replace(".json", "")] = json.load(f)

        return runs

    def generate_report(self, output_path: Optional[str] = None):
        """Generate comprehensive comparison report."""
        runs = self.compare_all()
        baseline = self._load_baseline()
        if not baseline:
            print("  未找到基线数据，先运行 --baseline")
            return

        lines = []
        lines.append("# 策略实验对比报告")
        lines.append(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        lines.append("")

        # Baseline summary
        lines.append("## 基线策略 (active.json)")
        lines.append("")
        lines.append("| 账户 | 策略 | 累计收益 | 夏普比率 | 最大回撤 |")
        lines.append("|------|------|---------|---------|---------|")
        for acc in ["main", "lab"]:
            if acc in baseline:
                m = baseline[acc]["metrics"]
                lines.append(
                    f"| {acc} | {baseline[acc]['name']} v{baseline[acc]['version']} "
                    f"| {m['total_ret']*100:+.2f}% | {m['sharpe']:+.2f} | {m['max_dd']*100:+.2f}% |"
                )
        lines.append("")

        # Experiments
        for exp_name, exp_data in runs.items():
            lines.append(f"## 实验: {exp_data.get('experiment', exp_name)}")
            lines.append(f"**账户**: {exp_data.get('account', 'unknown')}")
            lines.append("")

            base_m = exp_data["baseline"]["metrics"]
            lines.append("| 变体 | 累计收益 | 夏普比率 | 最大回撤 | Δ收益 | Δ夏普 | Δ回撤 |")
            lines.append("|------|---------|---------|---------|------|------|------|")
            for var_name, var_data in exp_data.get("variants", {}).items():
                if "error" in var_data:
                    lines.append(f"| {var_name} | ❌ {var_data['error']} | | | | | |")
                    continue
                m = var_data["metrics"]
                d = var_data["delta"]
                lines.append(
                    f"| {var_name} "
                    f"| {m['total_ret']*100:+.2f}% "
                    f"| {m['sharpe']:+.2f} "
                    f"| {m['max_dd']*100:+.2f}% "
                    f"| {d['total_ret']*100:+.2f}% "
                    f"| {d['sharpe']:+.2f} "
                    f"| {d['max_dd']*100:+.2f}% |"
                )
            lines.append("")

        # Recommendations
        lines.append("## 推荐")
        lines.append("")
        best_main = self._find_best(runs, "main")
        best_lab = self._find_best(runs, "lab")
        if best_main:
            lines.append(f"- **主账户推荐**: {best_main}")
        if best_lab:
            lines.append(f"- **实验账户推荐**: {best_lab}")

        report = "\n".join(lines)
        if output_path:
            with open(output_path, "w") as f:
                f.write(report)
            print(f"  报告已保存: {output_path}")
        else:
            path = os.path.join(VT_DIR, "reports", f"experiment_comparison_{datetime.now().strftime('%Y%m%d')}.md")
            with open(path, "w") as f:
                f.write(report)
            print(f"  报告已保存: {path}")

        print(report)
        return report

    def _find_best(self, runs: Dict, account: str) -> Optional[str]:
        """Find best variant by total return for given account."""
        best_return = -float("inf")
        best_name = None
        for exp_name, exp_data in runs.items():
            if exp_data.get("account") != account:
                continue
            for var_name, var_data in exp_data.get("variants", {}).items():
                if "error" in var_data:
                    continue
                ret = var_data["metrics"]["total_ret"]
                if ret > best_return:
                    best_return = ret
                    best_name = f"{var_name} ({ret*100:+.2f}%)"
        return best_name


# ---- Hook-based strategy simulation ----
def simulate_strategy_with_hooks(strategy: Dict, watchlist: Dict,
                                  prices: pd.DataFrame, account: str,
                                  hooks: List[str]) -> Dict:
    """Strategy simulation with optional hook layers.

    Supported hooks:
      - "market_regime_filter": Only trade when HS300 > MA20
      - "signal_confirmation": Require 2 confirmations for entry
      - "trade_quality_score": Filter trades by quality score > 0.6
      - "volatility_stop": Use ATR-based dynamic stop loss
      - "cooldown_after_loss": Wait 3 days after a failed trade
      - "hs300_above_ma60": HS300 above MA60 only
    """
    from strategy_simulator import (
        code_to_ticker, _watchlist_codes, _pct_to_decimal,
        _position_weight, _portfolio_limit,
    )

    prices = prices.sort_index()
    params = strategy.get("parameters", {})
    capital = 1_000_000.0 if account == "main" else 300_000.0
    cash = capital
    positions = {}
    entry_prices = {}
    entry_days = {}
    peak_prices = {}
    equity = []

    # Track for hooks
    last_trade_result = None  # "win" or "lose"
    last_trade_day = -999
    cooldown_days = 3

    breakout = max(1, int(params.get("breakout_lookback", 20)))
    take_profit = _pct_to_decimal(params.get("take_profit_pct"), 0.15)
    stop_loss = _pct_to_decimal(params.get("stop_loss_pct"), 0.07)
    trailing_stop = _pct_to_decimal(params.get("trailing_stop_pct"), 0.0)
    time_stop = max(1, int(params.get("time_stop_days", 20)))
    target_weight = _position_weight(strategy)
    total_position_limit = max(0.0, _portfolio_limit(strategy))

    tickers = [
        t for t in (code_to_ticker(c) for c in _watchlist_codes(watchlist, account))
        if t in prices.columns
    ]

    # Compute market regime indicators if needed
    hs300_col = "000300.SS"
    hs300_ma20 = None
    hs300_ma60 = None
    atr_values = None

    use_regime = "market_regime_filter" in hooks or "hs300_above_ma60" in hooks
    use_atr = "volatility_stop" in hooks
    use_cooldown = "cooldown_after_loss" in hooks
    use_quality = "trade_quality_score" in hooks
    use_confirm = "signal_confirmation" in hooks

    if use_regime and hs300_col in prices.columns:
        hs300_series = prices[hs300_col]
        hs300_ma20 = hs300_series.rolling(20).mean()
        hs300_ma60 = hs300_series.rolling(60).mean()

    if use_atr and tickers:
        atr_values = {}
        for t in tickers:
            if t in prices.columns:
                high = prices[t].rolling(14).max()
                low = prices[t].rolling(14).min()
                atr_values[t] = (high - low) / prices[t]  # normalized ATR

    for day_index, (_, row) in enumerate(prices.iterrows()):
        # Check market regime
        regime_ok = True
        if use_regime and hs300_ma20 is not None and hs300_ma60 is not None:
            hs300_val = row.get(hs300_col, 0)
            ma20_val = hs300_ma20.iloc[day_index] if day_index < len(hs300_ma20) else 0
            ma60_val = hs300_ma60.iloc[day_index] if day_index < len(hs300_ma60) else 0

            if ("market_regime_filter" in hooks and
                not (pd.notna(ma20_val) and hs300_val > ma20_val)):
                regime_ok = False
            if ("hs300_above_ma60" in hooks and
                not (pd.notna(ma60_val) and hs300_val > ma60_val)):
                regime_ok = False

        # Cooldown check
        cooldown_active = False
        if use_cooldown and last_trade_result == "lose":
            if day_index - last_trade_day < cooldown_days:
                cooldown_active = True

        # Exit positions
        for ticker in list(positions):
            px = row.get(ticker)
            if pd.isna(px):
                continue
            peak_prices[ticker] = max(peak_prices[ticker], px)
            ret = px / entry_prices[ticker] - 1
            trail_ret = px / peak_prices[ticker] - 1
            held_days = day_index - entry_days[ticker]

            # Volatility-adjusted stop: dynamic stop = stop_loss * (1 + normalize_ATR)
            dynamic_stop = stop_loss
            if use_atr and atr_values and ticker in atr_values:
                atr_val = atr_values[ticker].iloc[day_index]
                if pd.notna(atr_val) and atr_val > 0:
                    # Higher ATR = wider stop (up to 2x)
                    atr_multiplier = min(1 + atr_val * 5, 2.0)
                    dynamic_stop = stop_loss * atr_multiplier

            exit_position = (
                ret >= take_profit
                or ret <= -dynamic_stop
                or held_days >= time_stop
                or (trailing_stop > 0 and trail_ret <= -trailing_stop)
            )

            if exit_position:
                exit_value = positions.pop(ticker) * px
                cash += exit_value
                entry_value = entry_prices[ticker] * positions.get(ticker, 0)
                result = "win" if ret > 0 else "lose"
                last_trade_result = result
                last_trade_day = day_index
                entry_prices.pop(ticker, None)
                entry_days.pop(ticker, None)
                peak_prices.pop(ticker, None)

        # Compute invested value
        invested_value = 0.0
        for ticker, shares in positions.items():
            px = row.get(ticker)
            invested_value += shares * (px if not pd.isna(px) else entry_prices.get(ticker, 0))

        # Entry logic
        if day_index >= breakout and target_weight > 0 and regime_ok and not cooldown_active:
            candidates = []
            for ticker in tickers:
                if ticker in positions:
                    continue
                px = row.get(ticker)
                if pd.isna(px) or px <= 0:
                    continue
                recent_high = prices[ticker].iloc[max(0, day_index - breakout):day_index].max()
                if px <= recent_high:
                    continue

                # Signal confirmation: require MA crossover or volume spike
                if use_confirm:
                    ma_short = prices[ticker].iloc[max(0, day_index - 5):day_index+1].mean()
                    ma_long = prices[ticker].iloc[max(0, day_index - 10):day_index+1].mean()
                    if ma_short <= ma_long:
                        continue  # no confirmation

                # Trade quality score
                if use_quality:
                    score = _trade_quality_score(prices, ticker, day_index, breakout)
                    if score < 0.6:
                        continue

                budget = capital * target_weight
                if (invested_value + budget) / capital > total_position_limit:
                    continue
                if cash < budget:
                    continue
                shares = math.floor(budget / px / 100) * 100
                if shares <= 0:
                    continue

                # Store candidate with quality score
                quality = _trade_quality_score(prices, ticker, day_index, breakout) if use_quality else 1.0
                candidates.append((quality, ticker, px, shares))

            # Sort by quality score (highest first) and take top candidate
            if candidates:
                candidates.sort(key=lambda x: x[0], reverse=True)
                _, ticker, px, shares = candidates[0]
                positions[ticker] = shares
                entry_prices[ticker] = px
                entry_days[ticker] = day_index
                peak_prices[ticker] = px
                cash -= shares * px
                invested_value += shares * px

        # Daily equity
        total = cash
        for ticker, shares in positions.items():
            px = row.get(ticker)
            total += shares * (px if not pd.isna(px) else entry_prices.get(ticker, 0))
        equity.append(total)

    equity_series = pd.Series(equity, index=prices.index)
    return {"equity": equity_series, "metrics": _metrics(equity_series)}


def _trade_quality_score(prices: pd.DataFrame, ticker: str,
                          day_index: int, lookback: int) -> float:
    """Score a potential trade on multiple dimensions (0-1)."""
    if ticker not in prices.columns:
        return 0.0

    px_series = prices[ticker].iloc[max(0, day_index-lookback):day_index+1]
    if len(px_series) < 5:
        return 0.0

    current = px_series.iloc[-1]

    # Momentum: how far above recent average
    ma = px_series.mean()
    momentum = min((current / ma - 1) * 20, 1.0) if ma > 0 else 0

    # Consistency: how many recent days were up
    daily_returns = px_series.pct_change().dropna()
    up_days = (daily_returns > 0).sum()
    consistency = up_days / len(daily_returns) if len(daily_returns) > 0 else 0

    # Strength: current vs recent high
    recent_max = px_series.max()
    strength = current / recent_max if recent_max > 0 else 0

    # Volatility penalty
    if len(daily_returns) > 1:
        vol = daily_returns.std()
        vol_penalty = max(0, 1 - vol * 10)
    else:
        vol_penalty = 0.5

    score = 0.3 * momentum + 0.25 * consistency + 0.25 * strength + 0.2 * vol_penalty
    return min(max(score, 0), 1.0)


# ---- CLI ----
def main():
    parser = argparse.ArgumentParser(description="策略实验对比引擎")
    parser.add_argument("--baseline", action="store_true", help="生成基线指标")
    parser.add_argument("--run", type=str, help="运行实验组 (win_rate, value, etc.)")
    parser.add_argument("--account", type=str, default="main", choices=["main", "lab"])
    parser.add_argument("--compare", action="store_true", help="对比所有已运行实验")
    parser.add_argument("--report", action="store_true", help="生成完整对比报告")
    args = parser.parse_args()

    runner = ExperimentRunner()

    if args.baseline:
        runner.run_baseline()
    elif args.run:
        if args.run == "win_rate":
            from win_rate_variants import WIN_RATE_VARIANTS
            runner.run_experiment("win_rate", WIN_RATE_VARIANTS, args.account)
        elif args.run == "value":
            from value_variants import VALUE_VARIANTS
            runner.run_experiment("value", VALUE_VARIANTS, args.account)
        else:
            print(f"  未知实验组: {args.run}")
            sys.exit(1)
    elif args.compare:
        runs = runner.compare_all()
        print(json.dumps(runs, indent=2, ensure_ascii=False, default=str))
    elif args.report:
        runner.generate_report()
    else:
        # Default: run baseline then win_rate
        print("  运行基线...")
        runner.run_baseline()
        print()
        from win_rate_variants import WIN_RATE_VARIANTS
        runner.run_experiment("win_rate", WIN_RATE_VARIANTS, "main")
        print()
        runner.generate_report()


if __name__ == "__main__":
    main()
