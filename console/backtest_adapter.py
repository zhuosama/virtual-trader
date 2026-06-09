"""Backtest adapter — bridges backtest_engine.py with the console Gateway.

Runs backtests programmatically and returns structured results.
Only reads from accounts/, strategies/, trades/ — never writes to them.
Results are written to backtest/runs/<run-id>.json.
"""

import json
import os
import sys
import math
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

VT_DIR = Path(os.environ.get("VTRADER_HOME", Path.home() / ".hermes" / "virtual-trader"))
RUNS_DIR = VT_DIR / "backtest" / "runs"

# Import backtest engine
ENGINE_DIR = VT_DIR / "backtest"
sys.path.insert(0, str(ENGINE_DIR))


def run_backtest_task(request: dict) -> dict:
    """Execute a backtest and return the result dict.

    Args:
        request: {
            "strategyId": "main-v1.0.5",
            "startDate": "2026-04-13",
            "endDate": "2026-05-07",
            "benchmark": "hs300",
            "initialCapitalMode": "demo-normalized"
        }

    Returns:
        Full result dict with metrics and daily series.
    """
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    strategy_id = request.get("strategyId", "main-v1.0.5")
    start_date = request.get("startDate", "2026-04-13")
    end_date = request.get("endDate", "2026-05-07")
    benchmark = request.get("benchmark", "hs300")

    # Parse account from strategyId (e.g. "main-v1.0.5" → "main")
    account = "main"
    if strategy_id.startswith("lab"):
        account = "lab"

    result = {
        "runId": run_id,
        "status": "running",
        "strategyId": strategy_id,
        "account": account,
        "startDate": start_date,
        "endDate": end_date,
        "benchmark": benchmark,
        "initialCapitalMode": request.get("initialCapitalMode", "demo-normalized"),
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "completedAt": None,
        "error": None,
    }

    try:
        # Import engine (deferred to avoid import errors at module load)
        import backtest_engine as engine

        # Load trades (read-only)
        trades = engine.load_trade_files(start_date, end_date)
        if not trades:
            result["status"] = "failed"
            result["error"] = f"No trade records found for {start_date} ~ {end_date}"
            result["completedAt"] = datetime.now(timezone.utc).isoformat()
            _save_run(result)
            return result

        # Run backtest
        df, accounts, prices = engine.run_backtest(trades, account)

        if df.empty:
            result["status"] = "failed"
            result["error"] = "Backtest produced empty result (no trading days)"
            result["completedAt"] = datetime.now(timezone.utc).isoformat()
            _save_run(result)
            return result

        # Filter DataFrame to endDate boundary for report generation
        df_filtered = df[df["date"] <= end_date].copy()

        # Generate report and metrics from filtered data
        report_text, metrics = engine.generate_report(df_filtered, accounts, prices)

        # Extract metrics for the target account
        acc = accounts.get(account)
        m = metrics.get(account, {})

        if acc is None:
            result["status"] = "failed"
            result["error"] = f"Account '{account}' not found in backtest results"
            result["completedAt"] = datetime.now(timezone.utc).isoformat()
            _save_run(result)
            return result

        # Compute additional fields
        initial = acc.initial
        hs300_ret = df_filtered["hs300_ret"].iloc[-1]
        total_ret = m.get("total_ret", 0)
        excess_ret = total_ret - hs300_ret

        # Build daily series (filter to endDate boundary)
        daily_series = []
        for i, row in df_filtered.iterrows():
            row_date = row["date"]
            if row_date > end_date:
                continue
            total_val = row[f"{account}_total"]
            prev_val = df.iloc[max(0, i - 1)][f"{account}_total"]
            daily_ret = ((total_val / prev_val) - 1) * 100 if i > 0 else 0
            daily_series.append({
                "date": row_date,
                "totalValue": round(total_val, 2),
                "dailyReturnPct": round(daily_ret, 4),
                "hs300ReturnPct": round(row["hs300_ret"], 4),
                "positionCount": int(row.get(f"{account}_pos_n", 0)),
            })

        # Build trade summary (closed trades only, within endDate)
        trade_summary = []
        for t in acc.trade_log:
            if t["action"] == "sell" and t["date"] <= end_date:
                trade_summary.append({
                    "date": t["date"],
                    "code": t["code"],
                    "name": t.get("name", t["code"]),
                    "cost": round(t.get("cost", t["price"]), 2),
                    "sellPrice": round(t["price"], 2),
                    "shares": t["shares"],
                    "pnl": round(t.get("pnl", 0), 2),
                })

        # Populate result
        result["status"] = "completed"
        result["completedAt"] = datetime.now(timezone.utc).isoformat()
        result["privateOnly"] = True
        result["privacy"] = "local-private"
        result["initialCapital"] = round(initial, 2)
        result["cumulativeReturnPct"] = round(total_ret, 4)
        result["annualizedReturnPct"] = round(m.get("annual_ret", 0), 4)
        result["maxDrawdownPct"] = round(m.get("max_dd", 0), 4)
        result["winRatePct"] = round(m.get("win_rate", 0), 2) if m.get("total_trades", 0) > 0 else None
        result["tradeCount"] = m.get("total_trades", 0)
        result["benchmarkReturnPct"] = round(hs300_ret, 4)
        result["excessReturnPct"] = round(excess_ret, 4)
        result["sharpeRatio"] = round(m.get("sharpe", 0), 4)
        result["profitFactor"] = round(m.get("profit_factor", 0), 4)
        result["totalFees"] = round(m.get("total_fees", 0), 2)
        result["realizedPnl"] = round(m.get("realized_pnl", 0), 2)
        result["dailySeries"] = daily_series
        result["tradeSummary"] = trade_summary
        result["reportText"] = report_text

    except Exception as e:
        result["status"] = "failed"
        result["error"] = f"{type(e).__name__}: {e}"
        result["completedAt"] = datetime.now(timezone.utc).isoformat()
        result["traceback"] = traceback.format_exc()

    _save_run(result)
    return result


