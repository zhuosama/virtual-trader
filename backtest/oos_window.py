STRATEGY_CHANGE_TYPES = {"strategy", "parameter_adjustment", "strategy_upgrade", "param", "init"}


def _entry_account(entry):
    return entry.get("account") or entry.get("strategy")


def _is_strategy_entry(entry):
    change_type = entry.get("change_type")
    if change_type in STRATEGY_CHANGE_TYPES:
        return True
    if "change" in entry:
        return True
    return False


def compute_oos_window(changelog, account, today, trading_calendar):
    account_entries = [
        entry
        for entry in changelog
        if _entry_account(entry) in {account, "both"} and entry.get("date")
    ]
    if not account_entries:
        return {"status": "INFRA_ERROR", "reason": "NO_CHANGELOG_BASIS", "trading_days": 0}

    earliest = min(entry["date"] for entry in account_entries)
    strategy_entries = [entry for entry in account_entries if _is_strategy_entry(entry)]
    basis = max((entry["date"] for entry in strategy_entries), default=earliest)

    usable = [
        day
        for day in sorted(trading_calendar)
        if day > basis and day <= today and day >= earliest
    ]
    if len(usable) < 20:
        return {
            "status": "INFRA_ERROR",
            "reason": "INSUFFICIENT_OOS_DAYS",
            "start": usable[0] if usable else None,
            "end": usable[-1] if usable else None,
            "trading_days": len(usable),
            "basis_entry_date": basis,
        }
    window = usable[:30]
    return {
        "status": "OK",
        "start": window[0],
        "end": window[-1],
        "trading_days": len(window),
        "basis_entry_date": basis,
    }
