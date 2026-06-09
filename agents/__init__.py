"""Virtual Trader agents package.

Public API: only `propose` and audit_layer interfaces are intended for
external callers. Private methods (_save_*, _apply_*) on individual agents
are guarded by tests/audit_layer/test_no_bypass.py — do not import them
from outside agents/strategy_maintainer.py or agents/audit_layer.py.
"""

__all__ = [
    "audit_layer",
    "audit_subagent",
    "strategy_maintainer",
    "review_agent",
    "risk_controller",
    "execution_planner",
    "market_analyst",
    "coordinator",
    "llm_client",
]
