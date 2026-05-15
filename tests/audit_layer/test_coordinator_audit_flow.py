import os
import sys
import unittest
from unittest.mock import MagicMock

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


class TestCoordinatorAuditFlow(unittest.TestCase):
    def test_post_market_auto_merge_commits_only_after_audit(self):
        import coordinator
        import tempfile, os, json

        fake_audit_layer = MagicMock()
        fake_audit_layer.review.return_value = {
            "decision": "AUTO_MERGE",
            "reason": "3/3 unanimous approve",
            "proposal_id": "proposal-001",
        }
        coordinator.audit_layer = fake_audit_layer

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
