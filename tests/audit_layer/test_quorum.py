import unittest
import sys, os
sys.path.insert(0, os.path.expanduser("~/.hermes/virtual-trader"))


class TestProposalSchema(unittest.TestCase):
    def test_valid_proposal_passes(self):
        from agents.audit_layer import validate_proposal
        proposal = {
            "proposal_id": "2026-05-14T02:00:00-abc12345",
            "proposed_at": "2026-05-14T02:00:00+08:00",
            "proposer": "strategy_maintainer",
            "account": "main",
            "change_type": "strategy",
            "current_version": "1.0.5",
            "proposed_version": "1.0.6",
            "diff": [{"path": "main_strategy.parameters.take_profit_pct", "old": 15, "new": 12}],
            "triggering_events": [{"date": "2026-05-12", "ticker": "601088", "event": "..."}],
            "rationale": "...",
        }
        validate_proposal(proposal)

    def test_missing_change_type_rejected(self):
        from agents.audit_layer import validate_proposal
        proposal = {"proposal_id": "x", "proposed_at": "y", "proposer": "z",
                    "account": "main", "current_version": "1.0", "proposed_version": "1.1",
                    "diff": [], "triggering_events": [], "rationale": ""}
        with self.assertRaises(ValueError) as ctx:
            validate_proposal(proposal)
        self.assertIn("change_type", str(ctx.exception))

    def test_invalid_change_type_rejected(self):
        from agents.audit_layer import validate_proposal
        proposal = {"proposal_id": "x", "proposed_at": "y", "proposer": "z",
                    "account": "main", "change_type": "made_up_type",
                    "current_version": "1.0", "proposed_version": "1.1",
                    "diff": [], "triggering_events": [], "rationale": ""}
        with self.assertRaises(ValueError) as ctx:
            validate_proposal(proposal)
        self.assertIn("change_type", str(ctx.exception))


class TestQuorum(unittest.TestCase):
    def _verdict(self, kind):
        return {"verdict": kind, "reasoning": "test"}

    def test_3_of_3_approve_auto_merge(self):
        from agents.audit_layer import aggregate_verdicts
        result = aggregate_verdicts([self._verdict("APPROVE")] * 3)
        self.assertEqual(result["decision"], "AUTO_MERGE")
        self.assertFalse(result["feed_back_to_review_agent"])

    def test_2_approve_1_reject_human_review(self):
        from agents.audit_layer import aggregate_verdicts
        result = aggregate_verdicts(
            [self._verdict("APPROVE"), self._verdict("APPROVE"), self._verdict("REJECT")]
        )
        self.assertEqual(result["decision"], "HUMAN_REVIEW")
        self.assertFalse(result["feed_back_to_review_agent"])

    def test_2_reject_auto_reject_feeds_back(self):
        from agents.audit_layer import aggregate_verdicts
        result = aggregate_verdicts(
            [self._verdict("APPROVE"), self._verdict("REJECT"), self._verdict("REJECT")]
        )
        self.assertEqual(result["decision"], "AUTO_REJECT")
        self.assertTrue(result["feed_back_to_review_agent"])

    def test_concerns_counts_as_substantive_reject(self):
        from agents.audit_layer import aggregate_verdicts
        result = aggregate_verdicts(
            [self._verdict("APPROVE"), self._verdict("CONCERNS"), self._verdict("CONCERNS")]
        )
        self.assertEqual(result["decision"], "AUTO_REJECT")

    def test_any_infra_error_pending_retry_no_feedback(self):
        from agents.audit_layer import aggregate_verdicts
        result = aggregate_verdicts(
            [self._verdict("APPROVE"), self._verdict("APPROVE"), self._verdict("INFRA_ERROR")]
        )
        self.assertEqual(result["decision"], "PENDING_RETRY")
        self.assertFalse(result["feed_back_to_review_agent"])

    def test_all_infra_error_still_pending_not_blocked(self):
        # BLOCKED 在 retry 累计 3 次后才发生；单次审核 3/3 INFRA_ERROR 仍是 PENDING_RETRY
        from agents.audit_layer import aggregate_verdicts
        result = aggregate_verdicts([self._verdict("INFRA_ERROR")] * 3)
        self.assertEqual(result["decision"], "PENDING_RETRY")


if __name__ == "__main__":
    unittest.main()
