import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, os.path.expanduser("~/.hermes/virtual-trader"))


class TestStrategyMaintainerSplit(unittest.TestCase):
    def setUp(self):
        # use a tmp data_dir to avoid touching live state
        self.tmpdir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmpdir, "strategies"))
        # minimal active.json + changelog + audit_log
        Path(self.tmpdir, "strategies", "active.json").write_text(json.dumps({
            "main_strategy": {"parameters": {"max_single_position": 0.10}}
        }))
        Path(self.tmpdir, "strategies", "changelog.json").write_text("[]")
        Path(self.tmpdir, "strategies", "audit_log.json").write_text("[]")

    def _make_maintainer(self):
        from agents.strategy_maintainer import StrategyMaintainerAgent
        m = StrategyMaintainerAgent()
        m.data_dir = self.tmpdir
        m.strategies = json.loads(
            Path(self.tmpdir, "strategies", "active.json").read_text()
        )
        m.changelog = []
        return m

    def test_propose_returns_proposal_dict_no_side_effect(self):
        m = self._make_maintainer()
        adjustments = [{
            "type": "parameter_adjustment",
            "strategy": "main",
            "parameter": "max_single_position",
            "old_value": 0.10,
            "new_value": 0.08,
            "reason": "test"
        }]
        proposal = m.propose(adjustments)
        # 必要字段都在
        self.assertIn("proposal_id", proposal)
        self.assertEqual(proposal["change_type"], "strategy")
        self.assertEqual(proposal["account"], "main")
        # active.json 没有被修改
        active = json.loads(Path(self.tmpdir, "strategies", "active.json").read_text())
        self.assertEqual(active["main_strategy"]["parameters"]["max_single_position"], 0.10)

    def test_commit_approved_without_audit_log_entry_raises(self):
        m = self._make_maintainer()
        with self.assertRaises(PermissionError) as ctx:
            m.commit_approved("non-existent-proposal-id")
        self.assertIn("audit_log", str(ctx.exception))

    def test_commit_approved_with_auto_reject_raises(self):
        m = self._make_maintainer()
        # 伪造 audit_log entry with AUTO_REJECT
        Path(self.tmpdir, "strategies", "audit_log.json").write_text(json.dumps([{
            "proposal_id": "rejected_id",
            "audited_at": "2026-05-14T10:00:00Z",
            "decision": "AUTO_REJECT",
        }]))
        with self.assertRaises(PermissionError) as ctx:
            m.commit_approved("rejected_id")
        self.assertIn("AUTO_REJECT", str(ctx.exception))

    def test_commit_approved_with_auto_merge_writes_files(self):
        m = self._make_maintainer()
        # 先 propose 出来获取 id（并保存 proposal）
        adjustments = [{
            "type": "parameter_adjustment",
            "strategy": "main",
            "parameter": "max_single_position",
            "old_value": 0.10,
            "new_value": 0.08,
            "reason": "test"
        }]
        proposal = m.propose(adjustments)
        pid = proposal["proposal_id"]
        # 模拟审核通过：写 audit_log
        Path(self.tmpdir, "strategies", "audit_log.json").write_text(json.dumps([{
            "proposal_id": pid,
            "audited_at": "2026-05-14T10:00:00Z",
            "decision": "AUTO_MERGE",
        }]))
        m.commit_approved(pid)
        active = json.loads(Path(self.tmpdir, "strategies", "active.json").read_text())
        self.assertEqual(active["main_strategy"]["parameters"]["max_single_position"], 0.08)


if __name__ == "__main__":
    unittest.main()
