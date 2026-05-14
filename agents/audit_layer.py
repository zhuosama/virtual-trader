"""Audit Layer for virtual-trader strategy proposals.

See spec at ~/.hermes/specs/2026-05-14-virtual-trader-audit-layer-design.md
"""
from __future__ import annotations

from typing import Any

VALID_CHANGE_TYPES = {"strategy", "execution", "risk", "data", "system"}

REQUIRED_PROPOSAL_FIELDS = (
    "proposal_id",
    "proposed_at",
    "proposer",
    "account",
    "change_type",
    "current_version",
    "proposed_version",
    "diff",
    "triggering_events",
    "rationale",
)


def validate_proposal(proposal: dict[str, Any]) -> None:
    """Validate proposal.json structure. Raise ValueError on invalid.

    See spec §4 for field semantics.
    """
    for f in REQUIRED_PROPOSAL_FIELDS:
        if f not in proposal:
            raise ValueError(f"proposal missing required field: {f!r}")
    ct = proposal["change_type"]
    if ct not in VALID_CHANGE_TYPES:
        raise ValueError(
            f"proposal change_type={ct!r} not in {sorted(VALID_CHANGE_TYPES)}"
        )
    if not isinstance(proposal["diff"], list):
        raise ValueError("proposal.diff must be a list")
    if not isinstance(proposal["triggering_events"], list):
        raise ValueError("proposal.triggering_events must be a list")


def aggregate_verdicts(verdicts: list[dict]) -> dict:
    """Aggregate 3 reviewer verdicts into a quorum decision.

    See spec §6.2. INFRA_ERROR is a separate state that does NOT count
    toward substantive reject (it represents the reviewer being unable to
    judge, not finding a problem). Any INFRA_ERROR triggers PENDING_RETRY
    so review_agent doesn't get fed infra noise as strategy signal.
    """
    n_approve = sum(1 for v in verdicts if v["verdict"] == "APPROVE")
    n_substantive_reject = sum(
        1 for v in verdicts if v["verdict"] in ("REJECT", "CONCERNS")
    )
    n_infra = sum(1 for v in verdicts if v["verdict"] == "INFRA_ERROR")

    if n_infra > 0:
        return {
            "decision": "PENDING_RETRY",
            "reason": f"{n_infra}/3 reviewer 出现 infra 错误，下个 cron tick 重试",
            "feed_back_to_review_agent": False,
        }

    if n_approve == 3:
        return {
            "decision": "AUTO_MERGE",
            "reason": "3/3 unanimous approve",
            "feed_back_to_review_agent": False,
        }
    if n_approve == 2:
        return {
            "decision": "HUMAN_REVIEW",
            "reason": "2/3 approve, 1 substantive reject/concerns",
            "feed_back_to_review_agent": False,
        }
    return {
        "decision": "AUTO_REJECT",
        "reason": f"{n_substantive_reject}/3 substantive reject",
        "feed_back_to_review_agent": True,
    }
