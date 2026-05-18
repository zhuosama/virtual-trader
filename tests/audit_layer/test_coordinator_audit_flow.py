import os
import sys
import tempfile
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

import pandas as pd


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "agents"))


class FakeReviewAgent:
    def load_daily_data(self):
        return {"daily": "data"}

    def generate_review_report(self, daily_data):
        return {"daily_data": daily_data, "performance": {}, "accounts": {}}

    def generate_review_summary(self, review_report):
        return "review summary"


class FakeReviewAgentWithMistakes(FakeReviewAgent):
    def generate_review_report(self, daily_data):
        return {
            "daily_data": daily_data,
            "performance": {},
            "accounts": {},
            "mistakes": [
                {"type": "risk", "code": "000651"},
            ],
        }


class FakeReviewAgentWithNonRiskMistake(FakeReviewAgent):
    def generate_review_report(self, daily_data):
        return {
            "daily_data": daily_data,
            "performance": {},
            "accounts": {},
            "mistakes": [
                {"type": "data", "description": "benchmark missing"},
            ],
        }


class FakeStrategyMaintainer:
    def __init__(self):
        self.llm = MagicMock()
        self.changelog = [{"date": "2026-03-31", "account": "main", "change_type": "strategy"}]
        self.strategies = {
            "main_strategy": {
                "parameters": {
                    "breakout_lookback": 3,
                    "take_profit_pct": 15,
                    "stop_loss_pct": 7,
                    "max_single_position": 0.10,
                    "time_stop_days": 7,
                },
                "rules": {
                    "position_sizing": {
                        "initial_position": 0.10,
                        "max_single_position": 0.10,
                        "total_position_limit": 0.8,
                    }
                },
            }
        }
        self.apply_adjustments = MagicMock(
            side_effect=AssertionError("run_post_market_workflow must not bypass audit")
        )
        self.commit_approved = MagicMock()

    def analyze_strategy_performance(self, review_report):
        return {"confidence": "medium"}

    def generate_strategy_adjustments(self, performance_analysis):
        return [{
            "type": "parameter_adjustment",
            "strategy": "main",
            "parameter": "take_profit_pct",
            "old_value": 15,
            "new_value": 12,
            "reason": "three-event signal",
            "triggering_event_count": 3,
        }]

    def propose(self, adjustments):
        self.proposed_adjustments = adjustments
        return {
            "proposal_id": "proposal-001",
            "proposed_at": "2026-05-14T00:00:00Z",
            "proposer": "strategy_maintainer",
            "account": "main",
            "change_type": "strategy",
            "current_version": "1.0.5",
            "proposed_version": "1.0.5.pending",
            "diff": [{"path": "main_strategy.parameters.take_profit_pct", "old": 15, "new": 3}],
            "triggering_events": [{"e": 1}, {"e": 2}, {"e": 3}],
            "rationale": "three-event signal",
        }

    def generate_strategy_update_report(self, performance_analysis, adjustments, apply_result):
        self.apply_result = apply_result
        return {"summary": f"audit {apply_result['audit_decision']}"}


class FakeRiskControllerWithActions:
    def validate_and_reduce(self):
        return {
            "validation": {"decision": "APPROVED"},
            "risk_reduction_actions": [{
                "account": "main",
                "action": "sell",
                "code": "000651",
                "name": "格力电器",
                "reason": "仓位12.9%超过10%限制",
                "type": "risk_reduction",
                "current_pct": 0.129,
                "target_pct": 0.10,
            }],
        }


class FakeRiskControllerNoActions:
    def validate_and_reduce(self):
        return {
            "validation": {"decision": "APPROVED"},
            "risk_reduction_actions": [],
        }


class FakeRiskControllerExecutableActions:
    def validate_and_reduce(self):
        return {
            "validation": {"decision": "APPROVED"},
            "risk_reduction_actions": [{
                "account": "main",
                "action": "sell",
                "code": "000651",
                "name": "格力电器",
                "reason": "仓位12.9%超过10%限制",
                "type": "risk_reduction",
                "current_pct": 0.129,
                "target_pct": 0.10,
                "sell_shares": 800,
                "auto_execute": True,
            }],
        }


