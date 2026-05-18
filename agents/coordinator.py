#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
多Agent协调器
负责协调各个Agent的执行流程
"""

import json
import os

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
from strategy_maintainer import StrategyMaintainerAgent
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
            if expires_at and datetime.now() > datetime.fromisoformat(expires_at):
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
        return dt.weekday() < 5

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
            
            # 最终输出
            decision = validation_result.get('decision', 'REJECTED')
            workflow_result['risk_decision'] = decision
            
            if decision == 'APPROVED':
                final_output = self._generate_approved_output(market_analysis, trading_plan, validation_result)
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
    
    def _generate_approved_output(self, market_analysis: Dict, trading_plan: Dict, validation_result: Dict) -> str:
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
        
        # 目标仓位
        position_sizing = trading_plan.get('position_sizing', {})
        total_position = position_sizing.get('total_position', 0)
        output_parts.append(f"• 目标仓位: {self._format_ratio(total_position * 100)}")
        
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
            
            workflow_result['steps'].append({
                'step': 2,
                'agent': 'strategy_maintainer',
                'status': 'success',
                'output': update_report.get('summary', ''),
                'audit_decision': audit_decision,
            })
            
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
            
            with open(perf_path, 'w') as f:
                json.dump(perf, f, ensure_ascii=False, indent=2)
            
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

if __name__ == "__main__":
    main()
