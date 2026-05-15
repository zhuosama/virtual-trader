import json
import os
from collections import Counter
from datetime import datetime, timedelta


AUDIT_LOG = os.path.join("strategies", "audit_log.json")


def load_log():
    if not os.path.exists(AUDIT_LOG):
        return []
    with open(AUDIT_LOG, "r", encoding="utf-8") as f:
        return json.load(f)


log = load_log()
cutoff = (datetime.now() - timedelta(days=7)).isoformat()
recent = [e for e in log if e.get("audited_at", "") >= cutoff]
n = max(len(recent), 1)

print(f"=== Audit health (last 7 days, n={len(recent)}) ===\n")
decisions = Counter(e.get("decision", "UNKNOWN") for e in recent)
for decision, count in decisions.most_common():
    print(f"  {decision:20s} {count:3d}  {100 * count / n:.1f}%")

alerts = []
if recent and decisions.get("PENDING_RETRY", 0) / n > 0.3:
    alerts.append(
        f"PENDING_RETRY rate {100 * decisions['PENDING_RETRY'] / n:.0f}% > 30% - provider chain unstable"
    )
if decisions.get("AUTO_MERGE", 0) == 0 and len(recent) >= 5:
    alerts.append(f"0 AUTO_MERGE over {len(recent)} proposals - strategy iteration stalled")
unsupported = sum(1 for e in recent if "UNSUPPORTED_STRATEGY_DIFF" in json.dumps(e, ensure_ascii=False))
if recent and unsupported / n > 0.5:
    alerts.append(f"{100 * unsupported / n:.0f}% UNSUPPORTED_STRATEGY_DIFF - simulator scope too narrow")

if alerts:
    print("\n=== ALERTS ===")
    for alert in alerts:
        print(alert)
else:
    print("\nno alerts")
