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
