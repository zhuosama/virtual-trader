import json, os

VTRADER_HOME = os.environ.get("VTRADER_HOME", os.path.expanduser("~/.hermes/virtual-trader"))

base = VTRADER_HOME
ph_path = os.path.join(base, "strategies/performance_history.json")

with open(ph_path) as f:
    history = json.load(f)

# Add today's record
history.append({
    "date": "2026-04-28",
    "main_pct": 0.7168,
    "lab_pct": -0.2753,
    "hs300_pct": -0.27,
    "main_beat": True,
    "lab_beat": False
})

# Keep only last 30 days
if len(history) > 30:
    history = history[-30:]

with open(ph_path, "w") as f:
    json.dump(history, f, indent=2, ensure_ascii=False)

print("Performance history updated. {} records.".format(len(history)))

# Check strategy triggers - last 3 days
last3 = history[-3:]
main_underperform = all(not r["main_beat"] for r in last3)
lab_underperform = all(not r["lab_beat"] for r in last3)

print("\nLast 3 days:")
for r in last3:
    print("  {}: main={}% ({}), lab={}% ({}), hs300={}%".format(
        r["date"], r["main_pct"], "beat" if r["main_beat"] else "lag",
        r["lab_pct"], "beat" if r["lab_beat"] else "lag", r["hs300_pct"]))

if main_underperform:
    print("\n!!! MAIN: 3 consecutive days underperforming - trigger parameter tweak")
elif lab_underperform:
    print("\n!!! LAB: 3 consecutive days underperforming - trigger parameter tweak")
else:
    print("\nNo strategy adjustment needed.")
