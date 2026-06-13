#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
多Agent协调器
负责协调各个Agent的执行流程
"""

import json
import os
import csv

VTRADER_HOME = os.environ.get("VTRADER_HOME", os.path.expanduser("~/.hermes/virtual-trader"))
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import logging

# 添加当前目录到路径
sys.path.append(os.path.dirname(__file__))

# 导入各个Agent
from market_analyst import MarketAnalystAgent
from execution_planner import ExecutionPlannerAgent
from risk_controller import RiskControllerAgent
from review_agent import ReviewAgent
from strategy_maintainer import StrategyMaintainerAgent, detect_iteration_stall, detect_deployment_stall
import audit_layer

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class MultiAgentCoordinator:
    """多Agent协调器"""
    
    def __init__(self, config_path: str = None):
        """初始化协调器"""
        self.config = self._load_config(config_path)
        self.data_dir = VTRADER_HOME
        self.agents = self._initialize_agents()

    def _atomic_write_json(self, path: str, data):
        """Write JSON via same-directory temp file, then atomically replace target."""
        tmp_path = f"{path}.tmp"
        try:
            with open(tmp_path, 'w') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, path)
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

    def _run_ledger_validation(self, strict: bool = False) -> Dict:
        """Run ledger invariants for this data_dir and return a compact result."""
        scripts_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        from validate_ledger_consistency import LedgerValidator

        validator = LedgerValidator(root=self.data_dir, strict=strict)
        results = validator.validate()
        failures = [r for r in results if r.get("status") == "FAIL"]
        return {
            "status": "fail" if failures else "pass",
            "failures": failures,
            "results": results,
        }

    def _ensure_daily_report_for_date(self, date_str: str) -> Dict:
        """Create a minimal daily report when a trade file exists but the report is missing."""
        report_dir = os.path.join(self.data_dir, "reports", "daily")
        report_path = os.path.join(report_dir, f"{date_str}.md")
        if os.path.exists(report_path):
            return {"created": False, "path": report_path, "reason": "exists"}

        trade_path = os.path.join(self.data_dir, "trades", date_str[:7], f"{date_str}.json")
        if not os.path.exists(trade_path):
            return {"created": False, "path": report_path, "reason": "no_trade_file"}

        trade_record = self._read_json(trade_path, {})
        perf_history = self._read_json(
            os.path.join(self.data_dir, "strategies", "performance_history.json"),
            [],
        )
        perf_entry = next(
            (entry for entry in perf_history if isinstance(entry, dict) and entry.get("date") == date_str),
            {},
        )

        accounts = {
            account_type: self._read_json(
                os.path.join(self.data_dir, "accounts", f"{account_type}.json"),
                {},
            )
            for account_type in ("main", "lab")
        }
        snapshots = trade_record.get("account_snapshots", {}) if isinstance(trade_record, dict) else {}

        def account_line(account_type: str, title: str) -> str:
            snapshot = snapshots.get(account_type, {})
            account = accounts.get(account_type, {})
            pct = perf_entry.get(f"{account_type}_pct")
            if pct is None:
                pct = snapshot.get("daily_pnl_pct", account.get("daily_pnl_pct", 0))
            total_value = snapshot.get("total_value", account.get("total_value", 0))
            daily_pnl = snapshot.get("daily_pnl", account.get("daily_pnl", 0))
            return f"- {title}总资产：{total_value:,.0f}（{pct:+.2f}%），今日盈亏：{daily_pnl:+,.0f}元"

        lines = [
            f"# 虚拟盘日报 {date_str}",
            "",
            "## 今日绩效",
            account_line("main", "主账户"),
            account_line("lab", "实验账户"),
        ]
        if perf_entry.get("hs300_pct") is not None:
            lines.append(f"- 沪深300：{perf_entry.get('hs300_pct'):+.2f}%")

        trades = trade_record.get("trades", []) if isinstance(trade_record, dict) else []
        lines.extend(["", "## 今日交易"])
        if trades:
            for idx, trade in enumerate(trades, 1):
                action = {"buy": "买入", "sell": "卖出"}.get(trade.get("action"), trade.get("action", "交易"))
                amount = trade.get("amount", trade.get("net_amount", 0))
                lines.append(
                    f"{idx}. {trade.get('account', '')}账户 {action} "
                    f"{trade.get('name', '')}（{trade.get('code', '')}）"
                    f"{trade.get('shares', 0)}股 @ {trade.get('price', 0):.2f}，金额 {amount:,.0f}元"
                )
                if trade.get("signal"):
                    lines.append(f"   - 原因：{trade.get('signal')}")
        else:
            lines.append("- 今日无交易")

        lines.extend([
            "",
            "## 账本校验备注",
            "- 自动补全日报：交易文件已存在，盘后最终账本校验前补齐 INV-2 所需日报。",
            "",
        ])

        os.makedirs(report_dir, exist_ok=True)
        tmp_path = f"{report_path}.tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            os.replace(tmp_path, report_path)
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

        return {"created": True, "path": report_path, "reason": "missing_report"}

    def _persist_risk_actions(self, risk_actions: List[Dict]) -> Dict:
        """Persist reduce-only risk actions to a durable pending queue."""
        actions_dir = os.path.join(self.data_dir, "actions")
        os.makedirs(actions_dir, exist_ok=True)
        queue_path = os.path.join(actions_dir, "pending.json")

        queued = []
        if os.path.exists(queue_path):
            with open(queue_path) as f:
                queued = json.load(f)

        # Open statuses block duplicate queue entries; terminal statuses may be re-queued.
        open_statuses = {"proposed", "acknowledged"}
        open_keys = {
            (a.get("account"), a.get("code"), a.get("type"), a.get("action"))
            for a in queued
            if a.get("status") in open_statuses
        }

        now = datetime.now()
        expires_at = self._next_trading_action_expiry(now)
        added = []
        existing_open = 0
        for idx, action in enumerate(risk_actions, 1):
            key = (action.get("account"), action.get("code"), action.get("type"), action.get("action"))
            if key in open_keys:
                existing_open += 1
                continue

            item = dict(action)
            action_id = (
                f"risk-{now.strftime('%Y%m%d')}-{item.get('account')}-"
                f"{item.get('code')}-{item.get('type')}-{item.get('action')}"
            )
            item.setdefault("action_id", action_id)
            item.setdefault("generated_at", now.isoformat())
            item.setdefault("status", "proposed")
            item.setdefault("expires_at", expires_at.isoformat())
            queued.append(item)
            added.append(item)
            open_keys.add(key)

        if added:
            self._atomic_write_json(queue_path, queued)

        return {
            "path": queue_path,
            "added": len(added),
            "existing_open": existing_open,
            "total_open": sum(1 for a in queued if a.get("status") in open_statuses),
        }

    def _process_risk_actions(self, risk_actions: List[Dict]) -> Dict:
        """Auto-execute deterministic reduce-only actions; queue the rest."""
        result = {
            "executed": 0,
            "failed": 0,
            "results": [],
            "queued": {"added": 0, "existing_open": 0, "total_open": 0},
        }
        queue_actions = []
        can_execute_now = self._is_trading_day(datetime.now())
        for action in risk_actions:
            if (
                can_execute_now
                and action.get("auto_execute")
                and action.get("action") == "sell"
                and action.get("sell_shares", 0) > 0
            ):
                execution = self._execute_risk_action(action)
                execution.setdefault("action", dict(action))
                execution.setdefault("name", action.get("name", ""))
                execution.setdefault("reason", action.get("reason", ""))
                result["results"].append(execution)
                if execution.get("ok"):
                    result["executed"] += 1
                else:
                    result["failed"] += 1
                    queue_actions.append(action)
            else:
                queue_actions.append(action)

        if queue_actions:
            result["queued"] = self._persist_risk_actions(queue_actions)
        return result

    def _process_pending_risk_actions(self) -> Dict:
        """Execute queued auto risk actions on trading days."""
        result = {"executed": 0, "failed": 0, "skipped": 0, "results": []}
        queue_path = os.path.join(self.data_dir, "actions", "pending.json")
        if not os.path.exists(queue_path):
            return result
        with open(queue_path) as f:
            queued = json.load(f)

        if not self._is_trading_day(datetime.now()):
            result["skipped"] = sum(1 for a in queued if a.get("status") in {"proposed", "acknowledged"})
            return result

        changed = False
        for action in queued:
            if action.get("status") not in {"proposed", "acknowledged"}:
                continue
            if not (action.get("auto_execute") and action.get("action") == "sell" and action.get("sell_shares", 0) > 0):
                continue
            expires_at = action.get("expires_at")
            if expires_at:
                try:
                    expires_at_dt = datetime.fromisoformat(expires_at)
                except ValueError:
                    action["status"] = "invalid"
                    action["invalid_reason"] = f"invalid expires_at: {expires_at}"
                    result["skipped"] += 1
                    changed = True
                    continue
                if datetime.now() > expires_at_dt:
                    action["status"] = "expired"
                    action["expired_at"] = datetime.now().isoformat()
                    result["skipped"] += 1
                    changed = True
                    continue
            execution = self._execute_risk_action(action, use_action_price=False)
            execution.setdefault("action", dict(action))
            execution.setdefault("name", action.get("name", ""))
            execution.setdefault("reason", action.get("reason", ""))
            result["results"].append(execution)
            action["execution_result"] = execution
            action["executed_at"] = datetime.now().isoformat()
            if execution.get("ok"):
                action["status"] = "completed"
                result["executed"] += 1
            else:
                action["status"] = "failed"
                result["failed"] += 1
            changed = True

        if changed:
            self._atomic_write_json(queue_path, queued)
        return result

    def _is_trading_day(self, dt: datetime) -> bool:
        known_days = self._load_known_trading_days()
        if known_days:
            day = dt.strftime("%Y-%m-%d")
            if known_days["start"] <= day <= known_days["end"]:
                return day in known_days["days"]
        return dt.weekday() < 5

    def _load_known_trading_days(self) -> Optional[Dict]:
        """Load known A-share trading dates from local PIT price CSVs, if available."""
        cached = getattr(self, "_known_trading_days", None)
        if cached is not None:
            return cached

        data_dir = os.path.join(self.data_dir, "data", "cn_pit")
        days = set()
        if os.path.isdir(data_dir):
            for filename in os.listdir(data_dir):
                if not (filename.startswith("prices") and filename.endswith(".csv")):
                    continue
                path = os.path.join(data_dir, filename)
                try:
                    with open(path, newline="") as f:
                        reader = csv.reader(f)
                        next(reader, None)
                        for row in reader:
                            if row and len(row[0]) == 10:
                                days.add(row[0])
                except OSError:
                    continue

        self._known_trading_days = (
            {"days": days, "start": min(days), "end": max(days)}
            if days else None
        )
        return self._known_trading_days

    def _next_trading_action_expiry(self, dt: datetime) -> datetime:
        next_expiry = (dt + timedelta(days=1)).replace(hour=23, minute=59, second=59, microsecond=0)
        for _ in range(14):
            if self._is_trading_day(next_expiry):
                return next_expiry
            next_expiry += timedelta(days=1)
        return next_expiry

    def _execute_risk_action(self, action: Dict, use_action_price: bool = True) -> Dict:
        """Execute a reduce-only sell action in the virtual ledger."""
        if action.get("action") != "sell":
            return {"ok": False, "error": "only sell actions may be auto-executed", "action": action}

        account_type = action.get("account")
        code = action.get("code")
        sell_shares = int(action.get("sell_shares", 0))
        if account_type not in {"main", "lab"} or not code or sell_shares <= 0:
            return {"ok": False, "error": "invalid risk action", "action": action}

        account_path = os.path.join(self.data_dir, "accounts", f"{account_type}.json")
        try:
            with open(account_path) as f:
                account = json.load(f)
        except Exception as e:
            return {"ok": False, "error": f"account load failed: {e}", "action": action}

        positions = account.get("positions", [])
        position = next((p for p in positions if p.get("code") == code), None)
        if not position:
            return {"ok": False, "error": f"position not found: {code}", "action": action}

        current_shares = int(position.get("shares", 0))
        if sell_shares > current_shares:
            return {"ok": False, "error": "sell_shares exceeds current position", "action": action}

        price_source = action.get("price") if use_action_price else position.get("current_price")
        price = float(price_source or position.get("current_price") or 0)
        if price <= 0:
            return {"ok": False, "error": "invalid execution price", "action": action}

        amount = round(price * sell_shares, 2)
        commission = round(max(amount * 0.0003, 5), 2)
        stamp_tax = round(amount * 0.001, 2)
        transfer_fee = round(amount * 0.00002, 2)
        total_cost = round(commission + stamp_tax + transfer_fee, 2)
        net_amount = round(amount - total_cost, 2)
        avg_cost = float(position.get("avg_cost", price))
        realized_pnl = round((price - avg_cost) * sell_shares - total_cost, 2)

        remaining_shares = current_shares - sell_shares
        if remaining_shares > 0:
            position["shares"] = remaining_shares
            position["current_price"] = price
            position["market_value"] = round(remaining_shares * price, 2)
            position["unrealized_pnl"] = round(position["market_value"] - remaining_shares * avg_cost, 2)
            position["unrealized_pnl_pct"] = round((price / avg_cost - 1) * 100, 2) if avg_cost else 0
        else:
            account["positions"] = [p for p in positions if p.get("code") != code]

        account["cash"] = round(account.get("cash", 0) + net_amount, 2)
        account["portfolio_market_value"] = round(sum(p.get("market_value", 0) for p in account.get("positions", [])), 2)
        account["total_value"] = round(account["cash"] + account["portfolio_market_value"], 2)
        account["total_pnl"] = round(account["total_value"] - account.get("initial_capital", 0), 2)
        initial_capital = account.get("initial_capital", 1)
        account["total_pnl_pct"] = round(account["total_pnl"] / initial_capital * 100, 2) if initial_capital else 0
        account["position_pct"] = round(account["portfolio_market_value"] / account["total_value"] * 100, 1) if account["total_value"] else 0
        account["trade_count"] = account.get("trade_count", 0) + 1
        account["updated_at"] = datetime.now().strftime("%Y-%m-%dT15:00:00")
        self._atomic_write_json(account_path, account)

        today = datetime.now().strftime("%Y-%m-%d")
        trades_dir = os.path.join(self.data_dir, "trades", today[:7])
        os.makedirs(trades_dir, exist_ok=True)
        trade_path = os.path.join(trades_dir, f"{today}.json")
        if os.path.exists(trade_path):
            with open(trade_path) as f:
                trade_record = json.load(f)
        else:
            trade_record = {
                "date": today,
                "is_trading_day": True,
                "market_summary": {},
                "trades": [],
                "account_snapshots": {},
            }

        trade_record.setdefault("trades", []).append({
            "account": account_type,
            "time": "15:00",
            "action": "sell",
            "code": code,
            "name": action.get("name", position.get("name", "")),
            "type": "stock",
            "price": price,
            "shares": sell_shares,
            "amount": amount,
            "commission": commission,
            "stamp_tax": stamp_tax,
            "transfer_fee": transfer_fee,
            "total_cost": total_cost,
            "net_amount": net_amount,
            "signal": action.get("reason", "风控自动减仓"),
            "strategy_ref": "risk_controller",
            "realized_pnl": realized_pnl,
            "execution_type": "executed",
            "source": "auto_risk_reduction",
            "generated_at": datetime.now().isoformat(),
            "rationale": "deterministic reduce-only risk rule",
        })
        trade_record.setdefault("account_snapshots", {})[account_type] = {
            "total_value": account["total_value"],
            "daily_pnl": account.get("daily_pnl", 0),
            "daily_pnl_pct": account.get("daily_pnl_pct", 0),
        }
        self._atomic_write_json(trade_path, trade_record)

        action_id = action.get(
            "action_id",
            f"risk-{today.replace('-', '')}-{account_type}-{code}-{action.get('type', 'risk_reduction')}-sell",
        )
        return {
            "ok": True,
            "action_id": action_id,
            "account": account_type,
            "code": code,
            "executed_shares": sell_shares,
            "price": price,
            "net_amount": net_amount,
            "realized_pnl": realized_pnl,
            "trade_record_path": trade_path,
        }

    @staticmethod
    def _round_down_to_lot(shares: float, lot_size: int = 100) -> int:
        """Round share count DOWN to the nearest lot (A-share board lot = 100).

        Buys/autonomous sells must not overshoot the requested notional, so we
        round down (mirror of backtest_engine buy `(shares // 100) * 100`,
        backtest/backtest_engine.py:70). Contrast with risk_controller
        `_round_up_to_lot` (risk_controller.py:25) used for reduce-only sizing.
        """
        if shares <= 0:
            return 0
        return int(shares // lot_size) * lot_size

    def _execute_orders(self, account_type: str, orders: List[Dict],
                        prices: Dict, account_state: Dict) -> Dict:
        """G7 real-execution writer for canary/live: turn surviving (pass/clamp)
        orders into real trades, mutating accounts/<acct>.json + trades/<月>/<日>.json.

        Mirrors the reduce-only writer `_execute_risk_action` (coordinator.py:344-466)
        for the account/trade write format, fee model, and atomic writes.

        Fee model (cited):
          SELL — commission=round(max(amount*0.0003,5),2); stamp_tax=round(amount*0.001,2);
                 transfer_fee=round(amount*0.00002,2); net=amount-fees
                 (identical to _execute_risk_action coordinator.py:377-381 and
                  backtest_engine.sell backtest/backtest_engine.py:100-103).
          BUY  — commission + transfer_fee only, **NO stamp tax** (A-share asymmetry;
                 mirrors backtest_engine.buy backtest/backtest_engine.py:65-66);
                 net=amount+fees (cash out = notional + fees).

        Lot rounding: shares=round_down_to_lot(est_amount/price,100); shares→0 skipped.
        BUY cash guard: if cash < net, clamp shares down to the most affordable lot;
        if even one lot is unaffordable, the order is skipped (documented).
        SELL is clamped to currently-held shares.

        Returns {trades:[...], snapshot:{total_value,daily_pnl,daily_pnl_pct},
                 account_path, trade_path}. Caller is responsible for post-write
        bookkeeping (perf upsert + daily report + ledger validation + rollback).
        """
        account_path = os.path.join(self.data_dir, "accounts", f"{account_type}.json")
        with open(account_path) as f:
            account = json.load(f)

        today = datetime.now().strftime("%Y-%m-%d")
        executed_trades: List[Dict] = []

        for order in orders or []:
            side = order.get("side")
            code = order.get("code")
            if not code or side not in ("buy", "sell"):
                continue
            price = float(prices.get(code) or order.get("price") or 0)
            if price <= 0:
                continue
            est_amount = float(order.get("est_amount", 0) or 0)
            if est_amount <= 0:
                continue

            positions = account.get("positions", [])
            position = next((p for p in positions if p.get("code") == code), None)

            if side == "buy":
                shares = self._round_down_to_lot(est_amount / price)
                if shares <= 0:
                    continue
                amount = round(price * shares, 2)
                commission = round(max(amount * 0.0003, 5), 2)
                transfer_fee = round(amount * 0.00002, 2)
                stamp_tax = 0  # A-share buys pay no stamp tax
                total_cost = round(commission + transfer_fee, 2)
                net_amount = round(amount + total_cost, 2)

                # cash guard: clamp shares down to affordable lots, else skip
                cash = account.get("cash", 0)
                if net_amount > cash:
                    # cash >= price*shares*(1+0.0003+0.00002) (commission floor 5 ignored
                    # at clamp-time for simplicity; re-verified after re-lotting below)
                    affordable = int(cash / (price * 1.00032))
                    shares = self._round_down_to_lot(affordable)
                    if shares <= 0:
                        continue
                    amount = round(price * shares, 2)
                    commission = round(max(amount * 0.0003, 5), 2)
                    transfer_fee = round(amount * 0.00002, 2)
                    total_cost = round(commission + transfer_fee, 2)
                    net_amount = round(amount + total_cost, 2)
                    # if commission floor pushed net over cash, drop one more lot
                    while net_amount > cash and shares > 0:
                        shares -= 100
                        if shares <= 0:
                            break
                        amount = round(price * shares, 2)
                        commission = round(max(amount * 0.0003, 5), 2)
                        transfer_fee = round(amount * 0.00002, 2)
                        total_cost = round(commission + transfer_fee, 2)
                        net_amount = round(amount + total_cost, 2)
                    if shares <= 0:
                        continue

                if position:
                    old_shares = int(position.get("shares", 0))
                    old_avg = float(position.get("avg_cost", price))
                    new_shares = old_shares + shares
                    position["avg_cost"] = round(
                        (old_shares * old_avg + amount) / new_shares, 4) if new_shares else price
                    position["shares"] = new_shares
                    position["current_price"] = price
                    position["market_value"] = round(new_shares * price, 2)
                    position["unrealized_pnl"] = round(
                        position["market_value"] - new_shares * position["avg_cost"], 2)
                    position["unrealized_pnl_pct"] = round(
                        (price / position["avg_cost"] - 1) * 100, 2) if position["avg_cost"] else 0
                else:
                    positions.append({
                        "code": code,
                        "name": order.get("name", ""),
                        "shares": shares,
                        "avg_cost": round(price, 4),
                        "current_price": price,
                        "market_value": round(shares * price, 2),
                        "unrealized_pnl": 0.0,
                        "unrealized_pnl_pct": 0.0,
                    })
                    account["positions"] = positions

                account["cash"] = round(account.get("cash", 0) - net_amount, 2)
                realized_pnl = 0.0

            else:  # sell — mirror _execute_risk_action
                if not position:
                    continue
                current_shares = int(position.get("shares", 0))
                want = self._round_down_to_lot(est_amount / price)
                sell_shares = min(want, current_shares)
                if sell_shares <= 0:
                    continue
                amount = round(price * sell_shares, 2)
                commission = round(max(amount * 0.0003, 5), 2)
                stamp_tax = round(amount * 0.001, 2)
                transfer_fee = round(amount * 0.00002, 2)
                total_cost = round(commission + stamp_tax + transfer_fee, 2)
                net_amount = round(amount - total_cost, 2)
                avg_cost = float(position.get("avg_cost", price))
                realized_pnl = round((price - avg_cost) * sell_shares - total_cost, 2)

                remaining = current_shares - sell_shares
                if remaining > 0:
                    position["shares"] = remaining
                    position["current_price"] = price
                    position["market_value"] = round(remaining * price, 2)
                    position["unrealized_pnl"] = round(
                        position["market_value"] - remaining * avg_cost, 2)
                    position["unrealized_pnl_pct"] = round(
                        (price / avg_cost - 1) * 100, 2) if avg_cost else 0
                else:
                    account["positions"] = [p for p in positions if p.get("code") != code]

                account["cash"] = round(account.get("cash", 0) + net_amount, 2)
                shares = sell_shares

            executed_trades.append({
                "account": account_type,
                "time": "15:00",
                "action": side,
                "code": code,
                "name": order.get("name", (position or {}).get("name", "")),
                "type": "stock",
                "price": price,
                "shares": shares,
                "amount": amount,
                "commission": commission,
                "stamp_tax": stamp_tax,
                "transfer_fee": transfer_fee,
                "total_cost": total_cost,
                "net_amount": net_amount,
                "signal": order.get("reason", "受控自主执行"),
                "strategy_ref": "execution_model",
                "realized_pnl": realized_pnl,
                "execution_type": "executed",
                "source": "autonomous_exec",
                "generated_at": datetime.now().isoformat(),
                "rationale": "controlled autonomous execution (G7)",
            })

        if not executed_trades:
            return {"trades": [], "snapshot": {}, "account_path": account_path,
                    "trade_path": None}

        # recompute account aggregates
        account["portfolio_market_value"] = round(
            sum(p.get("market_value", 0) for p in account.get("positions", [])), 2)
        account["total_value"] = round(
            account["cash"] + account["portfolio_market_value"], 2)
        account["total_pnl"] = round(
            account["total_value"] - account.get("initial_capital", 0), 2)
        initial_capital = account.get("initial_capital", 1)
        account["total_pnl_pct"] = round(
            account["total_pnl"] / initial_capital * 100, 2) if initial_capital else 0
        account["position_pct"] = round(
            account["portfolio_market_value"] / account["total_value"] * 100, 1) \
            if account["total_value"] else 0
        account["trade_count"] = account.get("trade_count", 0) + len(executed_trades)
        account["updated_at"] = datetime.now().strftime("%Y-%m-%dT15:00:00")
        self._atomic_write_json(account_path, account)

        # append to trades/<月>/<日>.json
        trades_dir = os.path.join(self.data_dir, "trades", today[:7])
        os.makedirs(trades_dir, exist_ok=True)
        trade_path = os.path.join(trades_dir, f"{today}.json")
        if os.path.exists(trade_path):
            with open(trade_path) as f:
                trade_record = json.load(f)
        else:
            trade_record = {
                "date": today,
                "is_trading_day": True,
                "market_summary": {},
                "trades": [],
                "account_snapshots": {},
            }
        trade_record.setdefault("trades", []).extend(executed_trades)
        snapshot = {
            "total_value": account["total_value"],
            "daily_pnl": account.get("daily_pnl", 0),
            "daily_pnl_pct": account.get("daily_pnl_pct", 0),
        }
        trade_record.setdefault("account_snapshots", {})[account_type] = snapshot
        self._atomic_write_json(trade_path, trade_record)

        return {
            "trades": executed_trades,
            "snapshot": snapshot,
            "account_path": account_path,
            "trade_path": trade_path,
        }

    def _pending_retry_dir(self) -> str:
        return os.path.join(self.data_dir, "strategies", "proposals", "pending_retry")

    def _persist_pending_retry_proposal(self, proposal: Dict, audit_result: Dict) -> str:
        """Persist a proposal that hit audit INFRA_ERROR so a later run can retry it."""
        pending_dir = self._pending_retry_dir()
        os.makedirs(pending_dir, exist_ok=True)
        proposal_id = proposal["proposal_id"]
        retry_path = os.path.join(pending_dir, f"{proposal_id}.json")
        retry_count = 0
        if os.path.exists(retry_path):
            with open(retry_path) as f:
                retry_count = json.load(f).get("retry_count", 0)

        record = {
            "proposal_id": proposal_id,
            "status": "pending_retry",
            "retry_count": retry_count,
            "updated_at": datetime.now().isoformat(),
            "last_audit_result": audit_result,
            "proposal": proposal,
        }
        self._atomic_write_json(retry_path, record)
        return retry_path

    def _retry_pending_audit_proposals(self, maintainer, review_report: Dict, max_retries: int = 5) -> Dict:
        """Retry proposals previously blocked by audit INFRA_ERROR."""
        pending_dir = self._pending_retry_dir()
        result = {
            "attempted": 0,
            "auto_merged": 0,
            "still_pending": 0,
            "human_review": 0,
            "terminal": 0,
            "retry_exhausted": 0,
        }
        if not os.path.exists(pending_dir):
            return result

        llm = getattr(maintainer, "llm", None)
        if llm is None:
            result["blocked"] = "LLM client unavailable"
            return result

        for name in sorted(os.listdir(pending_dir)):
            if not name.endswith(".json"):
                continue
            retry_path = os.path.join(pending_dir, name)
            with open(retry_path) as f:
                record = json.load(f)
            proposal = record.get("proposal", record)
            proposal_id = proposal["proposal_id"]
            if record.get("status") == "human_review":
                result["human_review"] += 1
                continue
            if record.get("retry_count", 0) >= max_retries:
                audit_layer.append_audit_log({
                    "proposal_id": proposal_id,
                    "audited_at": datetime.now().isoformat(),
                    "decision": "BLOCKED",
                    "reason": f"retry exhausted after {record.get('retry_count', 0)} attempts",
                }, log_path=os.path.join(self.data_dir, "strategies", "audit_log.json"))
                os.unlink(retry_path)
                result["retry_exhausted"] += 1
                continue

            result["attempted"] += 1

            oos_backtest = self._build_oos_backtest_evidence(maintainer, proposal, review_report)
            audit_result = audit_layer.review(
                proposal=proposal,
                changelog=getattr(maintainer, "changelog", []),
                oos_backtest=oos_backtest,
                risk_rules=self._read_text(os.path.join(self.data_dir, "references", "risk-rules.md")),
                current_portfolio=review_report.get("accounts", {}),
                recent_trades=self._load_recent_trades(),
                current_account=review_report.get("accounts", {}).get(proposal.get("account", ""), {}),
                llm_client=llm,
                audit_log_path=os.path.join(self.data_dir, "strategies", "audit_log.json"),
            )
            decision = audit_result.get("decision")
            if decision == "AUTO_MERGE":
                maintainer.commit_approved(proposal_id)
                os.unlink(retry_path)
                result["auto_merged"] += 1
            elif decision == "PENDING_RETRY":
                record["retry_count"] = record.get("retry_count", 0) + 1
                record["updated_at"] = datetime.now().isoformat()
                record["last_audit_result"] = audit_result
                self._atomic_write_json(retry_path, record)
                result["still_pending"] += 1
            elif decision == "HUMAN_REVIEW":
                record["status"] = "human_review"
                record["updated_at"] = datetime.now().isoformat()
                record["last_audit_result"] = audit_result
                self._atomic_write_json(retry_path, record)
                result["human_review"] += 1
            else:
                os.unlink(retry_path)
                result["terminal"] += 1

        return result
    
    # 辅助函数：数据格式化
    def _format_currency(self, amount: float) -> str:
        """格式化货币"""
        if amount >= 0:
            return f"+{amount:,.0f}元"
        else:
            return f"{amount:,.0f}元"
    
    def _format_percentage(self, value: float) -> str:
        """格式化百分比"""
        if value >= 0:
            return f"+{value:.2f}%"
        else:
            return f"{value:.2f}%"
    
    def _format_ratio(self, value: float) -> str:
        """格式化比率"""
        return f"{value:.1f}%"
    
    def _get_trend_emoji(self, value: float) -> str:
        """获取趋势emoji"""
        if value > 0:
            return "📈"
        elif value < 0:
            return "📉"
        else:
            return "➡️"
    
    def _get_priority_emoji(self, priority: str) -> str:
        """获取优先级emoji"""
        priority_map = {
            'high': '🔴',
            'medium': '🟡',
            'low': '🟢'
        }
        return priority_map.get(priority, '⚪')
        
    def _load_config(self, config_path: str = None) -> Dict:
        """加载配置"""
        if config_path is None:
            config_path = os.path.join(
                os.path.dirname(__file__), 
                "config.json"
            )
        
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"加载配置失败: {e}")
            return {}
    
    def _initialize_agents(self) -> Dict:
        """初始化各个Agent"""
        logger.info("初始化Agent...")
        
        agents = {
            'market_analyst': MarketAnalystAgent(),
            'execution_planner': ExecutionPlannerAgent(),
            'risk_controller': RiskControllerAgent(),
            'review_agent': ReviewAgent(),
            'strategy_maintainer': StrategyMaintainerAgent()
        }
        
        return agents
    
    def build_plan_record(self, planner, plan: Dict, validation_result: Dict) -> Dict:
        """Phase 0b：把 trading_plan + 校验结果装配成持久化 plan 记录。

        纯函数式：从 planner.compute_target_weights 取 main/lab 目标权重，
        随同 decision / total_position / actions 一并落进 workflow JSON 的
        plan 字段，供盘后 ExecutionModel 消费（diff→候选订单）。
        """
        position_sizing = plan.get('position_sizing', {}) or {}
        total_position = position_sizing.get('total_position', 1.0)
        if total_position is None:
            total_position = 1.0
        return {
            "generated_at": datetime.now().isoformat(),
            "market_regime": plan.get('market_regime'),
            "decision": validation_result.get('decision'),
            "total_position": total_position,
            "actions": plan.get('actions', []),
            "target_weights": {
                "main": planner.compute_target_weights('main', plan),
                "lab": planner.compute_target_weights('lab', plan),
            },
        }

    def _latest_pre_market_plan(self, date_str: str) -> Dict:
        """读今日盘前 workflow JSON 的 plan 字段（含 target_weights）。

        date_str 形如 'YYYY-MM-DD'，对应文件名 workflow_pre_market_YYYYMMDD_*.json。
        找不到/无 plan 字段时返回 {}。
        """
        workflows_dir = os.path.join(self.data_dir, "agents", "workflows")
        if not os.path.isdir(workflows_dir):
            return {}
        prefix = f"workflow_pre_market_{date_str.replace('-', '')}_"
        candidates = sorted(
            f for f in os.listdir(workflows_dir)
            if f.startswith(prefix) and f.endswith(".json")
        )
        if not candidates:
            return {}
        latest = os.path.join(workflows_dir, candidates[-1])
        data = self._read_json(latest, {})
        plan = data.get("plan", {}) if isinstance(data, dict) else {}
        return plan if isinstance(plan, dict) else {}

    def _account_risk_rules(self, account: str) -> Dict:
        """读 strategies/active.json 的 <acct>_strategy.rules.position_sizing 硬上限（G2）。"""
        active = self._read_json(
            os.path.join(self.data_dir, "strategies", "active.json"), {}
        )
        strat = active.get(f"{account}_strategy", {}) if isinstance(active, dict) else {}
        ps = (strat.get("rules", {}) or {}).get("position_sizing", {}) or {}
        return {
            "max_single_position": ps.get("max_single_position"),
            "max_sector_exposure": ps.get("max_sector_exposure"),
            "total_position_limit": ps.get("total_position_limit"),
        }

    def _account_equity_series(self, account: str):
        """从 strategies/performance_history.json 的日度 <acct>_pct 构建月度权益序列
        （G4 回撤/月亏熔断喂的"实时权益序列"，复用 h35 阈值数学）。

        返回 {YYYY-MM: [equity values...]} dict（按月分组，复利累乘 (1+pct)）。
        不可读 → None（G4 据此 graceful skip）。
        """
        hist = self._read_json(
            os.path.join(self.data_dir, "strategies", "performance_history.json"), None
        )
        if not isinstance(hist, list) or not hist:
            return None
        key = f"{account}_pct"
        by_month: Dict[str, List[float]] = {}
        equity = 1.0
        for row in hist:
            if not isinstance(row, dict):
                continue
            date = str(row.get("date", ""))
            month = date[:7]
            pct = row.get(key)
            if pct is None or not month:
                continue
            try:
                # <acct>_pct 以"百分数"存储（-0.78 == -0.78%），故 /100 复利，
                # 否则把 -0.78 当 -78%/日 会伪造 ~-90% 回撤、G4 永久 halt 自主买入。
                equity *= (1.0 + float(pct) / 100.0)
            except (TypeError, ValueError):
                continue
            by_month.setdefault(month, []).append(equity)
        return by_month or None

    def _account_realized_sells(self, account: str):
        """从 trades/<月>/*.json 收集该账户的卖出记录（含 realized_pnl），按日期排序。
        G4 连亏卖熔断喂的"已实现盈亏/卖出"。不可读 → None（graceful skip）。"""
        trades_root = os.path.join(self.data_dir, "trades")
        if not os.path.isdir(trades_root):
            return None
        sells: List[Dict] = []
        try:
            for month in sorted(os.listdir(trades_root)):
                mdir = os.path.join(trades_root, month)
                if not os.path.isdir(mdir):
                    continue
                for fname in sorted(os.listdir(mdir)):
                    if not fname.endswith(".json"):
                        continue
                    data = self._read_json(os.path.join(mdir, fname), {})
                    day = data.get("date") if isinstance(data, dict) else None
                    for t in (data.get("trades", []) if isinstance(data, dict) else []):
                        if not isinstance(t, dict) or t.get("account") != account:
                            continue
                        if t.get("action") != "sell":
                            continue
                        sells.append({
                            "date": t.get("date", day),
                            "action": "sell",
                            "realized_pnl": t.get("realized_pnl", t.get("pnl", 0)),
                        })
        except OSError:
            return None
        sells.sort(key=lambda s: str(s.get("date", "")))
        return sells

    def run_shadow_execution(self, date_str: str = None) -> Dict:
        """Backward-compat alias for run_autonomous_execution (existing callers/tests).

        Mode is read from config/execution.json; in shadow this is the prior
        Phase 2 behaviour (no real writes). Kept so callers that say "shadow"
        keep working regardless of config — see run_autonomous_execution.
        """
        return self.run_autonomous_execution(date_str)

    def run_autonomous_execution(self, date_str: str = None) -> Dict:
        """Phase 3 mode-aware controlled autonomous execution.

        Consumes today's pre-market plan.target_weights, runs ExecutionModel
        gate chain G0–G6 per account, then by config.mode:
          shadow → write shadow ledger only; executed==0; accounts/trades untouched.
          canary → apply_canary_caps on surviving (pass/clamp) orders, then
                   _execute_orders (real writes) + bookkeeping + rollback-on-fail.
          live   → _execute_orders (real writes, NO canary caps) + bookkeeping
                   + rollback-on-fail.
          halt   → gate chain halts everything (G6); no writes.

        Prices come from accounts/<acct>.json current_price (settlement-updated).
        For canary/live, after writes for the day re-finalize the perf entry +
        daily report (H2 fix) then _run_ledger_validation(strict=True); on
        FAILURE roll back this execution's account + trade writes and set
        summary['degraded_reason'].
        Never raises into the caller (caller wraps in try/except too).
        """
        from execution_model import ExecutionModel

        if date_str is None:
            date_str = datetime.now().strftime("%Y-%m-%d")

        plan = self._latest_pre_market_plan(date_str)
        plan_found = isinstance(plan, dict) and bool(plan.get("target_weights"))
        target_weights = plan.get("target_weights", {}) if isinstance(plan, dict) else {}
        decision = plan.get("decision") if isinstance(plan, dict) else None
        plan_date = date_str if plan_found else None

        exec_model = ExecutionModel(data_dir=self.data_dir, exec_config=None, mode=None)
        mode = exec_model.mode
        agg = {"pass": 0, "clamp": 0, "reject": 0, "halt": 0}
        summary = {
            "mode": mode,
            "date": date_str,
            "plan_found": plan_found,
            "accounts": {},
            "counts": agg,
        }

        # 收集每账户的 gate 报告 + 存活订单（pass/clamp），real write 在闸门之后统一做。
        per_account = {}
        for acct in ("main", "lab"):
            account_state = self._read_json(
                os.path.join(self.data_dir, "accounts", f"{acct}.json"), {}
            )
            prices = {
                pos.get("code"): pos.get("current_price", 0)
                for pos in account_state.get("positions", [])
                if pos.get("code")
            }
            acct_plan = {
                "decision": decision,
                "total_position": plan.get("total_position") if isinstance(plan, dict) else None,
                "target_weights": target_weights.get(acct, {}),
            }
            report = exec_model.execute_plan(
                acct, acct_plan, prices, account_state,
                rules=self._account_risk_rules(acct),
                date_str=date_str,
                plan_date=plan_date,
                equity_series=self._account_equity_series(acct),
                realized_sells=self._account_realized_sells(acct),
            )
            counts = report.get("counts", {})
            for k in agg:
                agg[k] += counts.get(k, 0)
            summary["accounts"][acct] = {
                "candidates": len(report.get("candidates", [])),
                "executed": report.get("executed", 0),
                "counts": counts,
                "decisions": report.get("decisions", []),
            }
            per_account[acct] = {
                "report": report,
                "prices": prices,
                "account_state": account_state,
            }

        if mode not in ("canary", "live"):
            # shadow/halt：execute_plan 已写影子账本（或全 halt），executed 恒 0。
            return summary

        # ── canary/live：把存活订单变成真实成交，随后记账 + 校验 + 回滚 ──────────
        self._execute_real_orders(date_str, mode, exec_model, per_account, summary)
        return summary

    def _execute_real_orders(self, date_str: str, mode: str, exec_model,
                             per_account: Dict, summary: Dict) -> None:
        """canary/live real-write orchestration with H2 bookkeeping + rollback.

        1) Capture pre-write account snapshots + whether today's trade file
           pre-existed (for exact rollback).
        2) Per account: take surviving (pass/clamp) orders; canary → apply
           canary caps (live skips); call _execute_orders to write.
        3) Re-finalize perf entry + daily report for today (H2), then
           _run_ledger_validation(strict=True). On failure → roll back all
           account/trade writes to the captured pre-write state and set
           summary['degraded_reason'].
        """
        import copy

        accounts_dir = os.path.join(self.data_dir, "accounts")
        trade_path = os.path.join(
            self.data_dir, "trades", date_str[:7], f"{date_str}.json")

        # 1) 预写快照（精确回滚用）
        pre_accounts = {}
        for acct in ("main", "lab"):
            p = os.path.join(accounts_dir, f"{acct}.json")
            pre_accounts[acct] = copy.deepcopy(self._read_json(p, None))
        trade_file_preexisted = os.path.exists(trade_path)
        pre_trade_record = None
        if trade_file_preexisted:
            pre_trade_record = copy.deepcopy(self._read_json(trade_path, None))
        # _refinalize_after_execution 还会改 perf_history 与日报 → 一并快照，
        # 否则回滚后会残留按"已回滚写入"算出的脏 perf 条目（G4 equity 序列的来源）。
        perf_path = os.path.join(self.data_dir, "strategies", "performance_history.json")
        perf_preexisted = os.path.exists(perf_path)
        pre_perf = copy.deepcopy(self._read_json(perf_path, None)) if perf_preexisted else None
        report_path = os.path.join(self.data_dir, "reports", "daily", f"{date_str}.md")
        report_preexisted = os.path.exists(report_path)
        pre_report = None
        if report_preexisted:
            with open(report_path, encoding="utf-8") as rf:
                pre_report = rf.read()

        # 2) 逐账户真实成交
        executed_any = False
        for acct in ("main", "lab"):
            decisions = per_account[acct]["report"].get("decisions", [])
            survivors = [d["order"] for d in decisions
                         if d.get("verdict") in ("pass", "clamp")]
            if not survivors:
                continue

            if mode == "canary":
                capped = exec_model.apply_canary_caps(
                    survivors, per_account[acct]["account_state"],
                    exec_model.canary_config())
                # 记录 canary cap 决策进 summary（留痕）
                summary["accounts"][acct]["canary_caps"] = [
                    {"code": c["order"].get("code"), "verdict": c["verdict"],
                     "reason": c["reason"]} for c in capped]
                survivors = [c["order"] for c in capped
                             if c.get("verdict") in ("pass", "clamp")]
                if not survivors:
                    continue

            result = self._execute_orders(
                acct, survivors, per_account[acct]["prices"],
                per_account[acct]["account_state"])
            trades = result.get("trades", [])
            if trades:
                executed_any = True
                summary["accounts"][acct]["executed"] = len(trades)
                summary["accounts"][acct]["executed_trades"] = trades

        if not executed_any:
            summary["ledger_validation_passed"] = True
            return

        # 3) H2 记账：重跑当日绩效 upsert + 日报，再 strict 校验
        try:
            self._refinalize_after_execution(date_str)
            validation = self._run_ledger_validation(strict=True)
        except Exception as e:
            validation = {"status": "error", "error": str(e), "failures": []}

        summary["ledger_validation"] = validation
        passed = validation.get("status") == "pass"
        summary["ledger_validation_passed"] = passed
        if passed:
            return

        # ── 校验失败 → 回滚本次写入（精确还原预写账户 + 撤销 trade 追加）──────────
        for acct in ("main", "lab"):
            p = os.path.join(accounts_dir, f"{acct}.json")
            if pre_accounts.get(acct) is not None:
                self._atomic_write_json(p, pre_accounts[acct])
        if trade_file_preexisted and pre_trade_record is not None:
            self._atomic_write_json(trade_path, pre_trade_record)
        elif os.path.exists(trade_path):
            os.unlink(trade_path)
        # 还原 perf_history（撤销 _refinalize 的 upsert）与日报，确保回滚后账本三方
        # 与执行前完全一致，不给 G4 留脏 equity 点。
        if perf_preexisted and pre_perf is not None:
            self._atomic_write_json(perf_path, pre_perf)
        elif os.path.exists(perf_path):
            os.unlink(perf_path)
        if report_preexisted and pre_report is not None:
            with open(report_path, "w", encoding="utf-8") as rf:
                rf.write(pre_report)
        elif os.path.exists(report_path):
            os.unlink(report_path)
        failed_invs = ", ".join(
            r.get("inv", "") for r in validation.get("failures", []))
        summary["degraded_reason"] = (
            f"ledger validation failed after autonomous execution "
            f"({failed_invs or validation.get('error', 'unknown')}) — "
            f"rolled back account/trade writes")
        # 回滚后实际未成交：执行计数清零，反映真实账本状态。
        for acct in ("main", "lab"):
            summary["accounts"][acct]["executed"] = 0
            summary["accounts"][acct]["rolled_back"] = True

    def _refinalize_after_execution(self, date_str: str) -> None:
        """H2 fix: after a canary/live write, make trade/perf/report three-way
        consistent for `date_str`, then they pass strict ledger validation.

        - Upsert the perf entry for date_str using the post-write account
          daily_pnl_pct (preserving existing hs300/benchmark fields). This
          satisfies INV-1/INV-3 (perf <acct>_pct == trade snapshot daily_pnl_pct,
          which _execute_orders wrote from the same account field).
        - Ensure the daily report exists for date_str (INV-2); the report reader
          prefers perf_entry pct so report == perf == trade (INV-3).
        """
        accounts = {}
        for acct in ("main", "lab"):
            accounts[acct] = self._read_json(
                os.path.join(self.data_dir, "accounts", f"{acct}.json"), {})

        perf_path = os.path.join(
            self.data_dir, "strategies", "performance_history.json")
        perf = self._read_json(perf_path, [])
        if not isinstance(perf, list):
            perf = []
        idx = next((i for i, e in enumerate(perf)
                    if isinstance(e, dict) and e.get("date") == date_str), None)
        entry = dict(perf[idx]) if idx is not None else {}
        entry["date"] = date_str
        entry["main_pct"] = accounts.get("main", {}).get("daily_pnl_pct", entry.get("main_pct", 0))
        entry["lab_pct"] = accounts.get("lab", {}).get("daily_pnl_pct", entry.get("lab_pct", 0))
        # F5: 记录日度部署仓位，供 detect_deployment_stall 监控资金部署停滞。
        entry["main_position_pct"] = accounts.get("main", {}).get("position_pct", entry.get("main_position_pct"))
        entry["lab_position_pct"] = accounts.get("lab", {}).get("position_pct", entry.get("lab_position_pct"))
        # 保留 settlement 已写的 benchmark（settlement 在执行之前已 fetch hs300）。
        # 若今日 perf 条目尚不存在/无 benchmark（如离线），给出 INV-4 可接受的
        # 已标注占位（hs300_pct=0 + benchmark_note），避免记账后 strict 校验误失败。
        hs = entry.get("hs300_pct")
        if hs is None:
            entry["hs300_pct"] = 0
            entry["benchmark_note"] = entry.get("benchmark_note") or "api_unavailable"
            entry["main_beat"] = None
            entry["lab_beat"] = None
        if idx is not None:
            perf[idx] = entry
        else:
            perf.append(entry)
        self._atomic_write_json(perf_path, perf)

        # trade snapshot daily_pnl_pct 必须与 perf 一致（_execute_orders 写的是
        # account.daily_pnl_pct，与上面 entry 同源）；此处确保 perf 与之对齐即可。
        self._ensure_daily_report_for_date(date_str)

    def run_pre_market_workflow(self) -> Dict:
        """运行盘前工作流"""
        logger.info("开始盘前工作流...")
        
        workflow_result = {
            'timestamp': datetime.now().isoformat(),
            'workflow_type': 'pre_market',
            'steps': [],
            'final_output': None,
            'status': 'success',
            'warnings': [],
            'events': [],
        }
        
        try:
            # 步骤1: Market Analyst Agent
            logger.info("步骤1: Market Analyst Agent")
            market_analysis = self.agents['market_analyst'].run_analysis()
            
            workflow_result['steps'].append({
                'step': 1,
                'agent': 'market_analyst',
                'status': 'success',
                'output': market_analysis.get('analysis_summary', '')
            })
            
            # 步骤2: Execution Planner Agent
            logger.info("步骤2: Execution Planner Agent")
            trading_plan = self.agents['execution_planner'].generate_trading_plan(market_analysis)
            
            workflow_result['steps'].append({
                'step': 2,
                'agent': 'execution_planner',
                'status': 'success',
                'output': self.agents['execution_planner'].generate_plan_summary(trading_plan)
            })
            
            # 步骤3: Risk Controller Agent
            logger.info("步骤3: Risk Controller Agent")
            validation_result = self.agents['risk_controller'].validate_trading_plan(trading_plan)
            
            workflow_result['steps'].append({
                'step': 3,
                'agent': 'risk_controller',
                'status': 'success',
                'output': self.agents['risk_controller'].generate_validation_summary(validation_result)
            })
            
            # Phase 0b: 把完整计划（含 target_weights + decision）持久化进 workflow JSON
            # 纯加法：装配失败仅记日志，不污染 workflow warnings/status（不改执行行为）。
            try:
                workflow_result['plan'] = self.build_plan_record(
                    self.agents['execution_planner'], trading_plan, validation_result,
                )
            except Exception as e:
                logger.error(f"装配 plan 记录失败: {e}")

            # 最终输出
            decision = validation_result.get('decision', 'REJECTED')
            workflow_result['risk_decision'] = decision
            
            if decision == 'APPROVED':
                _plan_record = workflow_result.get('plan') or {}
                final_output = self._generate_approved_output(
                    market_analysis, trading_plan, validation_result,
                    _plan_record.get('target_weights'),
                )
            elif decision == 'MODIFY':
                final_output = self._generate_modify_output(market_analysis, trading_plan, validation_result)
            else:
                final_output = self._generate_rejected_output(validation_result)
            
            workflow_result['final_output'] = final_output
            if decision == 'APPROVED':
                workflow_result['status'] = 'success'
            else:
                workflow_result['status'] = 'degraded'
                workflow_result['warnings'].extend(validation_result.get('warnings', []))
            
            logger.info("盘前工作流完成")
            
        except Exception as e:
            logger.error(f"盘前工作流失败: {e}")
            workflow_result['status'] = 'failed'
            workflow_result['error'] = str(e)
            workflow_result['final_output'] = f"盘前分析失败: {e}"
        
        return workflow_result
    
    def _generate_approved_output(self, market_analysis: Dict, trading_plan: Dict, validation_result: Dict, target_weights: Dict = None) -> str:
        """生成批准输出"""
        output_parts = []
        
        # 标题和时间
        output_parts.append("📊 虚拟盘盘前分析")
        output_parts.append("────────────────────────")
        output_parts.append(f"📅 日期: {datetime.now().strftime('%Y-%m-%d (%A)')}")
        output_parts.append(f"⏰ 时间: {datetime.now().strftime('%H:%M')} 北京时间")
        
        # 市场概览
        output_parts.append("")
        output_parts.append("🔍 市场概览")
        
        # 市场状态
        market_tone = market_analysis.get('market_tone', 'neutral')
        tone_map = {
            'bullish': '看涨 ⬆️',
            'bearish': '看跌 ⬇️',
            'neutral': '中性 ➡️'
        }
        output_parts.append(f"• 市场状态: {tone_map.get(market_tone, '未知')}")
        
        # 板块强度
        sector_strength = market_analysis.get('sector_strength', [])
        if sector_strength:
            top_sector = sector_strength[0]
            avg_change = top_sector.get('avg_change', 0)
            output_parts.append(f"• 最强板块: {top_sector.get('sector')} ({self._format_percentage(avg_change)})")
        
        # 风险信号
        risk_signals = market_analysis.get('risk_signals', [])
        if risk_signals:
            output_parts.append(f"• 风险信号: {len(risk_signals)}个 ⚠️")
        
        # 交易计划
        output_parts.append("")
        output_parts.append("📈 交易计划")
        
        # 市场判断
        confidence = trading_plan.get('confidence', 'medium')
        confidence_map = {
            'high': '高',
            'medium': '中',
            'low': '低'
        }
        output_parts.append(f"• 市场判断: {tone_map.get(market_tone, '未知')}")
        
        # 目标仓位（S2 honest reporting）：显示计划上限 + 各账户【实际部署】仓位。
        # 二者背离 >5pp 时给 idle-cash warning，杜绝“显示 55% 实际 15%”的失真
        # （AGENTS.md § Success Honesty）。
        position_sizing = trading_plan.get('position_sizing', {})
        total_position = position_sizing.get('total_position', 0) or 0
        output_parts.append(f"• 目标仓位(上限): {self._format_ratio(total_position * 100)}")
        if target_weights:
            acct_label = {'main': '主', 'lab': '实验'}
            deployed_parts = []
            max_gap = 0.0
            for acct in ('main', 'lab'):
                weights = target_weights.get(acct) or {}
                deployed = sum(weights.values())
                deployed_parts.append(f"{acct_label.get(acct, acct)} {self._format_ratio(deployed * 100)}")
                max_gap = max(max_gap, total_position - deployed)
            output_parts.append(f"• 实际部署: {' / '.join(deployed_parts)}")
            if max_gap > 0.05:
                output_parts.append("⚠️ 实际部署低于目标仓位，存在未配置的闲置现金")
        
        # 信心水平
        output_parts.append(f"• 信心水平: {confidence_map.get(confidence, '中')}")
        
        # 今日操作
        actions = trading_plan.get('actions', [])
        if actions:
            output_parts.append("")
            output_parts.append("🎯 今日操作")
            for i, action in enumerate(actions[:3], 1):  # 只显示前3个
                account = action.get('account', '')
                action_type = action.get('action', '')
                name = action.get('name', '')
                code = action.get('code', '')
                priority = action.get('priority', 'medium')
                reason = action.get('reason', '')
                
                priority_emoji = self._get_priority_emoji(priority)
                output_parts.append(f"{i}. {priority_emoji} {account}账户: {action_type} {name}")
                output_parts.append(f"   理由: {reason}")
        
        # 风险审查
        output_parts.append("")
        decision = validation_result.get('decision', 'APPROVED')
        decision_map = {
            'APPROVED': '通过 ✅',
            'MODIFY': '需要修改 ⚠️',
            'REJECTED': '拒绝 ❌'
        }
        output_parts.append(f"⚠️ 风险审查: {decision_map.get(decision, '未知')}")
        
        warnings = validation_result.get('warnings', [])
        if warnings:
            for warning in warnings[:2]:  # 只显示前2个
                output_parts.append(f"• {warning}")
        
        # LLM 洞察
        llm_insight = market_analysis.get('llm_insight', '')
        if llm_insight:
            output_parts.append("")
            output_parts.append("🧠 AI 洞察")
            output_parts.append(llm_insight)
        
        # 今日目标
        output_parts.append("")
        if decision == 'APPROVED':
            output_parts.append("🎯 今日目标: 执行交易计划，监控风险")
        elif decision == 'MODIFY':
            output_parts.append("🎯 今日目标: 修改交易计划后重新审查")
        else:
            output_parts.append("🎯 今日目标: 无交易，观察市场")
        
        output_parts.append("────────────────────────")
        
        return "\n".join(output_parts)
    
    def _generate_modify_output(self, market_analysis: Dict, trading_plan: Dict, validation_result: Dict) -> str:
        """生成修改输出"""
        output_parts = []
        
        # 标题和时间
        output_parts.append("📊 虚拟盘盘前分析")
        output_parts.append("────────────────────────")
        output_parts.append(f"📅 日期: {datetime.now().strftime('%Y-%m-%d (%A)')}")
        output_parts.append(f"⏰ 时间: {datetime.now().strftime('%H:%M')} 北京时间")
        
        # 市场概览
        output_parts.append("")
        output_parts.append("🔍 市场概览")
        
        # 市场状态
        market_tone = market_analysis.get('market_tone', 'neutral')
        tone_map = {
            'bullish': '看涨 ⬆️',
            'bearish': '看跌 ⬇️',
            'neutral': '中性 ➡️'
        }
        output_parts.append(f"• 市场状态: {tone_map.get(market_tone, '未知')}")
        
        # 板块强度
        sector_strength = market_analysis.get('sector_strength', [])
        if sector_strength:
            top_sector = sector_strength[0]
            avg_change = top_sector.get('avg_change', 0)
            output_parts.append(f"• 最强板块: {top_sector.get('sector')} ({self._format_percentage(avg_change)})")
        
        # 风险信号
        risk_signals = market_analysis.get('risk_signals', [])
        if risk_signals:
            output_parts.append(f"• 风险信号: {len(risk_signals)}个 ⚠️")
        
        # LLM 洞察
        llm_insight = market_analysis.get('llm_insight', '')
        if llm_insight:
            output_parts.append("")
            output_parts.append("🧠 AI 洞察")
            output_parts.append(llm_insight)
        
        # 风险审查
        output_parts.append("")
        output_parts.append("⚠️ 风险审查: 需要修改 ⚠️")
        
        warnings = validation_result.get('warnings', [])
        if warnings:
            for warning in warnings:
                output_parts.append(f"• {warning}")
        
        # 修改建议
        modifications = validation_result.get('modifications', [])
        if modifications:
            output_parts.append("")
            output_parts.append("🔧 修改建议")
            
            for mod in modifications:
                action = mod.get('action', '')
                code = mod.get('code', '')
                output_parts.append(f"• {action}: {code}")
        
        # 今日目标
        output_parts.append("")
        output_parts.append("🎯 今日目标: 修改交易计划后重新审查")
        output_parts.append("────────────────────────")
        
        return "\n".join(output_parts)
    
    def _generate_rejected_output(self, validation_result: Dict) -> str:
        """生成拒绝输出"""
        output_parts = []
        
        # 标题和时间
        output_parts.append("📊 虚拟盘盘前分析")
        output_parts.append("────────────────────────")
        output_parts.append(f"📅 日期: {datetime.now().strftime('%Y-%m-%d (%A)')}")
        output_parts.append(f"⏰ 时间: {datetime.now().strftime('%H:%M')} 北京时间")
        
        # LLM 洞察（从 workflow context 获取）
        output_parts.append("")
        output_parts.append("🧠 AI 洞察")
        output_parts.append("交易计划被风控拒绝，请优先解决风控问题后再考虑交易。")
        
        # 风险审查
        output_parts.append("")
        output_parts.append("⚠️ 风险审查: 拒绝 ❌")
        
        warnings = validation_result.get('warnings', [])
        if warnings:
            for warning in warnings:
                output_parts.append(f"• {warning}")
        
        # 今日目标
        output_parts.append("")
        output_parts.append("🎯 今日目标: 无交易，观察市场")
        output_parts.append("────────────────────────")
        
        return "\n".join(output_parts)
    
    def _iteration_stall_warning(self, current_decision: str):
        """S4: 聚合最近 post_market 的 audit_decision + 绩效史，返回自迭代停滞告警或 None。

        防御式：任何 I/O 异常都吞掉并返回 None，绝不影响盘后主流程（纯加法）。
        """
        try:
            wf_dir = os.path.join(self.data_dir, "agents", "workflows")
            decisions = []
            files = sorted(
                f for f in os.listdir(wf_dir)
                if f.startswith("workflow_post_market_") and f.endswith(".json")
            )
            for fn in files[-15:]:
                dec = ""
                try:
                    with open(os.path.join(wf_dir, fn), encoding="utf-8") as f:
                        w = json.load(f)
                    for s in w.get("steps", []):
                        if s.get("agent") == "strategy_maintainer":
                            dec = s.get("audit_decision", "")
                except Exception:
                    dec = ""
                decisions.append(dec)
            decisions.append(current_decision)  # 本次决策尚未落盘，手动并入
            perf = self._read_json(
                os.path.join(self.data_dir, "strategies", "performance_history.json"), []
            )
            return detect_iteration_stall(decisions, perf)
        except Exception as e:
            logger.error(f"自迭代停滞检测失败: {e}")
            return None

    def _deployment_stall_warning(self):
        """F5: 读 performance_history 的 main_position_pct 史 + 策略 floor，
        返回资金部署停滞告警或 None。

        防御式：任何 I/O 异常都吞掉并返回 None，绝不影响盘后主流程（纯加法）。
        与 audit_decision 无关——部署停滞应无条件评估，不挂在 NO_CHANGES 分支下。
        """
        try:
            perf = self._read_json(
                os.path.join(self.data_dir, "strategies", "performance_history.json"), []
            )
            history = [e.get("main_position_pct") for e in perf if isinstance(e, dict)]
            active = self._read_json(
                os.path.join(self.data_dir, "strategies", "active.json"), {}
            )
            floor = (
                active.get("main_strategy", {})
                .get("rules", {})
                .get("position_sizing", {})
                .get("total_position_floor")
            )
            return detect_deployment_stall(history, floor)
        except Exception as e:
            logger.error(f"资金部署停滞检测失败: {e}")
            return None

    def run_post_market_workflow(self) -> Dict:
        """运行盘后工作流"""
        logger.info("开始盘后工作流...")
        
        workflow_result = {
            'timestamp': datetime.now().isoformat(),
            'workflow_type': 'post_market',
            'steps': [],
            'final_output': None,
            'status': 'success',
            'warnings': [],
            'events': [],
        }
        
        try:
            # 步骤0: 日终结算（mark-to-market）
            logger.info("步骤0: 日终结算")
            try:
                settlement = self.run_settlement()
                workflow_result['settlement'] = settlement
                step_status = 'success'
                if not settlement.get('accounts_updated', False):
                    reason = settlement.get('degraded_reason', 'no price updates')
                    workflow_result['warnings'].append(f"结算: {reason}")
                    step_status = 'degraded'
                if settlement.get('ledger_validation_passed') is False:
                    reason = settlement.get('degraded_reason', 'ledger validation failed')
                    workflow_result['warnings'].append(f"结算: {reason}")
                    step_status = 'degraded'
                workflow_result['steps'].append({
                    'step': 0,
                    'agent': 'settlement',
                    'status': step_status,
                    'output': f"主账户: {settlement.get('main_value', 0):,.0f} | 实验: {settlement.get('lab_value', 0):,.0f}"
                })
            except Exception as e:
                logger.error(f"结算失败: {e}")
                workflow_result['warnings'].append(f"日终结算失败: {e}")
                workflow_result['settlement'] = {'error': str(e)}
            
            # 步骤1: Review Agent
            logger.info("步骤1: Review Agent")
            daily_data = self.agents['review_agent'].load_daily_data()
            review_report = self.agents['review_agent'].generate_review_report(daily_data)
            
            # 检查 review 中的 mistakes
            mistakes = review_report.get('mistakes', [])
            risk_mistake_count = sum(
                1
                for mistake in mistakes
                if str(mistake.get("type", "")).lower() in {"risk", "risk_control"}
            )
            other_mistake_count = len(mistakes) - risk_mistake_count
            risk_handled_count = 0
            risk_unresolved_count = 0
            
            workflow_result['steps'].append({
                'step': 1,
                'agent': 'review_agent',
                'status': 'success',
                'output': self.agents['review_agent'].generate_review_summary(review_report)
            })
            
            # 步骤1.5: 风控减仓检查（reduce-only）
            if 'risk_controller' in self.agents:
                logger.info("步骤1.5: 风控减仓检查")
                pending_execution = self._process_pending_risk_actions()
                if pending_execution.get("executed") or pending_execution.get("failed"):
                    workflow_result['pending_risk_action_execution'] = pending_execution
                    if pending_execution.get("executed"):
                        risk_handled_count += pending_execution["executed"]
                        workflow_result['events'].append(f"{pending_execution['executed']} 条待处理风控减仓已自动执行")
                    if pending_execution.get("failed"):
                        risk_unresolved_count += pending_execution["failed"]
                        workflow_result['warnings'].append(f"{pending_execution['failed']} 条待处理风控减仓自动执行失败")
                risk_ctrl = self.agents['risk_controller']
                reduction_result = risk_ctrl.validate_and_reduce()
                risk_actions = reduction_result.get('risk_reduction_actions', [])
                if risk_actions:
                    for action in risk_actions:
                        logger.warning(f"风控减仓建议: {action.get('name', '')} - {action.get('reason', '')}")
                    workflow_result['risk_reduction_actions'] = risk_actions
                    execution_result = self._process_risk_actions(risk_actions)
                    workflow_result['risk_action_execution'] = execution_result
                    workflow_result['risk_action_queue'] = execution_result.get('queued', {})
                    if execution_result.get('executed'):
                        risk_handled_count += execution_result["executed"]
                        workflow_result['events'].append(f"{execution_result['executed']} 条风控减仓已自动执行")
                    queued_count = workflow_result['risk_action_queue'].get('added', 0)
                    if queued_count:
                        risk_unresolved_count += queued_count
                        workflow_result['warnings'].append(f"{queued_count} 条减仓建议待执行")
                    if execution_result.get('failed'):
                        risk_unresolved_count += execution_result["failed"]
                        workflow_result['warnings'].append(f"{execution_result['failed']} 条风控减仓自动执行失败")

            unresolved_risk_review_mistakes = max(0, risk_mistake_count - risk_handled_count)
            if risk_mistake_count:
                unresolved_risk_count = max(unresolved_risk_review_mistakes, risk_unresolved_count)
            else:
                unresolved_risk_count = risk_unresolved_count
            unresolved_mistakes = other_mistake_count + unresolved_risk_count
            if unresolved_mistakes:
                workflow_result['warnings'].append(f"{unresolved_mistakes} 个风控问题待处理")

            # 步骤1.6: 受控自主执行（Phase 3 · 模式感知 shadow/canary/live）
            # 消费盘前 plan.target_weights → 候选订单 → 过闸；
            #   shadow → 写影子账本，绝不碰 accounts/trades；
            #   canary/live → 真实成交 + 记账（H2）+ strict 校验 + 失败回滚。
            # try/except 包裹，绝不崩 workflow（mirror settlement 容错）。
            try:
                execution_summary = self.run_autonomous_execution()
                workflow_result['execution'] = execution_summary
                total_candidates = sum(
                    a.get('candidates', 0) for a in execution_summary.get('accounts', {}).values()
                )
                executed = sum(
                    a.get('executed', 0) for a in execution_summary.get('accounts', {}).values()
                )
                gc = execution_summary.get('counts', {})
                passed = gc.get('pass', 0) + gc.get('clamp', 0)
                vetoed = gc.get('reject', 0) + gc.get('halt', 0)
                mode = execution_summary.get('mode')
                workflow_result['events'].append(
                    f"自主执行: {mode} | 候选{total_candidates} | "
                    f"过{passed} | 否决{vetoed}（夹{gc.get('clamp', 0)}/拒{gc.get('reject', 0)}/"
                    f"停{gc.get('halt', 0)}）| 成交{executed}"
                )
                # Phase 4 (修假绿): persist compact execution_decisions view +
                # surface degraded/rolled-back/halt as a warning (shadow rejects
                # alone do NOT add a warning).
                self._apply_execution_observability(workflow_result)
            except Exception as e:
                logger.error(f"自主执行失败: {e}")
                workflow_result['warnings'].append(f"自主执行失败: {e}")
                workflow_result['execution'] = {'error': str(e)}

            # 步骤2: Strategy Maintainer Agent
            logger.info("步骤2: Strategy Maintainer Agent")
            maintainer = self.agents['strategy_maintainer']
            retry_result = self._retry_pending_audit_proposals(maintainer, review_report)
            if (
                retry_result.get("attempted")
                or retry_result.get("human_review")
                or retry_result.get("retry_exhausted")
            ):
                workflow_result['pending_retry_audit'] = retry_result
                if retry_result.get("still_pending"):
                    workflow_result['warnings'].append(f"{retry_result['still_pending']} 个审计重试仍待处理")
                if retry_result.get("human_review"):
                    workflow_result['warnings'].append(f"{retry_result['human_review']} 个审计重试需要人工确认")
                if retry_result.get("retry_exhausted"):
                    workflow_result['warnings'].append(f"{retry_result['retry_exhausted']} 个审计重试已耗尽")
            performance_analysis = maintainer.analyze_strategy_performance(review_report)
            adjustments = maintainer.generate_strategy_adjustments(performance_analysis)
            apply_result = self._audit_strategy_adjustments(
                maintainer=maintainer,
                adjustments=adjustments,
                review_report=review_report,
            )
            update_report = maintainer.generate_strategy_update_report(performance_analysis, adjustments, apply_result)
            
            # 检查审计决策
            audit_decision = apply_result.get('audit_decision', '')
            if audit_decision == 'BLOCKED':
                workflow_result['warnings'].append("策略变更被阻止（LLM不可用）")
            elif audit_decision == 'PENDING_RETRY':
                workflow_result['warnings'].append("审计层 INFRA_ERROR，待重试")
            elif audit_decision == 'AUTO_REJECT':
                workflow_result['warnings'].append("策略变更被审计层拒绝")
            elif audit_decision == 'NO_CHANGES':
                stall_msg = self._iteration_stall_warning(audit_decision)
                if stall_msg:
                    workflow_result['warnings'].append(stall_msg)

            # F5: 资金部署停滞告警（与 audit_decision 无关，无条件评估）
            deploy_msg = self._deployment_stall_warning()
            if deploy_msg:
                workflow_result['warnings'].append(deploy_msg)

            workflow_result['steps'].append({
                'step': 2,
                'agent': 'strategy_maintainer',
                'status': 'success',
                'output': update_report.get('summary', ''),
                'audit_decision': audit_decision,
            })

            # Risk reductions write trade/account files after settlement. Re-run
            # ledger validation so the persisted workflow reflects the final
            # ledger state, not the pre-risk-execution settlement snapshot.
            if self._collect_risk_execution_results(workflow_result):
                try:
                    daily_report = self._ensure_daily_report_for_date(datetime.now().strftime("%Y-%m-%d"))
                    workflow_result['daily_report'] = daily_report
                    if daily_report.get("created"):
                        workflow_result['events'].append("自动补全盘后日报以满足最终账本校验")
                    final_validation = self._run_ledger_validation(strict=False)
                    workflow_result['final_ledger_validation'] = final_validation
                    workflow_result['final_ledger_validation_passed'] = final_validation.get('status') == 'pass'
                    if not workflow_result['final_ledger_validation_passed']:
                        failed_invs = ", ".join(r.get('inv', '') for r in final_validation.get('failures', []))
                        workflow_result['warnings'].append(f"最终账本校验失败: {failed_invs}")
                except Exception as e:
                    workflow_result['final_ledger_validation'] = {"status": "error", "error": str(e), "failures": []}
                    workflow_result['final_ledger_validation_passed'] = False
                    workflow_result['warnings'].append(f"最终账本校验异常: {e}")
            
            # 最终状态判定
            if workflow_result['warnings']:
                workflow_result['status'] = 'degraded'
            
            # 最终输出
            final_output = self._generate_post_market_output(review_report, update_report, workflow_result)
            workflow_result['final_output'] = final_output
            
            logger.info(f"盘后工作流完成 (status={workflow_result['status']})")
            
        except Exception as e:
            logger.error(f"盘后工作流失败: {e}")
            workflow_result['status'] = 'failed'
            workflow_result['error'] = str(e)
            workflow_result['final_output'] = f"盘后复盘失败: {e}"
        
        return workflow_result

    def run_settlement(self) -> Dict:
        """日终结算：mark-to-market 所有持仓，更新账户和绩效历史。
        
        即使今日无交易也必须执行。用收盘价更新持仓市值。
        """
        import subprocess
        from datetime import datetime
        
        # 收集所有持仓代码
        all_codes = []
        accounts = {}
        for acct_type in ['main', 'lab']:
            path = os.path.join(self.data_dir, "accounts", f"{acct_type}.json")
            try:
                with open(path, 'r') as f:
                    acct = json.load(f)
                accounts[acct_type] = acct
                for pos in acct.get('positions', []):
                    all_codes.append(pos['code'])
            except Exception as e:
                logger.error(f"加载账户失败 {acct_type}: {e}")
                accounts[acct_type] = None
        
        # 用腾讯 API 获取收盘价
        prices = {}
        if all_codes:
            # 转换代码格式：600xxx→sh600xxx, 000xxx/002xxx→sz000xxx, 688xxx→sh688xxx, 300xxx→sz300xxx
            tx_codes = []
            for code in all_codes:
                if code.startswith('6'):
                    tx_codes.append(f'sh{code}')
                else:
                    tx_codes.append(f'sz{code}')
            
            try:
                url = f"https://qt.gtimg.cn/q={','.join(tx_codes)}"
                r = subprocess.run(['curl', '-s', '-m', '10', url], capture_output=True, timeout=15)
                text = r.stdout.decode('gbk', errors='replace')
                for line in text.strip().split(';'):
                    if '~' not in line:
                        continue
                    parts = line.split('~')
                    if len(parts) >= 38:
                        code = parts[2]
                        price = float(parts[3]) if parts[3] else 0
                        if price > 0:
                            prices[code] = price
            except Exception as e:
                logger.error(f"获取收盘价失败: {e}")
                raise
        
        # 更新账户
        results = {}
        today = datetime.now().strftime('%Y-%m-%d')
        
        for acct_type, acct in accounts.items():
            if acct is None:
                continue
            
            old_total = acct.get('total_value', 0)
            updated = False
            for pos in acct.get('positions', []):
                code = pos['code']
                if code in prices:
                    price = prices[code]
                    pos['current_price'] = price
                    pos['market_value'] = pos['shares'] * price
                    pos['unrealized_pnl'] = pos['market_value'] - pos['shares'] * pos['avg_cost']
                    pos['unrealized_pnl_pct'] = round((price / pos['avg_cost'] - 1) * 100, 2)
                    updated = True
            
            if updated:
                acct['portfolio_market_value'] = sum(p['market_value'] for p in acct.get('positions', []))
                acct['total_value'] = acct['portfolio_market_value'] + acct.get('cash', 0)
                acct['total_pnl'] = acct['total_value'] - acct.get('initial_capital', 0)
                acct['total_pnl_pct'] = round(acct['total_pnl'] / acct.get('initial_capital', 1) * 100, 2)
                acct['position_pct'] = round(acct['portfolio_market_value'] / acct['total_value'] * 100, 1) if acct['total_value'] > 0 else 0
                # Calculate real daily return from price change
                if old_total > 0:
                    acct['daily_pnl'] = acct['total_value'] - old_total
                    acct['daily_pnl_pct'] = round(acct['daily_pnl'] / old_total * 100, 2)
                acct['updated_at'] = f'{today}T15:00:00'
                
                # 写回文件
                path = os.path.join(self.data_dir, "accounts", f"{acct_type}.json")
                self._atomic_write_json(path, acct)
                
                results[f'{acct_type}_value'] = acct['total_value']
                results[f'{acct_type}_pnl'] = acct.get('daily_pnl', 0)
                logger.info(f"结算完成 {acct_type}: {acct['total_value']:,.0f}")
        
        # 更新绩效历史
        try:
            perf_path = os.path.join(self.data_dir, "strategies", "performance_history.json")
            perf = []
            if os.path.exists(perf_path):
                with open(perf_path) as f:
                    perf = json.load(f)
            
            main_acct = accounts.get('main')
            lab_acct = accounts.get('lab')
            # 获取沪深300涨跌幅；失败时保持 unknown，避免把占位 0 当成真实 benchmark。
            hs300_pct = None
            benchmark_note = None
            try:
                url = "https://qt.gtimg.cn/q=sh000300"
                r = subprocess.run(['curl', '-s', '-m', '10', url], capture_output=True, timeout=15)
                text = r.stdout.decode('gbk', errors='replace')
                for line in text.strip().split(';'):
                    if '~' in line:
                        parts = line.split('~')
                        if len(parts) >= 38 and parts[32]:
                            hs300_pct = float(parts[32])
                            break
            except Exception:
                benchmark_note = "api_unavailable"
            if hs300_pct is None and benchmark_note is None:
                benchmark_note = "api_unavailable"

            existing_idx = next((i for i, e in enumerate(perf) if e.get('date') == today), None)
            existing_entry = perf[existing_idx] if existing_idx is not None else None
            if hs300_pct is None and existing_entry and existing_entry.get('hs300_pct') is not None:
                hs300_pct = existing_entry.get('hs300_pct')
                benchmark_note = None
            
            entry = {
                'date': today,
                'main_pct': main_acct.get('daily_pnl_pct', 0) if main_acct else 0,
                'lab_pct': lab_acct.get('daily_pnl_pct', 0) if lab_acct else 0,
                'hs300_pct': hs300_pct,
            }
            if hs300_pct is None:
                entry['benchmark_note'] = benchmark_note
                entry['main_beat'] = None
                entry['lab_beat'] = None
            else:
                entry['benchmark_source'] = "tencent_api"
                entry['main_beat'] = entry['main_pct'] >= hs300_pct
                entry['lab_beat'] = entry['lab_pct'] >= hs300_pct
            
            # Upsert by date — replace existing entry for today, don't duplicate
            if existing_idx is not None:
                perf[existing_idx] = entry
            else:
                perf.append(entry)
            
            self._atomic_write_json(perf_path, perf)
            
            results['performance_updated'] = True
        except Exception as e:
            logger.warning(f"更新绩效历史失败: {e}")
            results['performance_updated'] = False
        
        # accounts_updated only if we actually got new prices
        results['accounts_updated'] = any(f'{a}_value' in results for a in ['main', 'lab'])
        if not results['accounts_updated']:
            results['degraded_reason'] = 'no price updates received'
        try:
            validation = self._run_ledger_validation(strict=False)
            results['ledger_validation'] = validation
            results['ledger_validation_passed'] = validation['status'] == 'pass'
            if not results['ledger_validation_passed']:
                failed_invs = ", ".join(r['inv'] for r in validation['failures'])
                reason = f"ledger validation failed: {failed_invs}"
                if results.get('degraded_reason'):
                    results['degraded_reason'] = f"{results['degraded_reason']}; {reason}"
                else:
                    results['degraded_reason'] = reason
                logger.warning(reason)
        except Exception as e:
            reason = f"ledger validation error: {e}"
            results['ledger_validation'] = {"status": "error", "error": str(e), "failures": []}
            results['ledger_validation_passed'] = False
            if results.get('degraded_reason'):
                results['degraded_reason'] = f"{results['degraded_reason']}; {reason}"
            else:
                results['degraded_reason'] = reason
            logger.warning(reason)
        return results

    def _audit_strategy_adjustments(self, maintainer, adjustments: List[Dict], review_report: Dict) -> Dict:
        """Audit strategy adjustments before any write to active.json/changelog."""
        if not adjustments:
            return {
                'applied_adjustments': [],
                'failed_adjustments': [],
                'changelog_entries': [],
                'audit_decision': 'NO_CHANGES',
                'audit_reason': 'no strategy adjustments proposed',
            }

        # P1 guard: never pass None llm_client to audit_layer
        llm = getattr(maintainer, "llm", None)
        if llm is None:
            logger.error("LLM 不可用，策略变更被阻止（不允许绕过审计层）")
            return {
                'applied_adjustments': [],
                'failed_adjustments': adjustments,
                'changelog_entries': [],
                'audit_decision': 'BLOCKED',
                'audit_reason': 'LLM client unavailable — audit layer cannot run, changes blocked by policy',
            }

        proposal = maintainer.propose(adjustments)
        oos_backtest = self._build_oos_backtest_evidence(maintainer, proposal, review_report)
        audit_log_path = os.path.join(self.data_dir, "strategies", "audit_log.json")
        audit_result = audit_layer.review(
            proposal=proposal,
            changelog=getattr(maintainer, "changelog", []),
            oos_backtest=oos_backtest,
            risk_rules=self._read_text(os.path.join(self.data_dir, "references", "risk-rules.md")),
            current_portfolio=review_report.get("accounts", {}),
            recent_trades=self._load_recent_trades(),
            current_account=review_report.get("accounts", {}).get(proposal.get("account", ""), {}),
            llm_client=llm,
            audit_log_path=audit_log_path,
        )

        decision = audit_result.get("decision")
        apply_result = {
            'applied_adjustments': [],
            'failed_adjustments': [],
            'changelog_entries': [],
            'audit_decision': decision,
            'audit_reason': audit_result.get("reason", ""),
            'proposal_id': proposal.get("proposal_id"),
            'audit_result': audit_result,
        }

        if decision == "AUTO_MERGE":
            maintainer.commit_approved(proposal["proposal_id"])
            apply_result['applied_adjustments'] = adjustments
        elif decision == "PENDING_RETRY":
            self._persist_pending_retry_proposal(proposal, audit_result)
            apply_result['failed_adjustments'] = adjustments
        else:
            apply_result['failed_adjustments'] = adjustments

        return apply_result

    def _build_oos_backtest_evidence(self, maintainer, proposal: Dict, review_report: Dict) -> Dict:
        """Build current-vs-proposed OOS evidence for audit_layer.review()."""
        try:
            from backtest.market_data import (
                AkshareProvider,
                BaoStockProvider,
                CachedPriceProvider,
                FallbackMarketDataProvider,
                TushareProvider,
                YFinanceProvider,
            )
            from backtest.oos_window import compute_oos_window
            from backtest.strategy_simulator import build_oos_evidence, code_to_ticker
        except Exception as exc:
            return {"status": "INFRA_ERROR", "reason": "OOS_IMPORT_FAILED", "error": str(exc)}

        account = proposal.get("account", "main")
        calendar = getattr(self, "trading_calendar", None) or self._derive_trading_calendar()
        window = compute_oos_window(
            getattr(maintainer, "changelog", []),
            account,
            datetime.now().strftime("%Y-%m-%d"),
            calendar,
        )
        if window.get("status") != "OK":
            return window

        watchlist = getattr(self, "watchlist", None) or self._read_json(
            os.path.join(self.data_dir, "market-data", "watchlist.json"),
            {"stocks": []},
        )
        account_tag = "main" if account == "main" else "lab"
        tickers = [
            code_to_ticker(row["code"])
            for row in watchlist.get("stocks", [])
            if row.get("code") and row.get("tag") in {account, account_tag}
        ]
        tickers.append("000300.SS")
        provider = getattr(self, "market_data_provider", None)
        if provider is None:
            provider = FallbackMarketDataProvider([
                CachedPriceProvider(os.path.join(self.data_dir, "market-data", "cache")),
                TushareProvider(),
                AkshareProvider(),
                BaoStockProvider(),
                YFinanceProvider(),
            ])

        price_result = provider.get_close_prices(sorted(set(tickers)), window["start"], window["end"])
        if price_result.status != "OK":
            return {
                "status": "INFRA_ERROR",
                "reason": price_result.reason or "NO_PRICE_DATA",
                "sources_tried": price_result.sources_tried,
                "missing_symbols": price_result.missing_symbols,
            }

        strategy_key = f"{account}_strategy"
        current_strategy = getattr(maintainer, "strategies", {}).get(strategy_key)
        if not current_strategy:
            return {
                "status": "INFRA_ERROR",
                "reason": "STRATEGY_NOT_FOUND",
                "strategy_key": strategy_key,
            }

        return build_oos_evidence(
            current_strategy,
            proposal,
            watchlist,
            price_result.prices,
            window,
            data_meta={
                "sources_tried": price_result.sources_tried,
                "sources_used": price_result.sources_used,
                "missing_symbols": price_result.missing_symbols,
                "cache_hit_ratio": price_result.cache_hit_ratio,
                "cache_oldest_age_days": price_result.cache_oldest_age_days,
                "adjustment": price_result.adjustment,
            },
        )

    def _read_text(self, path: str) -> str:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            return ""

    def _read_json(self, path: str, default):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return default

    def _derive_trading_calendar(self) -> List[str]:
        trades_dir = os.path.join(self.data_dir, "trades")
        trade_days = set()
        if os.path.isdir(trades_dir):
            for dirpath, _, filenames in os.walk(trades_dir):
                for filename in filenames:
                    if filename.endswith(".json"):
                        trade_days.add(filename[:-5])

        candidate_starts = [datetime.now() - timedelta(days=90)]
        if trade_days:
            candidate_starts.append(datetime.strptime(min(trade_days), "%Y-%m-%d"))

        changelog = self._read_json(os.path.join(self.data_dir, "strategies", "changelog.json"), [])
        changelog_dates = [
            entry.get("date")
            for entry in changelog
            if isinstance(entry, dict) and entry.get("date")
        ]
        if changelog_dates:
            candidate_starts.append(datetime.strptime(min(changelog_dates), "%Y-%m-%d"))

        start = min(candidate_starts)

        end = datetime.now()
        calendar = []
        current = start
        while current <= end:
            if current.weekday() < 5:
                calendar.append(current.strftime("%Y-%m-%d"))
            current += timedelta(days=1)
        return calendar

    def _load_recent_trades(self) -> List[Dict]:
        trades_dir = os.path.join(self.data_dir, "trades")
        recent_trades = []
        if not os.path.isdir(trades_dir):
            return recent_trades

        for dirpath, _, filenames in os.walk(trades_dir):
            for filename in filenames:
                if not filename.endswith(".json"):
                    continue
                path = os.path.join(dirpath, filename)
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except (OSError, json.JSONDecodeError):
                    continue
                if isinstance(data, list):
                    recent_trades.extend(data)
                elif isinstance(data, dict):
                    recent_trades.append(data)

        return recent_trades[-200:]
    
    def _collect_risk_execution_results(self, workflow_result: Dict) -> List[Dict]:
        """Collect auto risk execution results and enrich names from source actions."""
        workflow_result = workflow_result or {}
        action_lookup = {}
        for action in workflow_result.get("risk_reduction_actions", []):
            key = (action.get("account"), action.get("code"))
            action_lookup[key] = action

        results = []
        for section in ("pending_risk_action_execution", "risk_action_execution"):
            execution = workflow_result.get(section, {})
            for result in execution.get("results", []):
                item = dict(result)
                action = item.get("action", {})
                lookup = action_lookup.get((item.get("account"), item.get("code")), {})
                item.setdefault("account", action.get("account"))
                item.setdefault("code", action.get("code"))
                item.setdefault("name", action.get("name") or lookup.get("name", ""))
                item.setdefault("reason", action.get("reason") or lookup.get("reason", ""))
                results.append(item)
        return results

    def _apply_execution_observability(self, workflow_result: Dict) -> None:
        """Phase 4 (修假绿): derive a compact, persisted execution_decisions view
        from the autonomous-execution summary and surface execution truth.

        - workflow_result['execution_decisions'] = build_execution_decisions(summary)
          (the full 'execution' summary is kept untouched).
        - Add a warning ONLY for degraded/rolled-back/halt — shadow rejecting
          candidates is normal and must NOT add a warning (would falsely flip
          a passing post-market run to 'degraded').
        Never raises into the caller.
        """
        try:
            from execution_model import (
                build_execution_decisions,
                _is_halted,
                _is_rolled_back_or_degraded,
            )
            summary = workflow_result.get('execution')
            if not isinstance(summary, dict):
                return
            workflow_result['execution_decisions'] = build_execution_decisions(summary)
            if summary.get('error'):
                return
            warnings = workflow_result.setdefault('warnings', [])
            if _is_rolled_back_or_degraded(summary):
                reason = summary.get('degraded_reason', '已回滚/降级')
                warnings.append(f"自主执行已回滚/降级: {reason}")
            elif _is_halted(summary):
                warnings.append("自主执行已停 (kill-switch/halt) — 仅确定性 reduce-only 运行")
        except Exception as e:  # pragma: no cover - defensive, never crash workflow
            logger.error(f"execution observability 失败: {e}")

    def _generate_post_market_output(self, review_report: Dict, update_report: Dict, workflow_result: Dict = None) -> str:
        """生成盘后输出"""
        output_parts = []
        workflow_result = workflow_result or {}
        
        # P3: 显示警告
        warnings = workflow_result.get('warnings', [])
        if warnings:
            output_parts.append("⚠️ 警告")
            for w in warnings:
                output_parts.append(f"  • {w}")
            output_parts.append("")

        events = workflow_result.get('events', [])
        if events:
            output_parts.append("✅ 自动处置")
            for event in events:
                output_parts.append(f"  • {event}")
            output_parts.append("")

        # Phase 4 (修假绿): execution-truth block — make halt/rollback/shadow
        # unambiguous so cron "ok" never masks rejected-everything / rolled-back.
        execution_summary = workflow_result.get('execution')
        if isinstance(execution_summary, dict):
            try:
                from execution_model import format_execution_summary
                exec_block = format_execution_summary(execution_summary)
                if exec_block:
                    output_parts.append(exec_block)
                    output_parts.append("")
            except Exception as e:  # pragma: no cover - never crash output
                logger.error(f"format_execution_summary 失败: {e}")

        # P2: 显示减仓建议
        risk_actions = workflow_result.get('risk_reduction_actions', [])
        if risk_actions:
            output_parts.append("🔴 风控减仓建议（reduce-only）")
            for action in risk_actions:
                output_parts.append(f"  • {action.get('account','')}/{action.get('name','')}({action.get('code','')}): {action.get('reason','')}")
            output_parts.append("")

        risk_execution_results = self._collect_risk_execution_results(workflow_result)
        if risk_execution_results:
            output_parts.append("✅ 自动风控执行")
            for result in risk_execution_results:
                account = result.get("account", "")
                code = result.get("code", "")
                name = result.get("name", "")
                shares = result.get("executed_shares", 0)
                price = result.get("price", 0)
                if result.get("ok"):
                    display_name = f"{name} ({code})" if name else code
                    output_parts.append(f"  • {account}账户 卖出 {display_name}: {shares}股 @ {price:.2f}元")
                    if result.get("net_amount") is not None:
                        output_parts.append(f"     到账: {result.get('net_amount', 0):,.0f}元")
                else:
                    error = result.get("error", "unknown error")
                    output_parts.append(f"  • {account}/{code}: 自动执行失败 - {error}")
            output_parts.append("")
        
        # 标题和时间
        output_parts.append("📊 虚拟盘盘后复盘")
        output_parts.append("────────────────────────")
        output_parts.append(f"📅 日期: {datetime.now().strftime('%Y-%m-%d (%A)')}")
        output_parts.append(f"⏰ 时间: {datetime.now().strftime('%H:%M')} 北京时间")
        
        # 今日绩效
        output_parts.append("")
        output_parts.append("💰 今日绩效")
        
        performance = review_report.get('performance', {})
        daily_return = performance.get('daily_return', {})
        
        total_pnl = 0
        for account_type, data in daily_return.items():
            pnl = data.get('pnl', 0)
            total_pnl += pnl
            emoji = self._get_trend_emoji(pnl)
            pnl_str = self._format_currency(pnl)
            output_parts.append(f"• {account_type}账户: {pnl_str} {emoji}")
        
        # 合计
        if len(daily_return) > 1:
            emoji = self._get_trend_emoji(total_pnl)
            total_str = self._format_currency(total_pnl)
            output_parts.append(f"• 合计: {total_str} {emoji}")
        
        # 持仓状况
        output_parts.append("")
        output_parts.append("📊 持仓状况")
        
        # 从账户数据获取持仓信息
        accounts = review_report.get('accounts', {})
        total_value = 0
        total_positions = 0
        
        for account_type, account in accounts.items():
            total_value += account.get('total_value', 0)
            positions = account.get('positions', [])
            total_positions += len(positions)
        
        output_parts.append(f"• 总资产: {total_value:,.0f}元")
        output_parts.append(f"• 持仓数量: {total_positions}只")
        
        # 持仓详情
        if accounts:
            output_parts.append("")
            output_parts.append("📋 持仓详情")
            
            for account_type, account in accounts.items():
                output_parts.append(f"\n【{account_type}账户】")
                output_parts.append(f"  总资产: {account.get('total_value', 0):,.0f}元 | 现金: {account.get('cash', 0):,.0f}元")
                
                positions = account.get('positions', [])
                if positions:
                    # 按盈亏排序
                    sorted_positions = sorted(positions, key=lambda x: x.get('unrealized_pnl', 0), reverse=True)
                    
                    # 显示前5个持仓
                    for i, pos in enumerate(sorted_positions[:5], 1):
                        name = pos.get('name', '')
                        code = pos.get('code', '')
                        shares = pos.get('shares', 0)
                        avg_cost = pos.get('avg_cost', 0)
                        current_price = pos.get('current_price', 0)
                        market_value = pos.get('market_value', 0)
                        unrealized_pnl = pos.get('unrealized_pnl', 0)
                        unrealized_pnl_pct = pos.get('unrealized_pnl_pct', 0)
                        
                        # 计算仓位占比
                        position_pct = (market_value / account.get('total_value', 1)) * 100
                        
                        # 盈亏emoji
                        pnl_emoji = self._get_trend_emoji(unrealized_pnl)
                        
                        output_parts.append(f"  {i}. {name} ({code})")
                        output_parts.append(f"     数量: {shares}股 | 成本: {avg_cost:.2f}元 | 现价: {current_price:.2f}元")
                        output_parts.append(f"     市值: {market_value:,.0f}元 ({position_pct:.1f}%) | 盈亏: {self._format_currency(unrealized_pnl)} ({self._format_percentage(unrealized_pnl_pct)}) {pnl_emoji}")
                    
                    # 如果持仓超过5个，显示提示
                    if len(positions) > 5:
                        output_parts.append(f"  ... (共{len(positions)}只持仓，显示前5只)")
                else:
                    output_parts.append("  (空仓)")
        
        # 今日交易
        output_parts.append("")
        output_parts.append("🔄 今日交易")
        
        trade_analysis = review_report.get('trade_analysis', {})
        total_trades = trade_analysis.get('total_trades', 0)
        buy_trades = trade_analysis.get('buy_trades', 0)
        sell_trades = trade_analysis.get('sell_trades', 0)
        
        if total_trades > 0:
            output_parts.append(f"• 交易笔数: {total_trades}笔")
            output_parts.append(f"• 买入: {buy_trades}笔")
            output_parts.append(f"• 卖出: {sell_trades}笔")
            
            # 显示交易详情
            trade_details = trade_analysis.get('trade_details', [])
            if trade_details:
                output_parts.append("")
                output_parts.append("📝 交易详情")
                
                for i, trade in enumerate(trade_details[:3], 1):  # 只显示前3笔
                    account = trade.get('account', '')
                    action = trade.get('action', '')
                    name = trade.get('name', '')
                    code = trade.get('code', '')
                    price = trade.get('price', 0)
                    shares = trade.get('shares', 0)
                    amount = trade.get('amount', 0)
                    signal = trade.get('signal', '')
                    
                    action_map = {
                        'buy': '买入',
                        'sell': '卖出'
                    }
                    
                    output_parts.append(f"  {i}. {account}账户 {action_map.get(action, action)} {name} ({code})")
                    output_parts.append(f"     价格: {price:.2f}元 | 数量: {shares}股 | 金额: {amount:,.0f}元")
                    if signal:
                        output_parts.append(f"     信号: {signal}")
                
                if len(trade_details) > 3:
                    output_parts.append(f"  ... (共{len(trade_details)}笔交易，显示前3笔)")
        elif risk_execution_results:
            executed_count = sum(1 for result in risk_execution_results if result.get("ok"))
            output_parts.append(f"• 自动风控交易: {executed_count}笔")
        else:
            output_parts.append("• 今日无交易")
        
        # 错误识别
        mistakes = review_report.get('mistakes', [])
        if mistakes:
            output_parts.append("")
            output_parts.append(f"⚠️ 错误识别 ({len(mistakes)}个)")
            
            for mistake in mistakes[:3]:  # 只显示前3个
                mistake_type = mistake.get('type', '')
                description = mistake.get('description', '')
                impact = mistake.get('impact', '')
                prevention = mistake.get('prevention', '')
                
                impact_map = {
                    'high': '高',
                    'medium': '中',
                    'low': '低'
                }
                
                output_parts.append(f"❌ {description}")
                output_parts.append(f"   影响: {impact_map.get(impact, '中')}")
                if prevention:
                    output_parts.append(f"   建议: {prevention}")
        
        # 有效做法
        lessons = review_report.get('lessons', {})
        what_worked = lessons.get('what_worked', [])
        
        if what_worked:
            output_parts.append("")
            output_parts.append(f"✅ 有效做法 ({len(what_worked)}个)")
            
            for item in what_worked[:3]:  # 只显示前3个
                output_parts.append(f"• {item}")
        
        # 策略更新
        adjustments_applied = update_report.get('adjustments_applied', [])
        if adjustments_applied:
            output_parts.append("")
            output_parts.append("🔧 策略更新")
            
            for adj in adjustments_applied:
                parameter = adj.get('parameter', adj.get('rule', ''))
                old_value = adj.get('old_value', '')
                new_value = adj.get('new_value', '')
                reason = adj.get('reason', '')
                
                # 格式化参数名称
                param_map = {
                    'max_single_position': '单笔最大仓位',
                    'stop_loss_pct': '止损比例',
                    'take_profit_pct': '止盈比例'
                }
                param_name = param_map.get(parameter, parameter)
                
                output_parts.append(f"• {param_name}: {old_value} → {new_value}")
                output_parts.append(f"  原因: {reason}")
        
        # Changelog
        changelog_entries = update_report.get('changelog_entries', [])
        if changelog_entries:
            output_parts.append("")
            output_parts.append(f"📝 Changelog: {len(changelog_entries)}条记录已保存")
        
        output_parts.append("────────────────────────")
        
        return "\n".join(output_parts)
    
    def run_weekly_review_workflow(self) -> Dict:
        """运行周末复盘工作流"""
        logger.info("开始周末复盘工作流...")
        
        # 周末复盘是盘后复盘的扩展版
        # 可以复用盘后工作流，但增加更多分析
        
        workflow_result = self.run_post_market_workflow()
        workflow_result['workflow_type'] = 'weekly_review'
        
        # 添加周末特有的分析
        workflow_result['final_output'] = "📊 周末深度复盘\n" + workflow_result.get('final_output', '')
        
        return workflow_result
    
    def save_workflow_result(self, result: Dict, filename: str = None):
        """保存工作流结果"""
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            workflow_type = result.get('workflow_type', 'unknown')
            filename = f"workflow_{workflow_type}_{timestamp}.json"
        
        filepath = os.path.join(self.data_dir, "agents", "workflows", filename)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            logger.info(f"工作流结果已保存: {filepath}")
        except Exception as e:
            logger.error(f"保存工作流结果失败: {e}")

    def sync_to_site(self) -> Dict:
        """Fire-and-forget site sync. Errors are isolated, never raise."""
        import subprocess
        script = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "scripts", "sync_to_site.py",
        )
        try:
            proc = subprocess.run(
                [sys.executable, script],
                capture_output=True, text=True, timeout=180,
            )
            if proc.returncode == 0:
                try:
                    return json.loads(proc.stdout.strip().splitlines()[-1])
                except (json.JSONDecodeError, IndexError):
                    return {"success": False, "error": "unparseable output", "stdout": proc.stdout[-200:]}
            else:
                return {"success": False, "error": f"exit {proc.returncode}", "stderr": proc.stderr[-200:]}
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "timeout after 180s"}
        except FileNotFoundError:
            return {"success": False, "error": f"script not found: {script}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description='多Agent协调器')
    parser.add_argument('--workflow', type=str, required=True, 
                       choices=['pre_market', 'post_market', 'weekly_review'],
                       help='工作流类型')
    
    args = parser.parse_args()
    
    # 创建协调器
    coordinator = MultiAgentCoordinator()
    
    # 运行工作流
    if args.workflow == 'pre_market':
        result = coordinator.run_pre_market_workflow()
    elif args.workflow == 'post_market':
        result = coordinator.run_post_market_workflow()
    elif args.workflow == 'weekly_review':
        result = coordinator.run_weekly_review_workflow()
    else:
        print(f"未知工作流类型: {args.workflow}")
        return
    
    # 打印结果
    print("\n" + "="*50)
    print(result.get('final_output', '无输出'))
    print("="*50)
    
    # 保存结果
    coordinator.save_workflow_result(result)

    # 自动同步到网站 (fire-and-forget, errors logged but never fail the workflow)
    try:
        site_result = coordinator.sync_to_site()
        if not site_result.get("success"):
            logger.warning(f"站点同步未完全成功: {site_result.get('error', 'unknown')}")
    except Exception as e:
        logger.warning(f"站点同步调用异常(已隔离): {e}")

if __name__ == "__main__":
    main()
