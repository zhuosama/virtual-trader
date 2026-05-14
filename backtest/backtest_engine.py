#!/usr/bin/env python3
"""
A股虚拟盘回测引擎 v1.0
用法: python3 backtest_engine.py [--start YYYY-MM-DD] [--end YYYY-MM-DD] [--account main|lab|all]

功能:
  1. 基于实际交易记录回放策略绩效
  2. 对比策略 vs 沪深300基准
  3. 输出每日净值、已平仓明细、当前持仓快照
  4. 计算核心指标: 收益率/夏普/最大回撤/胜率/盈亏比
"""

import json, os, glob, sys, math, argparse
from datetime import datetime, timedelta

def ensure_deps():
    for pkg in ["yfinance", "pandas", "numpy"]:
        try:
            __import__(pkg)
        except ImportError:
            import subprocess
            subprocess.run([sys.executable, "-m", "pip", "install", pkg, "-q"], capture_output=True)

ensure_deps()
import yfinance as yf
import pandas as pd
import numpy as np

# ---- Constants ----
VT_DIR = os.path.expanduser("~/.hermes/virtual-trader")
TRADES_DIR = os.path.join(VT_DIR, "trades")
REPORTS_DIR = os.path.join(VT_DIR, "reports")
ACCOUNTS_DIR = os.path.join(VT_DIR, "accounts")

CODE_TO_TICKER = {
    "600900": "600900.SS", "600036": "600036.SS", "002415": "002415.SZ",
    "601318": "601318.SS", "000333": "000333.SZ", "600519": "600519.SS",
    "000651": "000651.SZ", "601088": "601088.SS", "000858": "000858.SZ",
    "601006": "601006.SS", "300750": "300750.SZ", "688256": "688256.SS",
    "688111": "688111.SS", "300274": "300274.SZ", "002475": "002475.SZ",
    "688981": "688981.SS", "601899": "601899.SS", "600028": "600028.SS",
    "601857": "601857.SS", "600030": "600030.SS", "000002": "000002.SZ",
    "601398": "601398.SS", "600276": "600276.SS", "603259": "603259.SS",
    "002371": "002371.SZ", "600309": "600309.SS", "601919": "601919.SS",
}
HS300_TICKER = "000300.SS"

