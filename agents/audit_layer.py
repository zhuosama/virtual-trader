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


import json as _json
import os as _os

VALID_DECISIONS = {"AUTO_MERGE", "HUMAN_REVIEW", "AUTO_REJECT", "PENDING_RETRY", "BLOCKED"}

_DEFAULT_AUDIT_LOG_PATH = _os.path.expanduser(
    "~/.hermes/virtual-trader/strategies/audit_log.json"
)


def append_audit_log(entry: dict, log_path: str | None = None) -> None:
    """Append-only writer for strategies/audit_log.json.

    Validates required fields and decision enum. Reads-merges-writes
    in one shot (acceptable for low write frequency; not concurrency-safe).
    See spec §7.
    """
    if "proposal_id" not in entry:
        raise ValueError("audit_log entry missing required field: proposal_id")
    if "audited_at" not in entry:
        raise ValueError("audit_log entry missing required field: audited_at")
    if "decision" not in entry:
        raise ValueError("audit_log entry missing required field: decision")
    if entry["decision"] not in VALID_DECISIONS:
        raise ValueError(
            f"audit_log entry decision={entry['decision']!r} not in "
            f"{sorted(VALID_DECISIONS)}"
        )

    path = log_path or _DEFAULT_AUDIT_LOG_PATH
    if _os.path.exists(path):
        with open(path) as f:
            data = _json.load(f)
    else:
        data = []
    data.append(entry)
    # atomic write via temp file
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        _json.dump(data, f, ensure_ascii=False, indent=2)
    _os.replace(tmp, path)


def _load_prompt(name: str) -> str:
    """Read a reviewer prompt file from agents/audit_prompts/."""
    here = _os.path.dirname(_os.path.abspath(__file__))
    path = _os.path.join(here, "audit_prompts", name)
    with open(path) as f:
        return f.read()


def run_overfitting_auditor(
    proposal: dict,
    changelog: list,
    oos_backtest: dict,
    llm_client,
    model: str = "deepseek-v4-pro",
) -> dict:
    """Run the Overfitting Auditor on a proposal.

    For change_type != strategy: short-circuit APPROVE without LLM call.
    Otherwise: dispatch LLM with the prompt + structured inputs.
    """
    if proposal.get("change_type") != "strategy":
        return {
            "verdict": "APPROVE",
            "in_scope": False,
            "reasoning": f"not in scope: change_type={proposal.get('change_type')}",
        }

    from .audit_subagent import call_auditor
    prompt_template = _load_prompt("overfitting_auditor.md")
    full_prompt = (
        prompt_template
        + "\n\n---\n\n## 当前 proposal\n```json\n"
        + _json.dumps(proposal, ensure_ascii=False, indent=2)
        + "\n```\n\n## changelog\n```json\n"
        + _json.dumps(changelog, ensure_ascii=False, indent=2)
        + "\n```\n\n## oos_backtest\n```json\n"
        + _json.dumps(oos_backtest, ensure_ascii=False, indent=2)
        + "\n```\n"
    )
    return call_auditor("overfitting_auditor", full_prompt, llm_client=llm_client, model=model)


def run_risk_auditor(
    proposal: dict,
    risk_rules: str,
    current_portfolio: dict,
    llm_client,
    model: str = "deepseek-v4-pro",
) -> dict:
    """Run the Risk Auditor on a proposal. Applies to ALL change_types."""
    from .audit_subagent import call_auditor
    prompt_template = _load_prompt("risk_auditor.md")
    full_prompt = (
        prompt_template
        + "\n\n---\n\n## 当前 proposal\n```json\n"
        + _json.dumps(proposal, ensure_ascii=False, indent=2)
        + "\n```\n\n## risk-rules.md\n"
        + risk_rules
        + "\n\n## current_portfolio\n```json\n"
        + _json.dumps(current_portfolio, ensure_ascii=False, indent=2)
        + "\n```\n"
    )
    return call_auditor("risk_auditor", full_prompt, llm_client=llm_client, model=model)


def run_cost_execution_auditor(
    proposal: dict,
    recent_trades: list,
    current_account: dict,
    llm_client,
    model: str = "deepseek-v4-pro",
) -> dict:
    """Run the Cost & Execution Auditor on a proposal. Applies to ALL change_types."""
    from .audit_subagent import call_auditor
    prompt_template = _load_prompt("cost_execution_auditor.md")
    full_prompt = (
        prompt_template
        + "\n\n---\n\n## 当前 proposal\n```json\n"
        + _json.dumps(proposal, ensure_ascii=False, indent=2)
        + "\n```\n\n## recent_trades (最近30天)\n```json\n"
        + _json.dumps(recent_trades, ensure_ascii=False, indent=2)
        + "\n```\n\n## current_account\n```json\n"
        + _json.dumps(current_account, ensure_ascii=False, indent=2)
        + "\n```\n"
    )
    return call_auditor("cost_execution_auditor", full_prompt, llm_client=llm_client, model=model)


def review(
    proposal: dict,
    changelog: list,
    oos_backtest: dict,
    risk_rules: str,
    current_portfolio: dict,
    recent_trades: list,
    current_account: dict,
    llm_client,
    audit_log_path: str | None = None,
    model: str = "deepseek-v4-pro",
) -> dict:
    """Top-level audit_layer entry point.

    Dispatches all 3 reviewers, aggregates verdicts via aggregate_verdicts,
    and appends the result to audit_log.json. Returns the decision dict.
    See spec §6.
    """
    from datetime import datetime, timezone

    validate_proposal(proposal)

    overfitting = run_overfitting_auditor(
        proposal=proposal, changelog=changelog, oos_backtest=oos_backtest,
        llm_client=llm_client, model=model,
    )
    risk = run_risk_auditor(
        proposal=proposal, risk_rules=risk_rules, current_portfolio=current_portfolio,
        llm_client=llm_client, model=model,
    )
    cost = run_cost_execution_auditor(
        proposal=proposal, recent_trades=recent_trades, current_account=current_account,
        llm_client=llm_client, model=model,
    )

    verdicts = [overfitting, risk, cost]
    decision = aggregate_verdicts(verdicts)

    log_entry = {
        "proposal_id": proposal["proposal_id"],
        "audited_at": datetime.now(timezone.utc).isoformat(),
        "decision": decision["decision"],
        "reason": decision.get("reason", ""),
        "verdicts": {
            "overfitting": overfitting,
            "risk": risk,
            "cost_execution": cost,
        },
        "oos_backtest_summary": oos_backtest,
    }
    append_audit_log(log_entry, log_path=audit_log_path)

    return {
        **decision,
        "verdicts": {"overfitting": overfitting, "risk": risk, "cost_execution": cost},
        "proposal_id": proposal["proposal_id"],
    }