def _save_run(result: dict):
    """Save run result to backtest/runs/<run-id>.json."""
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    path = RUNS_DIR / f"{result['runId']}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"[Backtest] Run {result['runId']} saved ({result['status']})")


def list_runs() -> list:
    """List all backtest runs (metadata only, no dailySeries)."""
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    runs = []
    for f in sorted(RUNS_DIR.glob("*.json"), reverse=True):
        try:
            data = json.loads(f.read_text())
            # Return summary only (exclude large fields)
            runs.append({
                "runId": data.get("runId"),
                "status": data.get("status"),
                "strategyId": data.get("strategyId"),
                "account": data.get("account"),
                "startDate": data.get("startDate"),
                "endDate": data.get("endDate"),
                "benchmark": data.get("benchmark"),
                "cumulativeReturnPct": data.get("cumulativeReturnPct"),
                "maxDrawdownPct": data.get("maxDrawdownPct"),
                "winRatePct": data.get("winRatePct"),
                "tradeCount": data.get("tradeCount"),
                "excessReturnPct": data.get("excessReturnPct"),
                "createdAt": data.get("createdAt"),
                "completedAt": data.get("completedAt"),
                "error": data.get("error"),
            })
        except Exception:
            continue
    return runs


def get_run(run_id: str) -> dict:
    """Get full run result by id. Returns None if not found."""
    path = RUNS_DIR / f"{run_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def compare_runs(run_ids: list) -> dict:
    """Compare multiple backtest runs — returns aggregated metrics + daily series.

    Returns a dict with:
      runs: list of run summaries (no reportText, no full tradeSummary)
      rankings: best return / drawdown / excess / sharpe
    Only completed runs participate in rankings; failed runs are included
    with their status but flagged.
    """
    runs_data = []
    for rid in run_ids:
        raw = get_run(rid)
        if not raw:
            runs_data.append({
                "runId": rid,
                "status": "not_found",
                "error": f"Run '{rid}' not found",
            })
            continue

        entry = {
            "runId": raw.get("runId"),
            "strategyId": raw.get("strategyId"),
            "account": raw.get("account"),
            "startDate": raw.get("startDate"),
            "endDate": raw.get("endDate"),
            "status": raw.get("status"),
            "initialCapital": raw.get("initialCapital"),
            "cumulativeReturnPct": raw.get("cumulativeReturnPct"),
            "benchmarkReturnPct": raw.get("benchmarkReturnPct"),
            "excessReturnPct": raw.get("excessReturnPct"),
            "maxDrawdownPct": raw.get("maxDrawdownPct"),
            "winRatePct": raw.get("winRatePct"),
            "tradeCount": raw.get("tradeCount"),
            "sharpeRatio": raw.get("sharpeRatio"),
            "realizedPnl": raw.get("realizedPnl"),
            "totalFees": raw.get("totalFees"),
            "error": raw.get("error"),
        }

        # Include dailySeries for charts (lightweight — no reportText/tradeSummary)
        ds = raw.get("dailySeries", [])
        if ds:
            entry["dailySeries"] = ds

        runs_data.append(entry)

    # Compute rankings (only completed runs with valid numeric values)
    completed = [r for r in runs_data if r.get("status") == "completed"]

    def best_of(key, higher=True):
        if not completed:
            return None
        valid = [r for r in completed if r.get(key) is not None]
        if not valid:
            return None
        return max(valid, key=lambda r: r[key] if higher else -r[key])["runId"]

    def best_of_lower(key):
        if not completed:
            return None
        valid = [r for r in completed if r.get(key) is not None]
        if not valid:
            return None
        return min(valid, key=lambda r: r[key])["runId"]

    rankings = {
        "bestReturn": best_of("cumulativeReturnPct"),
        "bestDrawdown": best_of_lower("maxDrawdownPct"),  # least negative
        "bestExcess": best_of("excessReturnPct"),
        "bestSharpe": best_of("sharpeRatio"),
    }

    return {
        "privateOnly": True,
        "privacy": "local-private",
        "runs": runs_data,
        "rankings": rankings,
    }
