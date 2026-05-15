import json
import os
import sys


WORKTREE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, WORKTREE)
sys.path.insert(0, os.path.join(WORKTREE, "agents"))

from coordinator import MultiAgentCoordinator


coord = MultiAgentCoordinator()
maintainer = coord.agents["strategy_maintainer"]

adjustments = [
    {
        "type": "parameter_adjustment",
        "strategy": "main",
        "parameter": "take_profit_pct",
        "old_value": 15,
        "new_value": 12,
        "reason": "L3 dry-run",
        "triggering_event_count": 3,
    }
]
proposal = maintainer.propose(adjustments)
print(f"proposal_id: {proposal['proposal_id']}")

fake_review = {"accounts": {"main": {"net_value": 1000000}}}
evidence = coord._build_oos_backtest_evidence(maintainer, proposal, fake_review)

print(f"\nstatus: {evidence.get('status')}")
print(f"window: {evidence.get('window')}")
print(f"sources_used: {evidence.get('data', {}).get('sources_used')}")
if evidence.get("status") == "OK":
    print(f"current:  {json.dumps(evidence['current'], ensure_ascii=False)}")
    print(f"proposed: {json.dumps(evidence['proposed'], ensure_ascii=False)}")
    print(f"DIFFERENT? {evidence['current'] != evidence['proposed']}")
else:
    print(f"reason: {evidence.get('reason')}")
    print(json.dumps(evidence, ensure_ascii=False, indent=2, default=str))