# ---- Account Simulation ----
class SimAccount:
    def __init__(self, name, capital):
        self.name = name
        self.cash = capital
        self.initial = capital
        self.positions = {}
        self.trade_log = []
        self.realized_pnl = 0.0
        self.total_fees = 0.0
        self.win_count = 0
        self.lose_count = 0
        self.win_amount = 0.0
        self.lose_amount = 0.0

    def buy(self, date, code, name, price, shares):
        amount = price * shares
        commission = max(amount * 0.0003, 5)
        xfer = amount * 0.00002
        cost = amount + commission + xfer
        if cost > self.cash:
            shares = int(self.cash / (price * 1.00032))
            shares = (shares // 100) * 100
            if shares <= 0:
                return
            amount = price * shares
            commission = max(amount * 0.0003, 5)
            xfer = amount * 0.00002
            cost = amount + commission + xfer
        self.cash -= cost
        self.total_fees += commission + xfer
        if code in self.positions:
            p = self.positions[code]
            old_total = p["shares"] * p["avg_cost"]
            p["shares"] += shares
            p["avg_cost"] = (old_total + amount) / p["shares"]
        else:
            self.positions[code] = {
                "shares": shares, "avg_cost": price,
                "name": name, "buy_date": date
            }
        self.trade_log.append({
            "date": date, "action": "buy", "code": code,
            "name": name, "price": price, "shares": shares, "amount": amount
        })

    def sell(self, date, code, price, shares):
        if code not in self.positions:
            return
        p = self.positions[code]
        sell_n = min(shares, p["shares"])
        amount = price * sell_n
        commission = max(amount * 0.0003, 5)
        tax = amount * 0.001
        xfer = amount * 0.00002
        net = amount - commission - tax - xfer
        self.cash += net
        self.total_fees += commission + tax + xfer
        pnl = net - p["avg_cost"] * sell_n
        self.realized_pnl += pnl
        pos_name = p.get("name", code)
        if pnl > 0:
            self.win_count += 1
            self.win_amount += pnl
        else:
            self.lose_count += 1
            self.lose_amount += abs(pnl)
        p["shares"] -= sell_n
        if p["shares"] <= 0:
            del self.positions[code]
        self.trade_log.append({
            "date": date, "action": "sell", "code": code,
            "name": pos_name, "price": price, "shares": sell_n,
            "amount": amount, "pnl": pnl, "cost": p.get("avg_cost", price)
        })

    def market_value(self, day_prices):
        mv = 0.0
        for code, pos in self.positions.items():
            ticker = CODE_TO_TICKER.get(code)
            if ticker and ticker in day_prices.index:
                px = day_prices[ticker]
                if not math.isnan(px):
                    mv += px * pos["shares"]
                else:
                    mv += pos["avg_cost"] * pos["shares"]
            else:
                mv += pos["avg_cost"] * pos["shares"]
        return mv

    def total_value(self, day_prices):
        return self.cash + self.market_value(day_prices)


# ---- Data Loading ----
def load_trade_files(start_date=None, end_date=None):
    """Load all trade JSON files, optionally filtered by date range."""
    trades_by_date = {}
    for month_dir in sorted(glob.glob(os.path.join(TRADES_DIR, "*"))):
        if not os.path.isdir(month_dir):
            continue
        for f in sorted(glob.glob(os.path.join(month_dir, "*.json"))):
            with open(f) as fh:
                d = json.load(fh)
                date = d["date"]
                if start_date and date < start_date:
                    continue
                if end_date and date > end_date:
                    continue
                trades_by_date[date] = d.get("trades", [])
    return trades_by_date


def fetch_prices(tickers, start, end):
    """Download historical close prices via yfinance."""
    print(f"  Fetching {len(tickers)} tickers from {start} to {end}...")
    data = yf.download(tickers, start=start, end=end, progress=False)
    prices = data["Close"]
    print(f"  Got {len(prices)} trading days, {prices.shape[1]} tickers")
    return prices


def collect_tickers(trades_by_date):
    """Extract all unique yfinance tickers needed."""
    tickers = set()
    for date, trades in trades_by_date.items():
        for t in trades:
            code = t["code"]
            ticker = CODE_TO_TICKER.get(code)
            if ticker:
                tickers.add(ticker)
    tickers.add(HS300_TICKER)
    return list(tickers)


# ---- Backtest Runner ----
def run_backtest(trades_by_date, account_filter="all", oos_start=None, oos_end=None):
    """
    Replay trades and compute daily portfolio values.

    Parameters
    ----------
    trades_by_date : dict[str, list[dict]]
        Map date string ("YYYY-MM-DD") to list of trade records.
    account_filter : str
        Account scope: "all", "main", or "lab".
    oos_start, oos_end : str | None
        Optional OOS window. If both set, only trades with date in
        [oos_start, oos_end] (inclusive, lexicographic on ISO date) are
        processed. If either is None, the full history is used (legacy).
    """
    # OOS filter applied before existing processing
    if oos_start is not None and oos_end is not None:
        trades_by_date = {
            d: tr for d, tr in trades_by_date.items()
            if oos_start <= d <= oos_end
        }

    tickers = collect_tickers(trades_by_date)

    # Determine date range (add buffer for pre-trade prices)
    all_dates = sorted(trades_by_date.keys())
    start = (datetime.strptime(all_dates[0], "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")
    end = (datetime.strptime(all_dates[-1], "%Y-%m-%d") + timedelta(days=3)).strftime("%Y-%m-%d")

    prices = fetch_prices(tickers, start, end)
    trading_dates = prices.index.tolist()

    # Initialize accounts
    accounts = {}
    if account_filter in ("all", "main"):
        accounts["main"] = SimAccount("main", 1000000)
    if account_filter in ("all", "lab"):
        accounts["lab"] = SimAccount("lab", 300000)

    hs300_base = None
    daily_records = []

    for idx, dt in enumerate(trading_dates):
        date = dt.strftime("%Y-%m-%d")
        day_prices = prices.iloc[idx]

        # Execute trades for this date
        if date in trades_by_date:
            for t in trades_by_date[date]:
                acc_name = t["account"]
                if acc_name not in accounts:
                    continue
                acc = accounts[acc_name]
                if t["action"] == "buy":
                    acc.buy(date, t["code"], t.get("name", t["code"]),
                            t["price"], t["shares"])
                elif t["action"] == "sell":
                    acc.sell(date, t["code"], t["price"], t["shares"])

        # HS300 benchmark
        hs300 = day_prices[HS300_TICKER]
        if hs300_base is None:
            hs300_base = hs300
        hs300_ret = (hs300 / hs300_base - 1) * 100 if not math.isnan(hs300) else 0

        # Skip days before first trade
        if date < all_dates[0]:
            continue

        record = {"date": date, "hs300": hs300, "hs300_ret": hs300_ret}
        for acc_name, acc in accounts.items():
            record[f"{acc_name}_total"] = acc.total_value(day_prices)
            record[f"{acc_name}_mv"] = acc.market_value(day_prices)
            record[f"{acc_name}_cash"] = acc.cash
            record[f"{acc_name}_pos_n"] = len(acc.positions)
        daily_records.append(record)

    df = pd.DataFrame(daily_records)
    return df, accounts, prices


# ---- Metrics ----
def compute_metrics(acc, initial):
    """Compute performance metrics for an account."""
    total_closed = acc.win_count + acc.lose_count
    win_rate = acc.win_count / total_closed * 100 if total_closed > 0 else 0
    avg_win = acc.win_amount / acc.win_count if acc.win_count > 0 else 0
    avg_lose = acc.lose_amount / acc.lose_count if acc.lose_count > 0 else 0
    profit_factor = acc.win_amount / acc.lose_amount if acc.lose_amount > 0 else float('inf')
    return {
        "realized_pnl": acc.realized_pnl,
        "total_fees": acc.total_fees,
        "total_trades": total_closed,
        "win_count": acc.win_count,
        "lose_count": acc.lose_count,
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_lose": avg_lose,
        "profit_factor": profit_factor,
    }


# ---- Report Generation ----
def generate_report(df, accounts, prices):
    """Generate the full backtest report."""
    lines = []
    last_prices = prices.iloc[-1]
    n_days = len(df)

    lines.append("=" * 72)
    lines.append(f"  虚拟盘回测报告  {df['date'].iloc[0]} ~ {df['date'].iloc[-1]} ({n_days}个交易日)")
    lines.append("=" * 72)

    # ---- Core metrics table ----
    lines.append("")
    header = f"{'指标':20s}"
    for acc_name in accounts:
        label = "主账户(100万)" if acc_name == "main" else "实验账户(30万)"
        header += f" {label:>14s}"
    header += f" {'沪深300':>10s}"
    lines.append(header)
    lines.append("-" * 72)

    for acc_name, acc in accounts.items():
        key = acc_name

    # Calculate per-account metrics
    acc_metrics = {}
    for acc_name, acc in accounts.items():
        col = f"{acc_name}_total"
        tv = df[col]
        returns = tv.pct_change().dropna()
        total_ret = (tv.iloc[-1] / acc.initial - 1) * 100
        annual_ret = ((tv.iloc[-1] / acc.initial) ** (252 / n_days) - 1) * 100 if n_days > 0 else 0
        volatility = returns.std() * np.sqrt(252) * 100 if len(returns) > 1 else 0
        sharpe = (returns.mean() / returns.std() * np.sqrt(252)) if returns.std() > 0 else 0
        peak = tv.expanding().max()
        max_dd = ((tv - peak) / peak).min() * 100
        trade_m = compute_metrics(acc, acc.initial)
        unrealized = 0.0
        for code, pos in acc.positions.items():
            ticker = CODE_TO_TICKER.get(code)
            if ticker and ticker in last_prices.index:
                px = last_prices[ticker]
                if not math.isnan(px):
                    unrealized += (px - pos["avg_cost"]) * pos["shares"]

        acc_metrics[acc_name] = {
            "total_ret": total_ret, "annual_ret": annual_ret,
            "volatility": volatility, "sharpe": sharpe,
            "max_dd": max_dd, "unrealized": unrealized, **trade_m
        }

    hs300_ret = df["hs300_ret"].iloc[-1]

    rows = [
        ("累计收益", "total_ret", "%", "+.2f"),
        ("年化收益", "annual_ret", "%", "+.2f"),
        ("波动率(年化)", "volatility", "%", ".2f"),
        ("夏普比率", "sharpe", "", ".2f"),
        ("最大回撤", "max_dd", "%", ".2f"),
        ("超额收益(vs HS300)", None, "%", "+.2f"),
        ("已实现盈亏", "realized_pnl", "", "+,.0f"),
        ("未实现盈亏", "unrealized", "", "+,.0f"),
        ("交易费用", "total_fees", "", ",.0f"),
        ("总交易次数", "total_trades", "", "d"),
        ("胜率", "win_rate", "%", ".1f"),
        ("盈亏比", "profit_factor", "", ".2f"),
    ]

    for label, key, unit, fmt in rows:
        row = f"{label:20s}"
        for acc_name in accounts:
            if key is None:  # excess return
                val = acc_metrics[acc_name]["total_ret"] - hs300_ret
            else:
                val = acc_metrics[acc_name][key]
            row += f" {val:{fmt}}{unit:>1s}".rjust(15)
        if label == "累计收益":
            row += f" {hs300_ret:+.2f}%".rjust(11)
        else:
            row += " " * 11
        lines.append(row)

    # ---- Daily NAV ----
    lines.append("")
    lines.append("-" * 72)
    lines.append(f"{'日期':12s}", )
    for acc_name in accounts:
        label = "主" if acc_name == "main" else "实验"
        lines[-1] += f" {label+'净值':>10s} {label+'日收益':>8s}"
    lines[-1] += f" {'HS300':>8s}"
    lines.append("-" * 72)

    for i, row in df.iterrows():
        line = f"{row['date']:12s}"
        for acc_name in accounts:
            total = row[f"{acc_name}_total"]
            prev_total = df.iloc[max(0, i-1)][f"{acc_name}_total"]
            daily_ret = ((total / prev_total) - 1) * 100 if i > 0 else 0
            line += f" {total:>10,.0f} {daily_ret:>+7.2f}%"
        line += f" {row['hs300_ret']:>+7.2f}%"
        lines.append(line)

    # ---- Closed trades detail ----
    lines.append("")
    lines.append("=" * 72)
    lines.append("  已平仓交易明细")
    lines.append("=" * 72)
    lines.append(f"{'账户':4s} {'代码':8s} {'名称':8s} {'成本':>8s} {'卖出':>8s} {'股数':>6s} {'盈亏':>10s} {'收益率':>8s}")
    lines.append("-" * 72)

    for acc_name, acc in accounts.items():
        label = "主" if acc_name == "main" else "实验"
        for t in acc.trade_log:
            if t["action"] != "sell":
                continue
            pnl = t["pnl"]
            cost = t.get("cost", t["price"])
            pnl_pct = pnl / (cost * t["shares"]) * 100 if cost and t["shares"] else 0
            lines.append(f"{label:4s} {t['code']:8s} {t.get('name', t['code']):8s} "
                         f"{cost:>8.2f} {t['price']:>8.2f} {t['shares']:>6d} "
                         f"{pnl:>+10,.2f} {pnl_pct:>+7.2f}%")

    # ---- Current holdings ----
    lines.append("")
    lines.append("=" * 72)
    lines.append("  当前持仓快照")
    lines.append("=" * 72)
    lines.append(f"{'账户':4s} {'代码':8s} {'名称':8s} {'成本':>8s} {'现价':>8s} {'股数':>6s} {'市值':>10s} {'浮盈':>10s} {'收益率':>8s}")
    lines.append("-" * 72)

    for acc_name, acc in accounts.items():
        label = "主" if acc_name == "main" else "实验"
        for code, pos in acc.positions.items():
            ticker = CODE_TO_TICKER.get(code)
            px = last_prices.get(ticker, pos["avg_cost"]) if ticker else pos["avg_cost"]
            if isinstance(px, float) and math.isnan(px):
                px = pos["avg_cost"]
            mv = px * pos["shares"]
            pnl = (px - pos["avg_cost"]) * pos["shares"]
            pnl_pct = (px / pos["avg_cost"] - 1) * 100 if pos["avg_cost"] else 0
            lines.append(f"{label:4s} {code:8s} {pos.get('name', code):8s} "
                         f"{pos['avg_cost']:>8.2f} {px:>8.2f} {pos['shares']:>6d} "
                         f"{mv:>10,.0f} {pnl:>+10,.0f} {pnl_pct:>+7.2f}%")

    # ---- Diagnosis ----
    lines.append("")
    lines.append("=" * 72)
    lines.append("  策略诊断")
    lines.append("=" * 72)

    for acc_name, acc in accounts.items():
        label = "主账户" if acc_name == "main" else "实验账户"
        m = acc_metrics[acc_name]
        lines.append(f"\n  [{label}]")

        # Strengths
        pos_count = sum(1 for t in acc.trade_log if t["action"] == "sell" and t.get("pnl", 0) > 0)
        neg_count = sum(1 for t in acc.trade_log if t["action"] == "sell" and t.get("pnl", 0) <= 0)
        if m["win_rate"] >= 60:
            lines.append(f"    ✅ 胜率{m['win_rate']:.0f}%，策略信号质量好")
        if m["max_dd"] > -5:
            lines.append(f"    ✅ 最大回撤{m['max_dd']:.2f}%，风控到位")
        if m["sharpe"] > 1:
            lines.append(f"    ✅ 夏普比率{m['sharpe']:.2f}，风险调整收益优秀")
        excess = m["total_ret"] - hs300_ret
        if excess > 0:
            lines.append(f"    ✅ 超额收益{excess:+.2f}%，跑赢基准")

        # Weaknesses
        if m["win_rate"] < 50 and m["total_trades"] >= 3:
            lines.append(f"    ⚠️ 胜率{m['win_rate']:.0f}%偏低，需优化入场信号")
        if m["max_dd"] < -10:
            lines.append(f"    ⚠️ 最大回撤{m['max_dd']:.2f}%，超过主策略目标(-10%)")
        if excess < -2:
            lines.append(f"    ⚠️ 跑输基准{excess:.2f}%，策略有效性存疑")
        if m["profit_factor"] < 1 and m["total_trades"] >= 3:
            lines.append(f"    ⚠️ 盈亏比{m['profit_factor']:.2f}<1，亏损交易侵蚀利润")

    return "\n".join(lines), acc_metrics


# ---- Main ----
def main():
    parser = argparse.ArgumentParser(description="A股虚拟盘回测引擎")
    parser.add_argument("--start", help="回测起始日期 YYYY-MM-DD")
    parser.add_argument("--end", help="回测结束日期 YYYY-MM-DD")
    parser.add_argument("--account", default="all", choices=["all", "main", "lab"])
    parser.add_argument("--save", action="store_true", help="保存报告到文件")
    parser.add_argument("--json", action="store_true", help="输出JSON格式指标")
    args = parser.parse_args()

    print("=" * 72)
    print("  虚拟盘回测引擎 v1.0")
    print("=" * 72)

    # Load trades
    trades = load_trade_files(args.start, args.end)
    if not trades:
        print("  ERROR: 没有找到交易记录")
        sys.exit(1)

    dates = sorted(trades.keys())
    print(f"  交易记录: {len(dates)}天 ({dates[0]} ~ {dates[-1]})")
    total_trades = sum(len(t) for t in trades.values())
    print(f"  交易笔数: {total_trades}")

    # Run backtest
    df, accounts, prices = run_backtest(trades, args.account)

    # Generate report
    report, metrics = generate_report(df, accounts, prices)
    print(report)

    # Save report
    if args.save:
        today = datetime.now().strftime("%Y%m%d")
        report_path = os.path.join(REPORTS_DIR, f"backtest_{today}.md")
        os.makedirs(REPORTS_DIR, exist_ok=True)
        with open(report_path, "w") as f:
            f.write(report.replace("═", "=").replace("─", "-").replace("│", "|"))
        print(f"\n  报告已保存: {report_path}")

    # JSON output
    if args.json:
        json_out = {}
        for acc_name, m in metrics.items():
            json_out[acc_name] = {k: round(v, 4) if isinstance(v, float) else v
                                  for k, v in m.items()}
        json_out["benchmark"] = {"hs300_return": round(df["hs300_ret"].iloc[-1], 4)}
        print(f"\n--- JSON ---")
        print(json.dumps(json_out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
