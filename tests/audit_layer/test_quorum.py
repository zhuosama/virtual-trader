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


if __name__ == "__main__":
    unittest.main()