class TestCoordinatorAuditFlow(unittest.TestCase):
    def _coordinator_for_post_market(self, tmpdir):
        import coordinator

        c = coordinator.MultiAgentCoordinator.__new__(coordinator.MultiAgentCoordinator)
        c.data_dir = tmpdir
        c.run_settlement = MagicMock(return_value={
            "accounts_updated": True,
            "main_value": 1000000,
            "lab_value": 300000,
            "performance_updated": True,
            "ledger_validation_passed": True,
        })
        c._audit_strategy_adjustments = MagicMock(return_value={
            "audit_decision": "NO_CHANGES",
            "applied_adjustments": [],
        })
        c.agents = {
            "review_agent": FakeReviewAgent(),
            "risk_controller": FakeRiskControllerWithActions(),
            "strategy_maintainer": FakeStrategyMaintainer(),
        }
        return c

    def test_post_market_persists_risk_actions_to_pending_queue(self):
        import json

        tmpdir = tempfile.mkdtemp()
        c = self._coordinator_for_post_market(tmpdir)

        result = c.run_post_market_workflow()

        queue_path = os.path.join(tmpdir, "actions", "pending.json")
        with open(queue_path) as f:
            queued = json.load(f)
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0]["status"], "proposed")
        self.assertEqual(queued[0]["account"], "main")
        self.assertEqual(queued[0]["code"], "000651")
        self.assertEqual(queued[0]["type"], "risk_reduction")
        self.assertIn("action_id", queued[0])
        self.assertIn("generated_at", queued[0])
        self.assertEqual(result["risk_action_queue"]["added"], 1)

    def test_pending_risk_action_expiry_skips_weekend(self):
        import coordinator
        import json

        class FrozenDatetime(datetime):
            @classmethod
            def now(cls):
                return cls(2026, 5, 16, 22, 11, 6)  # Saturday

        tmpdir = tempfile.mkdtemp()
        c = coordinator.MultiAgentCoordinator.__new__(coordinator.MultiAgentCoordinator)
        c.data_dir = tmpdir

        with patch("coordinator.datetime", FrozenDatetime):
            c._persist_risk_actions([{
                "account": "main",
                "action": "sell",
                "code": "000651",
                "type": "risk_reduction",
            }])

        queue_path = os.path.join(tmpdir, "actions", "pending.json")
        with open(queue_path) as f:
            queued = json.load(f)
        self.assertEqual(queued[0]["expires_at"], "2026-05-18T23:59:59")

    def test_post_market_does_not_duplicate_open_risk_actions(self):
        import json

        tmpdir = tempfile.mkdtemp()
        c = self._coordinator_for_post_market(tmpdir)

        c.run_post_market_workflow()
        result = c.run_post_market_workflow()

        queue_path = os.path.join(tmpdir, "actions", "pending.json")
        with open(queue_path) as f:
            queued = json.load(f)
        self.assertEqual(len(queued), 1)
        self.assertEqual(result["risk_action_queue"]["added"], 0)
        self.assertEqual(result["risk_action_queue"]["existing_open"], 1)

    def test_post_market_auto_executes_deterministic_risk_actions(self):
        tmpdir = tempfile.mkdtemp()
        c = self._coordinator_for_post_market(tmpdir)
        c.agents["risk_controller"] = FakeRiskControllerExecutableActions()
        c._is_trading_day = MagicMock(return_value=True)
        c._execute_risk_action = MagicMock(return_value={
            "ok": True,
            "action_id": "risk-20260516-main-000651-risk_reduction-sell",
            "account": "main",
            "code": "000651",
            "name": "格力电器",
            "executed_shares": 800,
            "price": 39.71,
            "net_amount": 31768,
        })

        result = c.run_post_market_workflow()

        c._execute_risk_action.assert_called_once()
        self.assertEqual(result["risk_action_execution"]["executed"], 1)
        self.assertEqual(result["risk_action_queue"]["added"], 0)
        self.assertFalse(any("减仓建议待执行" in w for w in result["warnings"]))

    def test_successful_auto_risk_execution_does_not_degrade_post_market(self):
        tmpdir = tempfile.mkdtemp()
        c = self._coordinator_for_post_market(tmpdir)
        c.agents["review_agent"] = FakeReviewAgentWithMistakes()
        c.agents["risk_controller"] = FakeRiskControllerExecutableActions()
        c._is_trading_day = MagicMock(return_value=True)
        c._execute_risk_action = MagicMock(return_value={
            "ok": True,
            "action_id": "risk-20260516-main-000651-risk_reduction-sell",
            "account": "main",
            "code": "000651",
            "name": "格力电器",
            "executed_shares": 800,
            "price": 39.71,
            "net_amount": 31768,
        })

        result = c.run_post_market_workflow()

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["warnings"], [])
        self.assertTrue(any("风控减仓已自动执行" in e for e in result["events"]))
        self.assertIn("自动风控执行", result["final_output"])
        self.assertIn("格力电器 (000651)", result["final_output"])
        self.assertNotIn("今日无交易", result["final_output"])

    def test_post_market_runs_final_ledger_validation_after_auto_risk_execution(self):
        tmpdir = tempfile.mkdtemp()
        c = self._coordinator_for_post_market(tmpdir)
        c.agents["risk_controller"] = FakeRiskControllerExecutableActions()
        c._is_trading_day = MagicMock(return_value=True)
        c._execute_risk_action = MagicMock(return_value={
            "ok": True,
            "action_id": "risk-20260516-main-000651-risk_reduction-sell",
            "account": "main",
            "code": "000651",
            "name": "格力电器",
            "executed_shares": 800,
            "price": 39.71,
            "net_amount": 31768,
        })
        c._run_ledger_validation = MagicMock(return_value={
            "status": "pass",
            "failures": [],
            "results": [
                {"inv": "INV-1", "status": "PASS", "msg": "all 23 trade dates have perf entry"},
            ],
        })

        result = c.run_post_market_workflow()

        c._run_ledger_validation.assert_called_once_with(strict=False)
        self.assertTrue(result["final_ledger_validation_passed"])
        self.assertEqual(result["final_ledger_validation"]["results"][0]["msg"], "all 23 trade dates have perf entry")

    def test_post_market_queues_auto_risk_actions_on_non_trading_day(self):
        tmpdir = tempfile.mkdtemp()
        c = self._coordinator_for_post_market(tmpdir)
        c.agents["risk_controller"] = FakeRiskControllerExecutableActions()
        c._is_trading_day = MagicMock(return_value=False)
        c._execute_risk_action = MagicMock()

        result = c.run_post_market_workflow()

        c._execute_risk_action.assert_not_called()
        self.assertEqual(result["risk_action_execution"]["executed"], 0)
        self.assertEqual(result["risk_action_queue"]["added"], 1)
        self.assertTrue(any("减仓建议待执行" in w for w in result["warnings"]))

    def test_unresolved_risk_queue_counts_alongside_non_risk_mistakes(self):
        tmpdir = tempfile.mkdtemp()
        c = self._coordinator_for_post_market(tmpdir)
        c.agents["review_agent"] = FakeReviewAgentWithNonRiskMistake()
        c.agents["risk_controller"] = FakeRiskControllerExecutableActions()
        c._is_trading_day = MagicMock(return_value=False)
        c._execute_risk_action = MagicMock()

        result = c.run_post_market_workflow()

        self.assertTrue(any("2 个风控问题待处理" in w for w in result["warnings"]))

    def test_post_market_executes_pending_auto_risk_actions_on_trading_day(self):
        import json

        tmpdir = tempfile.mkdtemp()
        actions_dir = os.path.join(tmpdir, "actions")
        os.makedirs(actions_dir)
        pending_path = os.path.join(actions_dir, "pending.json")
        with open(pending_path, "w") as f:
            json.dump([{
                "action_id": "risk-queued-001",
                "account": "main",
                "action": "sell",
                "code": "000651",
                "name": "格力电器",
                "type": "risk_reduction",
                "status": "proposed",
                "auto_execute": True,
                "sell_shares": 800,
            }], f)

        c = self._coordinator_for_post_market(tmpdir)
        c.agents["risk_controller"] = FakeRiskControllerNoActions()
        c._is_trading_day = MagicMock(return_value=True)
        c._execute_risk_action = MagicMock(return_value={
            "ok": True,
            "action_id": "risk-queued-001",
            "executed_shares": 800,
        })

        result = c.run_post_market_workflow()

        c._execute_risk_action.assert_called_once()
        self.assertEqual(result["pending_risk_action_execution"]["executed"], 1)
        with open(pending_path) as f:
            queued = json.load(f)
        self.assertEqual(queued[0]["status"], "completed")
        self.assertEqual(queued[0]["execution_result"]["executed_shares"], 800)

    def test_pending_risk_execution_output_preserves_action_name(self):
        import json

        tmpdir = tempfile.mkdtemp()
        actions_dir = os.path.join(tmpdir, "actions")
        os.makedirs(actions_dir)
        pending_path = os.path.join(actions_dir, "pending.json")
        with open(pending_path, "w") as f:
            json.dump([{
                "action_id": "risk-queued-001",
                "account": "main",
                "action": "sell",
                "code": "000651",
                "name": "格力电器",
                "type": "risk_reduction",
                "status": "proposed",
                "auto_execute": True,
                "sell_shares": 800,
            }], f)

        c = self._coordinator_for_post_market(tmpdir)
        c.agents["risk_controller"] = FakeRiskControllerNoActions()
        c._is_trading_day = MagicMock(return_value=True)
        c._execute_risk_action = MagicMock(return_value={
            "ok": True,
            "action_id": "risk-queued-001",
            "account": "main",
            "code": "000651",
            "executed_shares": 800,
            "price": 39.71,
        })

        result = c.run_post_market_workflow()

        self.assertIn("格力电器 (000651)", result["final_output"])

    def test_pending_risk_action_uses_current_account_price(self):
        import coordinator
        import json

        tmpdir = tempfile.mkdtemp()
        actions_dir = os.path.join(tmpdir, "actions")
        os.makedirs(actions_dir)
        pending_path = os.path.join(actions_dir, "pending.json")
        with open(pending_path, "w") as f:
            json.dump([{
                "action_id": "risk-queued-001",
                "account": "main",
                "action": "sell",
                "code": "000651",
                "type": "risk_reduction",
                "status": "proposed",
                "auto_execute": True,
                "sell_shares": 800,
                "price": 40.28,
                "expires_at": "2026-05-18T23:59:59",
            }], f)

        class FrozenDatetime(datetime):
            @classmethod
            def now(cls):
                return cls(2026, 5, 18, 15, 0, 0)

        c = coordinator.MultiAgentCoordinator.__new__(coordinator.MultiAgentCoordinator)
        c.data_dir = tmpdir
        c._is_trading_day = MagicMock(return_value=True)
        c._execute_risk_action = MagicMock(return_value={"ok": True})

        with patch("coordinator.datetime", FrozenDatetime):
            c._process_pending_risk_actions()

        c._execute_risk_action.assert_called_once()
        self.assertFalse(c._execute_risk_action.call_args.kwargs["use_action_price"])

    def test_expired_pending_risk_action_is_not_executed(self):
        import coordinator
        import json

        tmpdir = tempfile.mkdtemp()
        actions_dir = os.path.join(tmpdir, "actions")
        os.makedirs(actions_dir)
        pending_path = os.path.join(actions_dir, "pending.json")
        with open(pending_path, "w") as f:
            json.dump([{
                "action_id": "risk-expired-001",
                "account": "main",
                "action": "sell",
                "code": "000651",
                "type": "risk_reduction",
                "status": "proposed",
                "auto_execute": True,
                "sell_shares": 800,
                "expires_at": "2026-05-18T23:59:59",
            }], f)

        class FrozenDatetime(datetime):
            @classmethod
            def now(cls):
                return cls(2026, 5, 19, 15, 0, 0)

        c = coordinator.MultiAgentCoordinator.__new__(coordinator.MultiAgentCoordinator)
        c.data_dir = tmpdir
        c._is_trading_day = MagicMock(return_value=True)
        c._execute_risk_action = MagicMock()

        with patch("coordinator.datetime", FrozenDatetime):
            result = c._process_pending_risk_actions()

        c._execute_risk_action.assert_not_called()
        self.assertEqual(result["skipped"], 1)
        with open(pending_path) as f:
            queued = json.load(f)
        self.assertEqual(queued[0]["status"], "expired")

    def test_execute_risk_action_updates_virtual_account_and_trade_record(self):
        import coordinator
        import json

        tmpdir = tempfile.mkdtemp()
        accounts_dir = os.path.join(tmpdir, "accounts")
        os.makedirs(accounts_dir)
        account = {
            "id": "main",
            "initial_capital": 1000000,
            "cash": 100000,
            "positions": [{
                "code": "000651",
                "name": "格力电器",
                "type": "stock",
                "shares": 3200,
                "avg_cost": 37.4553,
                "current_price": 40.28,
                "market_value": 128896,
                "unrealized_pnl": 9039.04,
                "unrealized_pnl_pct": 7.54,
            }],
            "portfolio_market_value": 128896,
            "total_value": 228896,
            "trade_count": 0,
        }
        with open(os.path.join(accounts_dir, "main.json"), "w") as f:
            json.dump(account, f)

        c = coordinator.MultiAgentCoordinator.__new__(coordinator.MultiAgentCoordinator)
        c.data_dir = tmpdir

        result = c._execute_risk_action({
            "account": "main",
            "action": "sell",
            "code": "000651",
            "name": "格力电器",
            "type": "risk_reduction",
            "sell_shares": 800,
            "price": 40.28,
            "reason": "仓位超限",
        })

        self.assertTrue(result["ok"])
        self.assertEqual(result["executed_shares"], 800)
        with open(os.path.join(accounts_dir, "main.json")) as f:
            updated = json.load(f)
        self.assertEqual(updated["positions"][0]["shares"], 2400)
        self.assertGreater(updated["cash"], 100000)
        self.assertEqual(updated["trade_count"], 1)

        trade_path = result["trade_record_path"]
        with open(trade_path) as f:
            trade_record = json.load(f)
        self.assertEqual(trade_record["trades"][-1]["action"], "sell")
        self.assertEqual(trade_record["trades"][-1]["shares"], 800)
        self.assertEqual(trade_record["trades"][-1]["source"], "auto_risk_reduction")

    def test_pending_retry_audit_proposal_is_persisted(self):
        import coordinator
        import json

        tmpdir = tempfile.mkdtemp()
        c = coordinator.MultiAgentCoordinator.__new__(coordinator.MultiAgentCoordinator)
        c.data_dir = tmpdir
        c._build_oos_backtest_evidence = MagicMock(return_value={"status": "OK"})
        c._load_recent_trades = MagicMock(return_value=[])

        maintainer = FakeStrategyMaintainer()
        maintainer.llm = MagicMock()
        maintainer.data_dir = tmpdir
        adjustments = maintainer.generate_strategy_adjustments({"confidence": "medium"})

        with patch.object(coordinator.audit_layer, "review", return_value={
            "decision": "PENDING_RETRY",
            "reason": "INFRA_ERROR",
            "proposal_id": "proposal-001",
        }):
            result = c._audit_strategy_adjustments(maintainer, adjustments, {"accounts": {}})

        self.assertEqual(result["audit_decision"], "PENDING_RETRY")
        retry_path = os.path.join(tmpdir, "strategies", "proposals", "pending_retry", "proposal-001.json")
        with open(retry_path) as f:
            retry_record = json.load(f)
        self.assertEqual(retry_record["proposal_id"], "proposal-001")
        self.assertEqual(retry_record["status"], "pending_retry")
        self.assertEqual(retry_record["retry_count"], 0)
        self.assertEqual(retry_record["proposal"]["proposal_id"], "proposal-001")

    def test_retry_pending_audit_proposal_auto_merge_commits_and_clears(self):
        import coordinator
        import json

        tmpdir = tempfile.mkdtemp()
        pending_dir = os.path.join(tmpdir, "strategies", "proposals", "pending_retry")
        os.makedirs(pending_dir)
        proposal = {
            "proposal_id": "proposal-001",
            "proposed_at": "2026-05-14T00:00:00Z",
            "proposer": "strategy_maintainer",
            "account": "main",
            "change_type": "strategy",
            "current_version": "1.0.5",
            "proposed_version": "1.0.5.pending",
            "diff": [],
            "triggering_events": [{"reason": "test"}],
            "rationale": "test",
        }
        with open(os.path.join(pending_dir, "proposal-001.json"), "w") as f:
            json.dump({
                "proposal_id": "proposal-001",
                "status": "pending_retry",
                "retry_count": 1,
                "proposal": proposal,
            }, f)

        c = coordinator.MultiAgentCoordinator.__new__(coordinator.MultiAgentCoordinator)
        c.data_dir = tmpdir
        c._build_oos_backtest_evidence = MagicMock(return_value={"status": "OK"})
        c._load_recent_trades = MagicMock(return_value=[])
        maintainer = FakeStrategyMaintainer()
        maintainer.llm = MagicMock()

        with patch.object(coordinator.audit_layer, "review", return_value={
            "decision": "AUTO_MERGE",
            "reason": "retry passed",
            "proposal_id": "proposal-001",
        }):
            result = c._retry_pending_audit_proposals(maintainer, {"accounts": {}})

        self.assertEqual(result["attempted"], 1)
        self.assertEqual(result["auto_merged"], 1)
        maintainer.commit_approved.assert_called_once_with("proposal-001")
        self.assertFalse(os.path.exists(os.path.join(pending_dir, "proposal-001.json")))

    def test_post_market_auto_merge_retry_does_not_degrade_workflow(self):
        import coordinator
        import json

        tmpdir = tempfile.mkdtemp()
        pending_dir = os.path.join(tmpdir, "strategies", "proposals", "pending_retry")
        os.makedirs(pending_dir)
        proposal = {
            "proposal_id": "proposal-001",
            "proposed_at": "2026-05-14T00:00:00Z",
            "proposer": "strategy_maintainer",
            "account": "main",
            "change_type": "strategy",
            "current_version": "1.0.5",
            "proposed_version": "1.0.5.pending",
            "diff": [],
            "triggering_events": [{"reason": "test"}],
            "rationale": "test",
        }
        with open(os.path.join(pending_dir, "proposal-001.json"), "w") as f:
            json.dump({"proposal_id": "proposal-001", "status": "pending_retry", "retry_count": 1, "proposal": proposal}, f)

        c = coordinator.MultiAgentCoordinator.__new__(coordinator.MultiAgentCoordinator)
        c.data_dir = tmpdir
        c.run_settlement = MagicMock(return_value={
            "accounts_updated": True,
            "main_value": 1000000,
            "lab_value": 300000,
            "performance_updated": True,
            "ledger_validation_passed": True,
        })
        c._build_oos_backtest_evidence = MagicMock(return_value={"status": "OK"})
        c._load_recent_trades = MagicMock(return_value=[])
        c._audit_strategy_adjustments = MagicMock(return_value={
            "audit_decision": "NO_CHANGES",
            "applied_adjustments": [],
        })
        strategy = FakeStrategyMaintainer()
        strategy.llm = MagicMock()
        c.agents = {
            "review_agent": FakeReviewAgent(),
            "risk_controller": FakeRiskControllerNoActions(),
            "strategy_maintainer": strategy,
        }

        with patch.object(coordinator.audit_layer, "review", return_value={
            "decision": "AUTO_MERGE",
            "reason": "retry passed",
            "proposal_id": "proposal-001",
        }):
            result = c.run_post_market_workflow()

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["pending_retry_audit"]["auto_merged"], 1)
        self.assertEqual(result["warnings"], [])

    def test_retry_pending_human_review_preserves_proposal_for_handoff(self):
        import coordinator
        import json

        tmpdir = tempfile.mkdtemp()
        pending_dir = os.path.join(tmpdir, "strategies", "proposals", "pending_retry")
        os.makedirs(pending_dir)
        proposal = {
            "proposal_id": "proposal-001",
            "proposed_at": "2026-05-14T00:00:00Z",
            "proposer": "strategy_maintainer",
            "account": "main",
            "change_type": "strategy",
            "current_version": "1.0.5",
            "proposed_version": "1.0.5.pending",
            "diff": [],
            "triggering_events": [{"reason": "test"}],
            "rationale": "test",
        }
        path = os.path.join(pending_dir, "proposal-001.json")
        with open(path, "w") as f:
            json.dump({"proposal_id": "proposal-001", "status": "pending_retry", "retry_count": 1, "proposal": proposal}, f)

        c = coordinator.MultiAgentCoordinator.__new__(coordinator.MultiAgentCoordinator)
        c.data_dir = tmpdir
        c._build_oos_backtest_evidence = MagicMock(return_value={"status": "OK"})
        c._load_recent_trades = MagicMock(return_value=[])
        maintainer = FakeStrategyMaintainer()
        maintainer.llm = MagicMock()

        with patch.object(coordinator.audit_layer, "review", return_value={
            "decision": "HUMAN_REVIEW",
            "reason": "needs human",
            "proposal_id": "proposal-001",
        }):
            result = c._retry_pending_audit_proposals(maintainer, {"accounts": {}})

        self.assertEqual(result["human_review"], 1)
        with open(path) as f:
            record = json.load(f)
        self.assertEqual(record["status"], "human_review")
        self.assertEqual(record["last_audit_result"]["decision"], "HUMAN_REVIEW")

    def test_retry_pending_existing_human_review_is_not_reaudited(self):
        import coordinator
        import json

        tmpdir = tempfile.mkdtemp()
        pending_dir = os.path.join(tmpdir, "strategies", "proposals", "pending_retry")
        os.makedirs(pending_dir)
        proposal = {
            "proposal_id": "proposal-001",
            "proposed_at": "2026-05-14T00:00:00Z",
            "proposer": "strategy_maintainer",
            "account": "main",
            "change_type": "strategy",
            "current_version": "1.0.5",
            "proposed_version": "1.0.5.pending",
            "diff": [],
            "triggering_events": [{"reason": "test"}],
            "rationale": "test",
        }
        with open(os.path.join(pending_dir, "proposal-001.json"), "w") as f:
            json.dump({
                "proposal_id": "proposal-001",
                "status": "human_review",
                "retry_count": 1,
                "proposal": proposal,
                "last_audit_result": {"decision": "HUMAN_REVIEW"},
            }, f)

        c = coordinator.MultiAgentCoordinator.__new__(coordinator.MultiAgentCoordinator)
        c.data_dir = tmpdir
        maintainer = FakeStrategyMaintainer()
        maintainer.llm = MagicMock()

        with patch.object(coordinator.audit_layer, "review") as review:
            result = c._retry_pending_audit_proposals(maintainer, {"accounts": {}})

        review.assert_not_called()
        self.assertEqual(result["human_review"], 1)

    def test_post_market_surfaces_existing_human_review_pending_retry(self):
        import coordinator
        import json

        tmpdir = tempfile.mkdtemp()
        pending_dir = os.path.join(tmpdir, "strategies", "proposals", "pending_retry")
        os.makedirs(pending_dir)
        proposal = {
            "proposal_id": "proposal-001",
            "proposed_at": "2026-05-14T00:00:00Z",
            "proposer": "strategy_maintainer",
            "account": "main",
            "change_type": "strategy",
            "current_version": "1.0.5",
            "proposed_version": "1.0.5.pending",
            "diff": [],
            "triggering_events": [{"reason": "test"}],
            "rationale": "test",
        }
        with open(os.path.join(pending_dir, "proposal-001.json"), "w") as f:
            json.dump({
                "proposal_id": "proposal-001",
                "status": "human_review",
                "retry_count": 1,
                "proposal": proposal,
                "last_audit_result": {"decision": "HUMAN_REVIEW"},
            }, f)

        c = coordinator.MultiAgentCoordinator.__new__(coordinator.MultiAgentCoordinator)
        c.data_dir = tmpdir
        c.run_settlement = MagicMock(return_value={
            "accounts_updated": True,
            "main_value": 1000000,
            "lab_value": 300000,
            "performance_updated": True,
            "ledger_validation_passed": True,
        })
        c._audit_strategy_adjustments = MagicMock(return_value={
            "audit_decision": "NO_CHANGES",
            "applied_adjustments": [],
        })
        strategy = FakeStrategyMaintainer()
        strategy.llm = MagicMock()
        c.agents = {
            "review_agent": FakeReviewAgent(),
            "risk_controller": FakeRiskControllerNoActions(),
            "strategy_maintainer": strategy,
        }

        with patch.object(coordinator.audit_layer, "review") as review:
            result = c.run_post_market_workflow()

        review.assert_not_called()
        self.assertEqual(result["status"], "degraded")
        self.assertEqual(result["pending_retry_audit"]["human_review"], 1)
        self.assertTrue(any("审计重试需要人工确认" in w for w in result["warnings"]))

    def test_retry_pending_exhaustion_blocks_and_clears_proposal(self):
        import coordinator
        import json

        tmpdir = tempfile.mkdtemp()
        pending_dir = os.path.join(tmpdir, "strategies", "proposals", "pending_retry")
        os.makedirs(pending_dir)
        proposal = {
            "proposal_id": "proposal-001",
            "proposed_at": "2026-05-14T00:00:00Z",
            "proposer": "strategy_maintainer",
            "account": "main",
            "change_type": "strategy",
            "current_version": "1.0.5",
            "proposed_version": "1.0.5.pending",
            "diff": [],
            "triggering_events": [{"reason": "test"}],
            "rationale": "test",
        }
        path = os.path.join(pending_dir, "proposal-001.json")
        with open(path, "w") as f:
            json.dump({"proposal_id": "proposal-001", "status": "pending_retry", "retry_count": 5, "proposal": proposal}, f)

        c = coordinator.MultiAgentCoordinator.__new__(coordinator.MultiAgentCoordinator)
        c.data_dir = tmpdir
        maintainer = FakeStrategyMaintainer()
        maintainer.llm = MagicMock()

        with patch.object(coordinator.audit_layer, "review") as review:
            result = c._retry_pending_audit_proposals(maintainer, {"accounts": {}})

        review.assert_not_called()
        self.assertEqual(result["retry_exhausted"], 1)
        self.assertFalse(os.path.exists(path))
        with open(os.path.join(tmpdir, "strategies", "audit_log.json")) as f:
            log = json.load(f)
        self.assertEqual(log[-1]["decision"], "BLOCKED")
        self.assertIn("retry exhausted", log[-1]["reason"])

    def test_post_market_surfaces_retry_exhaustion_warning(self):
        import coordinator
        import json

        tmpdir = tempfile.mkdtemp()
        pending_dir = os.path.join(tmpdir, "strategies", "proposals", "pending_retry")
        os.makedirs(pending_dir)
        proposal = {
            "proposal_id": "proposal-001",
            "proposed_at": "2026-05-14T00:00:00Z",
            "proposer": "strategy_maintainer",
            "account": "main",
            "change_type": "strategy",
            "current_version": "1.0.5",
            "proposed_version": "1.0.5.pending",
            "diff": [],
            "triggering_events": [{"reason": "test"}],
            "rationale": "test",
        }
        with open(os.path.join(pending_dir, "proposal-001.json"), "w") as f:
            json.dump({"proposal_id": "proposal-001", "status": "pending_retry", "retry_count": 5, "proposal": proposal}, f)

        c = coordinator.MultiAgentCoordinator.__new__(coordinator.MultiAgentCoordinator)
        c.data_dir = tmpdir
        c.run_settlement = MagicMock(return_value={
            "accounts_updated": True,
            "main_value": 1000000,
            "lab_value": 300000,
            "performance_updated": True,
            "ledger_validation_passed": True,
        })
        c._audit_strategy_adjustments = MagicMock(return_value={
            "audit_decision": "NO_CHANGES",
            "applied_adjustments": [],
        })
        strategy = FakeStrategyMaintainer()
        strategy.llm = MagicMock()
        c.agents = {
            "review_agent": FakeReviewAgent(),
            "risk_controller": FakeRiskControllerNoActions(),
            "strategy_maintainer": strategy,
        }

        with patch.object(coordinator.audit_layer, "review") as review:
            result = c.run_post_market_workflow()

        review.assert_not_called()
        self.assertEqual(result["status"], "degraded")
        self.assertEqual(result["pending_retry_audit"]["retry_exhausted"], 1)
        self.assertTrue(any("审计重试已耗尽" in w for w in result["warnings"]))

    def test_post_market_degrades_when_settlement_ledger_validation_fails(self):
        import coordinator

        c = coordinator.MultiAgentCoordinator.__new__(coordinator.MultiAgentCoordinator)
        c.data_dir = tempfile.mkdtemp()
        c.run_settlement = MagicMock(return_value={
            "accounts_updated": True,
            "main_value": 1000000,
            "lab_value": 300000,
            "performance_updated": True,
            "ledger_validation_passed": False,
            "degraded_reason": "ledger validation failed: INV-4",
        })
        strategy = FakeStrategyMaintainer()
        c.agents = {
            "review_agent": FakeReviewAgent(),
            "strategy_maintainer": strategy,
        }
        c._audit_strategy_adjustments = MagicMock(return_value={
            "audit_decision": "NO_CHANGES",
            "applied_adjustments": [],
        })

        result = c.run_post_market_workflow()

        self.assertEqual(result["status"], "degraded")
        self.assertIn("ledger validation failed: INV-4", result["warnings"][0])
        self.assertEqual(result["steps"][0]["status"], "degraded")

    def test_post_market_auto_merge_commits_only_after_audit(self):
        import coordinator
        import tempfile, os, json

        fake_audit_layer = MagicMock()
        fake_audit_layer.review.return_value = {
            "decision": "AUTO_MERGE",
            "reason": "3/3 unanimous approve",
            "proposal_id": "proposal-001",
        }

        # Use temp dir to avoid writing to real repo
        tmpdir = tempfile.mkdtemp()
        os.makedirs(os.path.join(tmpdir, "strategies"), exist_ok=True)
        os.makedirs(os.path.join(tmpdir, "references"), exist_ok=True)
        # Copy needed files from ROOT
        for src_name in ("strategies/active.json", "strategies/changelog.json"):
            src = os.path.join(ROOT, src_name)
            dst = os.path.join(tmpdir, src_name)
            if os.path.exists(src):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                import shutil
                shutil.copy2(src, dst)
        with open(os.path.join(tmpdir, "references", "risk-rules.md"), 'w') as f:
            f.write("# Risk Rules\n")

        c = coordinator.MultiAgentCoordinator.__new__(coordinator.MultiAgentCoordinator)
        c.data_dir = tmpdir
        # Mock run_settlement to avoid API calls and file writes
        c.run_settlement = MagicMock(return_value={
            "accounts_updated": True, "main_value": 1000000,
            "lab_value": 300000, "performance_updated": True,
        })
        strategy = FakeStrategyMaintainer()
        from backtest.market_data import StaticPriceProvider

        dates = pd.to_datetime([f"2026-04-{day:02d}" for day in range(1, 31)])
        c.market_data_provider = StaticPriceProvider(pd.DataFrame({
            "600519.SS": list(range(100, 130)),
            "000858.SZ": [100 + day * 0.5 for day in range(30)],
            "000300.SS": [4000 + day * 5 for day in range(30)],
        }, index=dates))
        c.trading_calendar = [day.strftime("%Y-%m-%d") for day in dates]
        c.watchlist = {
            "stocks": [
                {"code": "600519", "name": "贵州茅台", "tag": "main"},
                {"code": "000858", "name": "五粮液", "tag": "main"},
            ]
        }
        c.agents = {
            "review_agent": FakeReviewAgent(),
            "strategy_maintainer": strategy,
        }

        with patch.object(coordinator, "audit_layer", fake_audit_layer):
            result = c.run_post_market_workflow()

        self.assertEqual(result["status"], "success")
        strategy.apply_adjustments.assert_not_called()
        fake_audit_layer.review.assert_called_once()
        review_kwargs = fake_audit_layer.review.call_args.kwargs
        self.assertEqual(review_kwargs["proposal"]["proposal_id"], "proposal-001")
        self.assertIs(review_kwargs["llm_client"], strategy.llm)
        oos = review_kwargs["oos_backtest"]
        self.assertEqual(oos["status"], "OK")
        self.assertIn("current", oos)
        self.assertIn("proposed", oos)
        self.assertIn("window", oos)
        self.assertNotEqual(oos, {})
        strategy.commit_approved.assert_called_once_with("proposal-001")
        self.assertEqual(strategy.apply_result["audit_decision"], "AUTO_MERGE")


if __name__ == "__main__":
    unittest.main()
