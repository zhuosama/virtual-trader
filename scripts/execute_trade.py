import json, os

VTRADER_HOME = os.environ.get("VTRADER_HOME", os.path.expanduser("~/.hermes/virtual-trader"))

base = VTRADER_HOME

# Load main account
with open(os.path.join(base, "accounts/main.json")) as f:
    main = json.load(f)

# Trade: Buy 大秦铁路 601006, 8000 shares at 5.39
price = 5.39
shares = 8000
amount = price * shares
commission = max(amount * 0.0003, 5)
stamp_tax = 0
transfer_fee = amount * 0.00002
total_cost = round(amount + commission + stamp_tax + transfer_fee, 2)

# Update account
main["cash"] = round(main["cash"] - total_cost, 2)
main["positions"].append({
    "code": "601006",
    "name": "大秦铁路",
    "type": "stock",
    "shares": 8000,
    "avg_cost": round(price + (commission + stamp_tax + transfer_fee) / shares, 4),
    "buy_date": "2026-04-28",
    "buy_price": price,
    "current_price": price,
    "market_value": amount,
    "unrealized_pnl": 0,
    "unrealized_pnl_pct": 0
})
main["trade_count"] = main.get("trade_count", 0) + 1

# Recalculate totals
portfolio_mv = sum(p["market_value"] for p in main["positions"])
main["portfolio_market_value"] = round(portfolio_mv, 2)
main["total_value"] = round(main["cash"] + portfolio_mv, 2)
main["position_pct"] = round(portfolio_mv / main["total_value"] * 100, 2)

with open(os.path.join(base, "accounts/main.json"), "w") as f:
    json.dump(main, f, indent=2, ensure_ascii=False)

print("TRADE: Buy 大秦铁路 601006, {} shares @ {}".format(shares, price))
print("Amount: {}, Commission: {:.2f}, Transfer: {:.2f}".format(amount, commission, transfer_fee))
print("Total cost: {}".format(total_cost))
print("New cash: {:,.2f}".format(main["cash"]))
print("New position: {:.2f}%".format(main["position_pct"]))

# Create trade record
trades_dir = os.path.join(base, "trades/2026-04")
os.makedirs(trades_dir, exist_ok=True)
trade_file = os.path.join(trades_dir, "2026-04-28.json")

trade_record = {
    "date": "2026-04-28",
    "is_trading_day": True,
    "market_summary": {
        "shanghai_index": {"close": 4078.64, "change_pct": -0.19},
        "shenzhen_index": {"close": 14830.46, "change_pct": -1.10},
        "hs300_index": {"close": 4758.21, "change_pct": -0.27},
        "northbound_flow_billion": None,
        "total_turnover_billion": 1113.96
    },
    "trades": [
        {
            "account": "main",
            "time": "15:00",
            "action": "buy",
            "code": "601006",
            "name": "大秦铁路",
            "type": "stock",
            "price": price,
            "shares": shares,
            "amount": amount,
            "commission": round(commission, 2),
            "stamp_tax": 0,
            "transfer_fee": round(transfer_fee, 2),
            "total_cost": total_cost,
            "signal": "强制加仓：仓位45.35%低于50%下限，选择大秦铁路(股息率6%+，防御属性强)",
            "strategy_ref": "value_trend_hybrid_v1.0.5"
        }
    ],
    "account_snapshots": {
        "main": {
            "total_value": main["total_value"],
            "daily_pnl": round(main["total_value"] - 999284.33, 2),
            "daily_pnl_pct": round((main["total_value"] / 999284.33 - 1) * 100, 4)
        },
        "lab": {
            "total_value": 311448.61,
            "daily_pnl": -859.80,
            "daily_pnl_pct": -0.2753
        }
    }
}

with open(trade_file, "w") as f:
    json.dump(trade_record, f, indent=2, ensure_ascii=False)

print("\nTrade record saved to {}".format(trade_file))
