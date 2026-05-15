import json
import os
import sys
import argparse


WORKTREE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("VTRADER_HOME", WORKTREE)
sys.path.insert(0, WORKTREE)
sys.path.insert(0, os.path.join(WORKTREE, "agents"))

from coordinator import MultiAgentCoordinator


parser = argparse.ArgumentParser()
parser.add_argument(
    "--calibration-window",
    action="store_true",
    help="Use an in-memory older changelog basis to prove real-provider OOS evidence when strict post-change OOS days are insufficient.",
)
args = parser.parse_args()

coord = MultiAgentCoordinator()
maintainer = coord.agents["strategy_maintainer"]

if args.calibration_window:
    maintainer.changelog = [{"date": "2026-04-10", "account": "main", "change_type": "strategy"}]

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
if args.calibration_window:
    proposal["diff"] = [
        {
            "path": "main_strategy.rules.position_sizing.initial_position",
            "old": 0.08,
            "new": 0.04,
        }
    ]
print(f"proposal_id: {proposal['proposal_id']}")
if args.calibration_window:
    print("mode: calibration-window (production gate unchanged)")

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

proposal_path = os.path.join(WORKTREE, "strategies", "proposals", f"{proposal['proposal_id']}.json")
if os.path.exists(proposal_path):
    os.remove(proposal_path)
