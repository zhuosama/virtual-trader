#!/usr/bin/env python3
"""价值投资独立账户模块 v1.1

独立的虚拟账户 — 价值选股+定期再平衡 vs 主策略对照。

v1.1(H10修复):
- market_value / total_value / total_return 改为普通方法 (原 @property 含参反模式)
- 新增 market_value_at_cost / total_value_at_cost / return_at_cost (无价格版)
- buy 返回 Result dict 含 status 字段
- sell 对部分卖出的成本核算修正
- rebalance 修复: 原 total_value(prices) 因 @property 含参而失效
"""

import json, math, os
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Union

# pandas kept for BenchmarkTracker only
try:
    import pandas as pd
except ImportError:
    pd = None


# ---- ValuePosition ----
@dataclass
class ValuePosition:
    ticker: str
    code: str
    name: str
    shares: int
    avg_cost: float
    buy_date: str
    fcf_yield: Optional[float] = None
    roe: Optional[float] = None
    dividend_yield: Optional[float] = None
    pe_ratio: Optional[float] = None
    pb_ratio: Optional[float] = None
    debt_to_equity: Optional[float] = None
    quality_score: Optional[float] = None

    def cost_basis(self) -> float:
        return self.avg_cost * self.shares


# ---- ValueAccount ----
@dataclass
class ValueAccount:
    name: str
    cash: float
    initial_capital: float
    positions: Dict[str, ValuePosition] = field(default_factory=dict)
    trade_history: List[Dict] = field(default_factory=list)
    rebalance_dates: List[str] = field(default_factory=list)
    created_at: str = ""
    # ---- Last-known prices for property-style access ----
    _last_prices: Optional[Dict[str, float]] = field(default=None, repr=False)

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().strftime("%Y-%m-%d")

    # ---- Price-dependent value (require explicit prices) ----
    def market_value(self, prices: Dict[str, float]) -> float:
        """Compute current market value given price dict."""
        mv = 0.0
        for ticker, pos in self.positions.items():
            px = prices.get(ticker, pos.avg_cost)
            mv += px * pos.shares
        return mv

    def market_value_at_cost(self) -> float:
        """Market value at average cost (no price input needed)."""
        return sum(p.cost_basis() for p in self.positions.values())

    def total_value(self, prices: Dict[str, float]) -> float:
        return self.cash + self.market_value(prices)

    def total_value_at_cost(self) -> float:
        return self.cash + self.market_value_at_cost()

    def total_return(self, prices: Dict[str, float]) -> float:
        return self.total_value(prices) / self.initial_capital - 1

    def return_at_cost(self) -> float:
        return self.total_value_at_cost() / self.initial_capital - 1

    @property
    def position_count(self) -> int:
        return len(self.positions)

    @property
    def total_fees_paid(self) -> float:
        """Sum all fees from trade history."""
        fees = 0.0
        for t in self.trade_history:
            fees += t.get("commission", 0) + t.get("stamp_tax", 0) + t.get("transfer_fee", 0)
        return fees

    @property
    def realized_pnl(self) -> float:
        return sum(t.get("pnl", 0) for t in self.trade_history if t.get("action") == "sell")

    @property
    def trade_count(self) -> int:
        return len([t for t in self.trade_history if t.get("action") == "sell"])

    @property
    def win_rate(self) -> Optional[float]:
        sells = [t for t in self.trade_history if t.get("action") == "sell"]
        if not sells:
            return None
        wins = sum(1 for t in sells if t.get("pnl", 0) > 0)
        return wins / len(sells)

    @property
    def profit_factor(self) -> Optional[float]:
        sells = [t for t in self.trade_history if t.get("action") == "sell"]
        if not sells:
            return None
        gross_win = sum(t.get("pnl", 0) for t in sells if t.get("pnl", 0) > 0)
        gross_loss = abs(sum(t.get("pnl", 0) for t in sells if t.get("pnl", 0) < 0))
        return gross_win / gross_loss if gross_loss > 0 else float("inf") if gross_win > 0 else None

    # ---- Set last-known prices (convenience for stateful access) ----
    def update_prices(self, prices: Dict[str, float]) -> None:
        self._last_prices = dict(prices)

    # ---- buy ----
    def buy(self, ticker: str, code: str, name: str, price: float,
            shares: int, date: str, value_metrics: Optional[Dict] = None) -> Dict:
        """Execute buy order. Returns result dict with status field."""
        if shares <= 0:
            return {"action": "buy_rejected", "status": "error",
                    "reason": "shares must be positive", "shares": shares}
        if price <= 0:
            return {"action": "buy_rejected", "status": "error",
                    "reason": "price must be positive", "price": price}

        amount = price * shares
        commission = max(amount * 0.0003, 5)
        transfer_fee = amount * 0.00002
        total_cost = amount + commission + transfer_fee

        if total_cost > self.cash:
            adjusted_shares = int(self.cash / (price * 1.00032))
            adjusted_shares = (adjusted_shares // 100) * 100
            if adjusted_shares <= 0:
                return {"action": "buy_rejected", "status": "error",
                        "reason": "insufficient_cash",
                        "cash": self.cash, "required": total_cost}
            shares = adjusted_shares
            amount = price * shares
            commission = max(amount * 0.0003, 5)
            transfer_fee = amount * 0.00002
            total_cost = amount + commission + transfer_fee

        vm = value_metrics or {}
        pos = ValuePosition(
            ticker=ticker, code=code, name=name,
            shares=shares, avg_cost=price, buy_date=date,
            fcf_yield=vm.get("fcf_yield"),
            roe=vm.get("roe"),
            dividend_yield=vm.get("dividend_yield"),
            pe_ratio=vm.get("pe_ratio"),
            pb_ratio=vm.get("pb_ratio"),
            debt_to_equity=vm.get("debt_to_equity"),
            quality_score=vm.get("quality_score"),
        )

        if ticker in self.positions:
            existing = self.positions[ticker]
            old_total = existing.shares * existing.avg_cost
            existing.shares += shares
            existing.avg_cost = (old_total + amount) / existing.shares
        else:
            self.positions[ticker] = pos

        self.cash -= total_cost

        trade = {
            "action": "buy", "status": "success",
            "date": date, "code": code, "name": name, "ticker": ticker,
            "price": price, "shares": shares, "amount": amount,
            "commission": commission, "transfer_fee": transfer_fee,
            "total_cost": total_cost,
        }
        self.trade_history.append(trade)
        return trade

    # ---- sell ----
    def sell(self, ticker: str, price: float, shares: int, date: str) -> Optional[Dict]:
        """Execute sell order. Returns trade dict or None if position not found."""
        if ticker not in self.positions:
            return None
        if shares <= 0 or price <= 0:
            return {"action": "sell_rejected", "status": "error",
                    "reason": "invalid shares or price"}

        pos = self.positions[ticker]
        sell_shares = min(shares, pos.shares)
        amount = price * sell_shares
        commission = max(amount * 0.0003, 5)
        stamp_tax = amount * 0.001
        transfer_fee = amount * 0.00002
        net = amount - commission - stamp_tax - transfer_fee
        cost_basis = pos.avg_cost * sell_shares
        pnl = net - cost_basis

        pos.shares -= sell_shares
        if pos.shares <= 0:
            del self.positions[ticker]

        self.cash += net

        trade = {
            "action": "sell", "status": "success",
            "date": date, "code": pos.code, "name": pos.name, "ticker": ticker,
            "price": price, "shares": sell_shares, "amount": amount,
            "commission": commission, "stamp_tax": stamp_tax,
            "transfer_fee": transfer_fee, "net": net,
            "pnl": pnl, "pnl_pct": pnl / cost_basis * 100 if cost_basis else 0,
        }
        self.trade_history.append(trade)
        return trade

    # ---- rebalance ----
    def rebalance(self, target_weights: Dict[str, float], prices: Dict[str, float],
                  date: str, tolerance: float = 0.05) -> List[Dict]:
        """Rebalance portfolio to target weights.

        target_weights: {ticker: weight} — weights MUST sum to ≤1.0
        prices: {ticker: current_price}
        tolerance: fractional deviation allowed before rebalancing (default 5%)
        """
        trades = []
        total_val = self.total_value(prices)
        if total_val <= 0:
            return trades

        weight_sum = sum(target_weights.values())
        if weight_sum <= 0 or weight_sum > 1.0 + 1e-9:
            raise ValueError(f"target_weights sum {weight_sum} must be in (0, 1]")

        # Target values in CNY
        target_values = {t: total_val * w for t, w in target_weights.items()}

        # Phase 1: Sell positions NOT in target list
        for ticker in list(self.positions):
            if ticker not in target_weights:
                px = prices.get(ticker, self.positions[ticker].avg_cost)
                trade = self.sell(ticker, px, self.positions[ticker].shares, date)
                if trade:
                    trades.append(trade)

        # Phase 2: Trim overweight positions
        for ticker, pos in list(self.positions.items()):
            if ticker not in target_weights:
                continue
            px = prices.get(ticker, pos.avg_cost)
            current_val = pos.shares * px
            target_val = target_values.get(ticker, 0)
            if target_val <= 0:
                continue
            deviation = (current_val - target_val) / target_val
            if deviation > tolerance:
                target_shares = int(target_val / px / 100) * 100
                sell_n = max(0, pos.shares - target_shares)
                if sell_n > 0:
                    trade = self.sell(ticker, px, sell_n, date)
                    if trade:
                        trades.append(trade)

        # Phase 3: Buy underweight positions
        for ticker, target_val in target_values.items():
            px = prices.get(ticker)
            if px is None or px <= 0:
                continue
            current_val = 0.0
            if ticker in self.positions:
                current_val = self.positions[ticker].shares * px
            if current_val >= target_val * (1 - tolerance):
                continue
            deficit = target_val - current_val
            shares = int(deficit / px / 100) * 100
            if shares <= 0:
                continue
            trade = self.buy(ticker, ticker.split(".")[0], ticker, px, shares, date)
            if trade and trade.get("status") == "success":
                trades.append(trade)

        if trades:
            self.rebalance_dates.append(date)
        return trades

    # ---- Serialization ----
    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "cash": self.cash,
            "initial_capital": self.initial_capital,
            "positions": {
                ticker: {
                    "code": p.code, "name": p.name, "shares": p.shares,
                    "avg_cost": p.avg_cost, "buy_date": p.buy_date,
                    "fcf_yield": p.fcf_yield, "roe": p.roe,
                    "dividend_yield": p.dividend_yield,
                    "pe_ratio": p.pe_ratio, "pb_ratio": p.pb_ratio,
                    "debt_to_equity": p.debt_to_equity,
                    "quality_score": p.quality_score,
                }
                for ticker, p in self.positions.items()
            },
            "trade_count": len(self.trade_history),
            "trade_history": self.trade_history,
            "rebalance_dates": self.rebalance_dates,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "ValueAccount":
        acct = cls(
            name=data["name"], cash=data["cash"],
            initial_capital=data["initial_capital"],
            created_at=data.get("created_at", ""),
        )
        for ticker, pdict in data.get("positions", {}).items():
            acct.positions[ticker] = ValuePosition(
                ticker=ticker,
                code=pdict["code"], name=pdict["name"],
                shares=pdict["shares"], avg_cost=pdict["avg_cost"],
                buy_date=pdict.get("buy_date", ""),
                fcf_yield=pdict.get("fcf_yield"),
                roe=pdict.get("roe"),
                dividend_yield=pdict.get("dividend_yield"),
                pe_ratio=pdict.get("pe_ratio"),
                pb_ratio=pdict.get("pb_ratio"),
                debt_to_equity=pdict.get("debt_to_equity"),
                quality_score=pdict.get("quality_score"),
            )
        acct.rebalance_dates = data.get("rebalance_dates", [])
        acct.trade_history = data.get("trade_history", [])
        return acct


# ---- RebalanceScheduler ----
class RebalanceScheduler:
    def __init__(self, frequency: str = "quarterly", start_date: Optional[str] = None):
        if frequency not in ("weekly", "monthly", "quarterly"):
            raise ValueError(f"Unknown frequency: {frequency}")
        self.frequency = frequency
        self.last_rebalance = start_date

    def should_rebalance(self, date_str: str) -> bool:
        if not self.last_rebalance:
            return True
        date = datetime.strptime(date_str, "%Y-%m-%d")
        last = datetime.strptime(self.last_rebalance, "%Y-%m-%d")
        if self.frequency == "quarterly":
            months_diff = (date.year - last.year) * 12 + (date.month - last.month)
            return months_diff >= 3
        elif self.frequency == "monthly":
            return (date.year, date.month) != (last.year, last.month)
        elif self.frequency == "weekly":
            return date.isocalendar()[1] != last.isocalendar()[1]
        return False


# ---- BenchmarkTracker ----
class BenchmarkTracker:
    def __init__(self, benchmark_ticker: str = "000300.SS"):
        self.benchmark_ticker = benchmark_ticker
        self.records: List[Dict] = []

    def record(self, date: str, account_value: float, account_initial: float,
               bench_value: float, bench_initial: float) -> Dict:
        rec = {
            "date": date,
            "account_value": round(account_value, 2),
            "account_return": round(account_value / account_initial - 1, 6),
            "benchmark_value": round(bench_value, 2),
            "benchmark_return": round(bench_value / bench_initial - 1, 6),
            "excess_return": round(
                (account_value / account_initial - 1) -
                (bench_value / bench_initial - 1), 6),
        }
        self.records.append(rec)
        return rec

    def latest(self) -> Optional[Dict]:
        return self.records[-1] if self.records else None

    def summary(self) -> Dict:
        if not self.records:
            return {"status": "no_records"}
        latest = self.records[-1]
        excess_returns = [r["excess_return"] for r in self.records]
        return {
            "latest_account_return": latest["account_return"],
            "latest_benchmark_return": latest["benchmark_return"],
            "latest_excess_return": latest["excess_return"],
            "total_days": len(self.records),
            "positive_excess_days": sum(1 for e in excess_returns if e > 0),
            "tracking_error": (
                (sum((e - sum(excess_returns)/len(excess_returns))**2
                     for e in excess_returns) / (len(excess_returns) - 1)) ** 0.5
                if len(excess_returns) > 1 else 0.0
            ),
        }

    def to_dataframe(self):
        """Returns list of dicts (pd-free for testing)."""
        return list(self.records)


# ---- CLI ----
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Value Account Manager")
    parser.add_argument("--init", action="store_true")
    parser.add_argument("--capital", type=float, default=500000)
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args()

    vdir = os.path.expanduser("~/.hermes/virtual-trader/value_account")
    os.makedirs(vdir, exist_ok=True)

    if args.init:
        acct = ValueAccount(name="value_investing", cash=args.capital,
                           initial_capital=args.capital)
        with open(os.path.join(vdir, "account.json"), "w") as f:
            json.dump(acct.to_dict(), f, indent=2, ensure_ascii=False)
        print(f"Value account initialized: {args.capital:,.0f} CNY")
        print(f"Saved: {vdir}/account.json")

    if args.report:
        path = os.path.join(vdir, "account.json")
        if os.path.exists(path):
            with open(path) as f:
                data = json.load(f)
            acct = ValueAccount.from_dict(data)
            print(f"Name: {acct.name}")
            print(f"Cash: {acct.cash:,.2f}")
            print(f"Positions: {acct.position_count}")
            print(f"Trades: {acct.trade_count}")
            print(f"Win rate: {acct.win_rate}")
