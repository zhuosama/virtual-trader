import json, os

VTRADER_HOME = os.environ.get("VTRADER_HOME", os.path.expanduser("~/.hermes/virtual-trader"))

prices = {"600900": 26.73, "600036": 39.60, "002415": 34.65, "601318": 57.63, "000333": 80.45, "000651": 38.44, "601088": 48.15, "000858": 100.20, "601006": 5.39, "300750": 427.67, "688256": 1374.50, "688111": 247.58, "688981": 114.00}
base = os.path.join(VTRADER_HOME, "accounts")

for acct_name in ["main", "lab"]:
    path = os.path.join(base, acct_name + ".json")
    with open(path) as f:
        acct = json.load(f)
    portfolio_mv = 0
    for pos in acct["positions"]:
        new_price = prices[pos["code"]]
        shares = pos["shares"]
        pos["current_price"] = new_price
        pos["market_value"] = round(new_price * shares, 2)
        pos["unrealized_pnl"] = round((new_price - pos["avg_cost"]) * shares, 2)
        pos["unrealized_pnl_pct"] = round((new_price / pos["avg_cost"] - 1) * 100, 2)
        portfolio_mv += pos["market_value"]
    old_total = acct["total_value"]
    acct["portfolio_market_value"] = round(portfolio_mv, 2)
    acct["total_value"] = round(acct["cash"] + portfolio_mv, 2)
    acct["daily_pnl"] = round(acct["total_value"] - old_total, 2)
    acct["daily_pnl_pct"] = round((acct["total_value"] / old_total - 1) * 100, 4)
    acct["total_pnl"] = round(acct["total_value"] - acct["initial_capital"], 2)
    acct["total_pnl_pct"] = round((acct["total_value"] / acct["initial_capital"] - 1) * 100, 2)
    acct["position_pct"] = round(portfolio_mv / acct["total_value"] * 100, 2)
    acct["updated_at"] = "2026-04-28T15:00:00"
    with open(path, "w") as f:
        json.dump(acct, f, indent=2, ensure_ascii=False)
    print("=== " + acct_name.upper() + " ===")
    print("Total: {:,.2f}  Daily PnL: {:+,.2f} ({:+.4f}%)".format(acct["total_value"], acct["daily_pnl"], acct["daily_pnl_pct"]))
    print("Position: {:.2f}%  Cash: {:,.2f}".format(acct["position_pct"], acct["cash"]))
    for p in acct["positions"]:
        print("  {} {} mv={:,.2f} pnl={:+,.2f} ({:+.2f}%)".format(p["name"], p["current_price"], p["market_value"], p["unrealized_pnl"], p["unrealized_pnl_pct"]))
