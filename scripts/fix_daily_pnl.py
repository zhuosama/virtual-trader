import json, os

VTRADER_HOME = os.environ.get("VTRADER_HOME", os.path.expanduser("~/.hermes/virtual-trader"))

base = os.path.join(VTRADER_HOME, "accounts")
prev = {"main": 999284.33, "lab": 312308.41}

for name in ["main", "lab"]:
    path = os.path.join(base, name + ".json")
    with open(path) as f:
        acct = json.load(f)
    acct["daily_pnl"] = round(acct["total_value"] - prev[name], 2)
    acct["daily_pnl_pct"] = round((acct["total_value"] / prev[name] - 1) * 100, 4)
    with open(path, "w") as f:
        json.dump(acct, f, indent=2, ensure_ascii=False)
    print("{}: daily_pnl={:+,.2f} ({:+.4f}%)".format(name, acct["daily_pnl"], acct["daily_pnl_pct"]))
