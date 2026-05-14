"""Backfill validation: feed 6 negative + 3 positive samples through review().

Pass criteria (spec §10):
- 6 negatives: at least 5/6 must be REJECT or CONCERNS via overfitting
- 3 positives: at least 2/3 must be APPROVE or HUMAN_REVIEW

Severity reporting: failing positive sample 'multi_event_strategy' is high
severity (means audit layer never lets evidence-backed strategy iterations
through). Failing 'risk_tightening' or 'execution_bugfix' is medium severity
(reviewer mis-routing).
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock
sys.path.insert(0, os.path.expanduser("~/.hermes/virtual-trader"))


# 6 negative samples (single-event-triggered strategy changes from history)
NEGATIVE_SAMPLES = [
    {
        "proposal_id": "neg-main-104",
        "proposed_at": "2026-04-22T00:00:00Z",
        "proposer": "history",
        "account": "main",
        "change_type": "strategy",
        "current_version": "1.0.3",
        "proposed_version": "1.0.4",
        "diff": [{"path": "main_strategy.commodity_dimension", "old": "absent", "new": "enabled"}],
        "triggering_events": [{"date": "2026-04-21", "ticker": "601088", "event": "神华煤价盲点"}],
        "rationale": "神华教训，加商品价格维度",
    },
    {
        "proposal_id": "neg-main-105",
        "proposed_at": "2026-04-25T00:00:00Z",
        "proposer": "history",
        "account": "main",
        "change_type": "strategy",
        "current_version": "1.0.4",
        "proposed_version": "1.0.5",
        "diff": [{"path": "main_strategy.position_floor", "old": 0.0, "new": 0.5}],
        "triggering_events": [{"date": "2026-04-24", "event": "回测发现 cash drag -3.12%"}],
        "rationale": "建仓节奏 + 仓位下限",
    },
    {
        "proposal_id": "neg-lab-101",
        "proposed_at": "2026-04-16T00:00:00Z",
        "proposer": "history",
        "account": "lab",
        "change_type": "strategy",
        "current_version": "1.0.0",
        "proposed_version": "1.0.1",
        "diff": [{"path": "lab_strategy.parameters.volume_ratio_threshold", "old": 1.5, "new": 1.3}],
        "triggering_events": [{"date": "2026-04-15", "event": "信号不够"}],
        "rationale": "量比 1.5→1.3",
    },
    {
        "proposal_id": "neg-lab-102",
        "proposed_at": "2026-04-17T00:00:00Z",
        "proposer": "history",
        "account": "lab",
        "change_type": "strategy",
        "current_version": "1.0.1",
        "proposed_version": "1.0.2",
        "diff": [{"path": "lab_strategy.parameters.trailing_stop_pct", "old": 0.05, "new": 0.06}],
        "triggering_events": [{"date": "2026-04-16", "event": "过早出场"}],
        "rationale": "trailing 5%→6%",
    },
    {
        "proposal_id": "neg-lab-105",
        "proposed_at": "2026-04-19T00:00:00Z",
        "proposer": "history",
        "account": "lab",
        "change_type": "strategy",
        "current_version": "1.0.4",
        "proposed_version": "1.0.5",
        "diff": [{"path": "lab_strategy.rules.sector_confirmation", "old": "absent", "new": "enabled"}],
        "triggering_events": [{"date": "2026-04-18", "ticker": "300274", "event": "阳光电源假突破"}],
        "rationale": "板块确认 + 换手率",
    },
    {
        "proposal_id": "neg-lab-106",
        "proposed_at": "2026-04-30T00:00:00Z",
        "proposer": "history",
        "account": "lab",
        "change_type": "strategy",
        "current_version": "1.0.5",
        "proposed_version": "1.0.6",
        "diff": [{"path": "lab_strategy.rules.partial_profit", "old": "absent", "new": "enabled"}],
        "triggering_events": [{"date": "2026-04-29", "ticker": "688256", "event": "寒武纪 +20% 错过"}],
        "rationale": "高弹性分批止盈",
    },
]

POSITIVE_FIXTURES_DIR = os.path.join(
    os.path.expanduser("~/.hermes/virtual-trader"),
    "tests/audit_layer/fixtures/positive_samples",
)


def _intelligent_mock(proposal):
    """Returns a mock LLM that simulates how each Auditor SHOULD vote.

    Overfitting: REJECT iff change_type=strategy AND triggering_events count <= 2
                 APPROVE iff change_type=strategy AND triggering_events count > 2
                 (non-strategy types short-circuited by run_overfitting_auditor before LLM)
    Risk:        APPROVE always (no proposals in samples weaken risk rules)
    Cost:        APPROVE always (no proposals assume T+0 or violate liquidity)
    """
    mock = MagicMock()

    def respond(*args, **kwargs):
        prompt = kwargs.get("user") or (args[-1] if args else "")
        # detect which auditor by inspecting prompt content
        if "Overfitting Auditor" in prompt:
            n_events = len(proposal.get("triggering_events", []))
            if proposal.get("change_type") == "strategy" and n_events <= 2:
                return json.dumps({
                    "verdict": "REJECT", "in_scope": True,
                    "devil_advocate_points": ["only " + str(n_events) + " event(s)", "no OOS", "hindsight"],
                    "hard_reject_hits": ["triggering_events <= 2"],
                    "specific_evidence": [],
                })
            else:
                return json.dumps({
                    "verdict": "APPROVE", "in_scope": True,
                    "devil_advocate_points": ["a", "b", "c"],
                    "hard_reject_hits": [], "specific_evidence": [],
                })
        elif "Risk Auditor" in prompt:
            return json.dumps({
                "verdict": "APPROVE",
                "devil_advocate_points": ["a", "b", "c"],
                "hard_reject_hits": [], "specific_evidence": [],
            })
        elif "Cost & Execution Auditor" in prompt:
            return json.dumps({
                "verdict": "APPROVE",
                "devil_advocate_points": ["a", "b", "c"],
                "hard_reject_hits": [], "specific_evidence": [],
            })
        else:
            return json.dumps({"verdict": "INFRA_ERROR"})

    mock.call.side_effect = respond
    return mock


class TestAuditBackfill(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.log_path = os.path.join(self.tmpdir, "audit_log.json")
        Path(self.log_path).write_text("[]")

    def _review(self, proposal):
        from agents.audit_layer import review
        mock = _intelligent_mock(proposal)
        return review(
            proposal=proposal,
            changelog=[],
            oos_backtest={"current": {"sharpe": 0.5, "max_dd": 0.01},
                          "proposed": {"sharpe": 0.5, "max_dd": 0.01}},
            risk_rules="",
            current_portfolio={},
            recent_trades=[],
            current_account={},
            llm_client=mock,
            audit_log_path=self.log_path,
        )

    def test_negatives_at_least_5_of_6_rejected(self):
        rejected = 0
        details = []
        for sample in NEGATIVE_SAMPLES:
            r = self._review(sample)
            decision = r["decision"]
            details.append(f"{sample['proposal_id']}: {decision}")
            if decision in ("AUTO_REJECT", "HUMAN_REVIEW"):
                rejected += 1
        self.assertGreaterEqual(
            rejected, 5,
            f"Only {rejected}/6 negatives were caught:\n" + "\n".join(details)
        )

    def test_positives_at_least_2_of_3_approved(self):
        positive_files = [
            "risk_tightening.json",
            "execution_bugfix.json",
            "multi_event_strategy.json",
        ]
        approved = 0
        failed_severity = {"high": [], "medium": []}
        for fname in positive_files:
            sample = json.loads(Path(POSITIVE_FIXTURES_DIR, fname).read_text())
            r = self._review(sample)
            decision = r["decision"]
            if decision in ("AUTO_MERGE", "HUMAN_REVIEW"):
                approved += 1
            else:
                if fname == "multi_event_strategy.json":
                    failed_severity["high"].append(fname)
                else:
                    failed_severity["medium"].append(fname)
        self.assertGreaterEqual(
            approved, 2,
            f"Only {approved}/3 positives approved.\n"
            f"High severity failures: {failed_severity['high']}\n"
            f"Medium severity failures: {failed_severity['medium']}\n"
            "See spec §10.3 — multi_event_strategy failure means audit layer "
            "blocks all evidence-backed strategy iteration."
        )


if __name__ == "__main__":
    unittest.main()
