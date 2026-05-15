"""P2: Risk deadlock — overconcentrated positions generate sell actions, never buy."""
import json
import os
import tempfile
import unittest

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


if __name__ == "__main__":
    unittest.main()
