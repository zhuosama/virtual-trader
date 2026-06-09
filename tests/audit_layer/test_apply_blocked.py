"""P1: apply_adjustments() is a public bypass — must raise PermissionError."""
import json
import os
import tempfile
import unittest

VTRADER_ROOT = os.path.expanduser("~/.hermes/virtual-trader")
import sys
sys.path.insert(0, os.path.join(VTRADER_ROOT, "agents"))


class TestApplyAdjustmentsBlocked(unittest.TestCase):
    """apply_adjustments() must refuse to write, forcing all changes through audit."""

    def _make_agent(self):
        """Create a real StrategyMaintainerAgent with temp data."""
        from strategy_maintainer import StrategyMaintainerAgent
        tmpdir = tempfile.mkdtemp()
        strategies_dir = os.path.join(tmpdir, "strategies")
        os.makedirs(strategies_dir)
        with open(os.path.join(strategies_dir, "active.json"), 'w') as f:
            json.dump({"main_strategy": {"parameters": {"take_profit_pct": 15}},
                        "lab_strategy": {"parameters": {}}}, f)
        with open(os.path.join(strategies_dir, "changelog.json"), 'w') as f:
            json.dump([], f)
        with open(os.path.join(strategies_dir, "audit_log.json"), 'w') as f:
            json.dump([], f)

        agent = StrategyMaintainerAgent.__new__(StrategyMaintainerAgent)
        agent.data_dir = tmpdir
        agent.config = {}
        agent.llm = None
        agent.strategies = json.load(open(os.path.join(strategies_dir, "active.json")))
        agent.changelog = []
        return agent, tmpdir

    def test_apply_adjustments_raises_permission_error(self):
        """Calling apply_adjustments() must raise PermissionError."""
        agent, _ = self._make_agent()
        adjustments = [{"type": "parameter_adjustment", "parameter": "take_profit_pct",
                        "new_value": 12, "strategy": "main", "reason": "test"}]
        with self.assertRaises(PermissionError) as ctx:
            agent.apply_adjustments(adjustments)
        self.assertIn("blocked", str(ctx.exception).lower())
        self.assertIn("propose", str(ctx.exception).lower())

    def test_apply_adjustments_does_not_write_active_json(self):
        """apply_adjustments() must not modify active.json."""
        agent, tmpdir = self._make_agent()
        active_path = os.path.join(tmpdir, "strategies", "active.json")
        before = open(active_path).read()
        adjustments = [{"type": "parameter_adjustment", "parameter": "take_profit_pct",
                        "new_value": 12, "strategy": "main", "reason": "test"}]
        try:
            agent.apply_adjustments(adjustments)
        except PermissionError:
            pass
        after = open(active_path).read()
        self.assertEqual(before, after, "active.json was modified by apply_adjustments()!")

    def test_apply_adjustments_does_not_write_changelog(self):
        """apply_adjustments() must not modify changelog.json."""
        agent, tmpdir = self._make_agent()
        changelog_path = os.path.join(tmpdir, "strategies", "changelog.json")
        before = open(changelog_path).read()
        adjustments = [{"type": "parameter_adjustment", "parameter": "take_profit_pct",
                        "new_value": 12, "strategy": "main", "reason": "test"}]
        try:
            agent.apply_adjustments(adjustments)
        except PermissionError:
            pass
        after = open(changelog_path).read()
        self.assertEqual(before, after, "changelog.json was modified by apply_adjustments()!")


if __name__ == "__main__":
    unittest.main()
