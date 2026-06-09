import json
import os
import tempfile
import unittest
from pathlib import Path
import sys
sys.path.insert(0, os.path.expanduser("~/.hermes/virtual-trader"))


class TestAuditLog(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.log_path = Path(self.tmpdir) / "audit_log.json"
        self.log_path.write_text("[]")

    def test_append_to_empty(self):
        from agents.audit_layer import append_audit_log
        entry = {"proposal_id": "p1", "audited_at": "2026-05-14T10:00:00Z", "decision": "AUTO_MERGE"}
        append_audit_log(entry, log_path=str(self.log_path))
        data = json.loads(self.log_path.read_text())
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["proposal_id"], "p1")

    def test_append_preserves_prior(self):
        from agents.audit_layer import append_audit_log
        self.log_path.write_text(json.dumps([{"proposal_id": "old", "decision": "AUTO_REJECT"}]))
        append_audit_log({"proposal_id": "new", "audited_at": "2026-05-14T10:00:00Z", "decision": "AUTO_MERGE"}, log_path=str(self.log_path))
        data = json.loads(self.log_path.read_text())
        self.assertEqual(len(data), 2)
        self.assertEqual(data[0]["proposal_id"], "old")
        self.assertEqual(data[1]["proposal_id"], "new")

    def test_required_fields_enforced(self):
        from agents.audit_layer import append_audit_log
        with self.assertRaises(ValueError) as ctx:
            append_audit_log({"proposal_id": "p1"}, log_path=str(self.log_path))  # missing decision/audited_at
        msg = str(ctx.exception).lower()
        self.assertTrue("audited_at" in msg or "decision" in msg)

    def test_decision_must_be_valid(self):
        from agents.audit_layer import append_audit_log
        with self.assertRaises(ValueError):
            append_audit_log(
                {"proposal_id": "p1", "audited_at": "2026-05-14T10:00:00Z", "decision": "INVENTED"},
                log_path=str(self.log_path),
            )


if __name__ == "__main__":
    unittest.main()
