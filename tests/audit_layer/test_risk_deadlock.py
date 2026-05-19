"""P2: Risk deadlock — overconcentrated positions generate sell actions, never buy."""
import json
import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

VTRADER_ROOT = os.path.expanduser("~/.hermes/virtual-trader")
import sys
sys.path.insert(0, os.path.join(VTRADER_ROOT, "agents"))


class TestRiskReduction(unittest.TestCase):
    """Test generate_risk_reduction_actions() in risk_controller."""

    def _make_accounts(self, main_positions=None, lab_positions=None):
        """Create temp accounts directory with given positions."""
        tmpdir = tempfile.mkdtemp()
        accounts_dir = os.path.join(tmpdir, "accounts")
        os.makedirs(accounts_dir)
        strategies_dir = os.path.join(tmpdir, "strategies")
        os.makedirs(strategies_dir)
        refs_dir = os.path.join(tmpdir, "references")
        os.makedirs(refs_dir)

        main = {
            "id": "main", "initial_capital": 1000000,
            "cash": 500000,
            "positions": main_positions or [],
            "total_value": 1000000,
            "position_pct": 50.0,
        }
        lab = {
            "id": "lab", "initial_capital": 300000,
            "cash": 150000,
            "positions": lab_positions or [],
            "total_value": 300000,
            "position_pct": 50.0,
        }
        with open(os.path.join(accounts_dir, "main.json"), 'w') as f:
            json.dump(main, f)
        with open(os.path.join(accounts_dir, "lab.json"), 'w') as f:
            json.dump(lab, f)

        # Minimal strategy file
        with open(os.path.join(strategies_dir, "active.json"), 'w') as f:
            json.dump({"main_strategy": {}, "lab_strategy": {}}, f)
        with open(os.path.join(strategies_dir, "changelog.json"), 'w') as f:
            json.dump([], f)
        with open(os.path.join(refs_dir, "risk-rules.md"), 'w') as f:
            f.write("# Risk Rules\n")

        return tmpdir

    def test_overconcentrated_generates_sell(self):
        """Position at 15% > 10% limit → generate sell action."""
        from risk_controller import RiskControllerAgent

        positions = [
            {"code": "000651", "name": "格力电器", "shares": 3200,
             "market_value": 150000, "avg_cost": 37.46, "current_price": 46.88,
             "unrealized_pnl_pct": 25.0, "unrealized_pnl": 30000},
        ]
        tmpdir = self._make_accounts(main_positions=positions)
        agent = RiskControllerAgent.__new__(RiskControllerAgent)
        agent.data_dir = tmpdir
        agent.config = {}
        agent.risk_rules = agent._load_risk_rules()

        actions = agent.generate_risk_reduction_actions()
        sell_actions = [a for a in actions if a['type'] == 'risk_reduction']
        self.assertEqual(len(sell_actions), 1)
        self.assertEqual(sell_actions[0]['code'], '000651')
        self.assertEqual(sell_actions[0]['action'], 'sell')
        self.assertIn('15.0%', sell_actions[0]['reason'])

    def test_overconcentrated_sell_action_has_deterministic_board_lot_size(self):
        """Risk reductions should be executable without human sizing judgment."""
        from risk_controller import RiskControllerAgent

        positions = [
            {"code": "000651", "name": "格力电器", "shares": 3200,
             "market_value": 128896, "avg_cost": 37.46, "current_price": 40.28,
             "unrealized_pnl_pct": 7.54, "unrealized_pnl": 9039},
        ]
        tmpdir = self._make_accounts(main_positions=positions)
        agent = RiskControllerAgent.__new__(RiskControllerAgent)
        agent.data_dir = tmpdir
        agent.config = {}
        agent.risk_rules = agent._load_risk_rules()

        action = [
            a for a in agent.generate_risk_reduction_actions()
            if a['type'] == 'risk_reduction' and a['code'] == '000651'
        ][0]

        self.assertTrue(action["auto_execute"])
        self.assertEqual(action["sell_shares"], 800)
        self.assertEqual(action["current_shares"], 3200)
        self.assertEqual(action["lot_size"], 100)
        self.assertLessEqual(action["projected_pct"], action["target_pct"])

    def test_no_buy_actions_ever(self):
        """generate_risk_reduction_actions must NEVER produce buy actions."""
        from risk_controller import RiskControllerAgent

        positions = [
            {"code": "000651", "name": "格力电器", "shares": 5000,
             "market_value": 200000, "avg_cost": 37.46, "current_price": 40.0,
             "unrealized_pnl_pct": 6.8, "unrealized_pnl": 12700},
        ]
        tmpdir = self._make_accounts(main_positions=positions)
        agent = RiskControllerAgent.__new__(RiskControllerAgent)
        agent.data_dir = tmpdir
        agent.config = {}
        agent.risk_rules = agent._load_risk_rules()

        actions = agent.generate_risk_reduction_actions()
        for a in actions:
            self.assertNotEqual(a['action'], 'buy',
                                f"Generated buy action: {a}")

    def test_within_limits_no_action(self):
        """All positions within limits → empty actions list."""
        from risk_controller import RiskControllerAgent

        positions = [
            {"code": "600900", "name": "长江电力", "shares": 1800,
             "market_value": 48000, "avg_cost": 26.43, "current_price": 27.0,
             "unrealized_pnl_pct": 2.2, "unrealized_pnl": 1026},
        ]
        tmpdir = self._make_accounts(main_positions=positions)
        agent = RiskControllerAgent.__new__(RiskControllerAgent)
        agent.data_dir = tmpdir
        agent.config = {}
        agent.risk_rules = agent._load_risk_rules()

        actions = agent.generate_risk_reduction_actions()
        self.assertEqual(actions, [])

    def test_concentration_limit_uses_account_value_not_position_book(self):
        """Top-3 concentration should not flag normal low-exposure accounts."""
        from risk_controller import RiskControllerAgent

        main_positions = [
            {"code": "000651", "name": "格力电器", "market_value": 95304},
            {"code": "002415", "name": "海康威视", "market_value": 49500},
            {"code": "600900", "name": "长江电力", "market_value": 48402},
            {"code": "601006", "name": "大秦铁路", "market_value": 43440},
        ]
        lab_positions = [
            {"code": "300124", "name": "汇川技术", "market_value": 61888},
            {"code": "688111", "name": "金山办公", "market_value": 25319},
        ]
        tmpdir = self._make_accounts(main_positions=main_positions, lab_positions=lab_positions)
        agent = RiskControllerAgent.__new__(RiskControllerAgent)
        agent.data_dir = tmpdir
        agent.config = {}
        agent.risk_rules = agent._load_risk_rules()

        result = agent._check_concentration_limits({})

        self.assertTrue(result["passed"])
        self.assertEqual(result["warnings"], [])

    def test_stop_loss_generates_sell(self):
        """Position with -8% loss > 7% stop → generate stop_loss sell."""
        from risk_controller import RiskControllerAgent

        positions = [
            {"code": "600036", "name": "招商银行", "shares": 800,
             "market_value": 30000, "avg_cost": 39.19, "current_price": 37.46,
             "unrealized_pnl_pct": -4.4, "unrealized_pnl": -1384},
        ]
        # Override stop loss to be lower for test
        tmpdir = self._make_accounts(main_positions=positions)
        agent = RiskControllerAgent.__new__(RiskControllerAgent)
        agent.data_dir = tmpdir
        agent.config = {}
        agent.risk_rules = agent._load_risk_rules()
        # Artificially lower the stop loss for testing
        agent.risk_rules['stop_loss_rules']['main']['default'] = 0.04

        actions = agent.generate_risk_reduction_actions()
        stop_actions = [a for a in actions if a['type'] == 'stop_loss']
        self.assertEqual(len(stop_actions), 1)
        self.assertEqual(stop_actions[0]['code'], '600036')
        self.assertTrue(stop_actions[0]['auto_execute'])
        self.assertEqual(stop_actions[0]['sell_shares'], 800)
        self.assertEqual(stop_actions[0]['current_shares'], 800)

    def test_time_stop_generates_sell_for_old_flat_position(self):
        """Main position held longer than 20 days with no gain → generate time_stop sell."""
        from risk_controller import RiskControllerAgent

        class FrozenDatetime(datetime):
            @classmethod
            def now(cls):
                return cls(2026, 5, 16)

        positions = [
            {"code": "600900", "name": "长江电力", "shares": 1800,
             "market_value": 48000, "avg_cost": 26.43, "current_price": 26.43,
             "unrealized_pnl_pct": 0.0, "unrealized_pnl": 0,
             "buy_date": "2026-04-20"},
        ]
        tmpdir = self._make_accounts(main_positions=positions)
        agent = RiskControllerAgent.__new__(RiskControllerAgent)
        agent.data_dir = tmpdir
        agent.config = {}
        agent.risk_rules = agent._load_risk_rules()

        with patch("risk_controller.datetime", FrozenDatetime):
            actions = agent.generate_risk_reduction_actions()

        time_actions = [a for a in actions if a['type'] == 'time_stop']
        self.assertEqual(len(time_actions), 1)
        self.assertEqual(time_actions[0]['code'], '600900')
        self.assertEqual(time_actions[0]['holding_days'], 26)
        self.assertEqual(time_actions[0]['time_stop_days'], 20)
        self.assertTrue(time_actions[0]['auto_execute'])
        self.assertEqual(time_actions[0]['sell_shares'], 1800)
        self.assertEqual(time_actions[0]['current_shares'], 1800)
        self.assertIn('持有26天', time_actions[0]['reason'])

    def test_time_stop_does_not_sell_old_profitable_position(self):
        """Old positions with gains should not trigger time_stop."""
        from risk_controller import RiskControllerAgent

        class FrozenDatetime(datetime):
            @classmethod
            def now(cls):
                return cls(2026, 5, 16)

        positions = [
            {"code": "600900", "name": "长江电力", "shares": 1800,
             "market_value": 48000, "avg_cost": 26.43, "current_price": 27.00,
             "unrealized_pnl_pct": 2.2, "unrealized_pnl": 1026,
             "buy_date": "2026-04-20"},
        ]
        tmpdir = self._make_accounts(main_positions=positions)
        agent = RiskControllerAgent.__new__(RiskControllerAgent)
        agent.data_dir = tmpdir
        agent.config = {}
        agent.risk_rules = agent._load_risk_rules()

        with patch("risk_controller.datetime", FrozenDatetime):
            actions = agent.generate_risk_reduction_actions()

        self.assertEqual([a for a in actions if a['type'] == 'time_stop'], [])

    def test_position_with_stop_loss_and_time_stop_generates_one_sell_action(self):
        """One position breaching multiple rules should still generate one sell action."""
        from risk_controller import RiskControllerAgent

        class FrozenDatetime(datetime):
            @classmethod
            def now(cls):
                return cls(2026, 5, 16)

        positions = [
            {"code": "600036", "name": "招商银行", "shares": 800,
             "market_value": 30000, "avg_cost": 39.19, "current_price": 36.00,
             "unrealized_pnl_pct": -8.1, "unrealized_pnl": -2552,
             "buy_date": "2026-04-20"},
        ]
        tmpdir = self._make_accounts(main_positions=positions)
        agent = RiskControllerAgent.__new__(RiskControllerAgent)
        agent.data_dir = tmpdir
        agent.config = {}
        agent.risk_rules = agent._load_risk_rules()

        with patch("risk_controller.datetime", FrozenDatetime):
            actions = agent.generate_risk_reduction_actions()

        sell_actions = [
            a for a in actions
            if a['account'] == 'main' and a['code'] == '600036' and a['action'] == 'sell'
        ]
        self.assertEqual(len(sell_actions), 1)
        self.assertEqual(sell_actions[0]['type'], 'stop_loss')

    def test_malformed_time_stop_date_logs_warning_and_skips(self):
        """Malformed entry dates should be visible, but must not create false sell actions."""
        from risk_controller import RiskControllerAgent

        positions = [
            {"code": "600900", "name": "长江电力", "shares": 1800,
             "market_value": 48000, "avg_cost": 26.43, "current_price": 26.43,
             "unrealized_pnl_pct": 0.0, "unrealized_pnl": 0,
             "buy_date": "2026/04/20"},
        ]
        tmpdir = self._make_accounts(main_positions=positions)
        agent = RiskControllerAgent.__new__(RiskControllerAgent)
        agent.data_dir = tmpdir
        agent.config = {}
        agent.risk_rules = agent._load_risk_rules()

        with self.assertLogs("risk_controller", level="WARNING") as logs:
            actions = agent.generate_risk_reduction_actions()

        self.assertEqual([a for a in actions if a['type'] == 'time_stop'], [])
        self.assertIn("cannot parse entry_date", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
