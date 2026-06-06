#!/usr/bin/env python3
"""H47 production-candidate price rebuild using Tushare qfq data.

The script writes a new candidate matrix and never overwrites H38 research data.
It is designed to be resumable through per-ticker raw CSV cache files.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set

import pandas as pd


ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data/cn_pit"
RAW_DIR = DATA_DIR / "raw/h47_tushare_qfq"
DEFAULT_UNIVERSE = DATA_DIR / "universe_h30_candidate.jsonl"
DEFAULT_PRICES_OUT = DATA_DIR / "prices_h47_tushare_qfq_candidate.csv"
DEFAULT_COVERAGE_OUT = DATA_DIR / "price_coverage_h47.json"
DEFAULT_REPORT_OUT = ROOT / "reports/h47_tushare_qfq_price_rebuild_report.md"
HS300_TICKER = "000300.SS"
HS300_INDEX_CODE = "399300.SZ"
STOCK_PROVIDER = "tushare:pro_bar:qfq"
BENCHMARK_PROVIDER = "tushare:index_daily"


@dataclass(frozen=True)
class FetchResult:
    ticker: str
    rows: int
    start: Optional[str]
    end: Optional[str]
    source: str
    cached: bool
    error: Optional[str] = None

    def to_dict(self) -> Dict:
        return {
            "ticker": self.ticker,
            "rows": self.rows,
            "start": self.start,
            "end": self.end,
            "source": self.source,
            "cached": self.cached,
            "error": self.error,
        }


def to_tushare_code(ticker: str) -> str:
    if ticker.endswith(".SS"):
        return ticker[:-3] + ".SH"
    if ticker.endswith(".SZ"):
        return ticker
    raise ValueError(f"unsupported ticker suffix: {ticker}")


def to_yahoo_style(ts_code: str) -> str:
    if ts_code.endswith(".SH"):
        return ts_code[:-3] + ".SS"
    if ts_code.endswith(".SZ"):
        return ts_code
    raise ValueError(f"unsupported ts_code suffix: {ts_code}")


def compact_date(date_str: str) -> str:
    return date_str.replace("-", "")


def dashed_date(date_str: str) -> str:
    if len(date_str) == 8 and date_str.isdigit():
        return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    return date_str


def parse_iso_date(value: str) -> datetime.date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def get_tushare_token() -> str:
    token = os.environ.get("TUSHARE_TOKEN", "").strip()
    if token:
        return token
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import ingest_cn_pit_data as ingest  # noqa: WPS433

        token = (ingest._get_tushare_token() or "").strip()
        if token:
            return token
    except Exception:
        pass
    raise RuntimeError("Tushare token is missing")


def load_universe_rows(path: Path, start: str, end: str) -> List[Dict]:
    rows = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    start_date = parse_iso_date(start)
    requested_end_date = parse_iso_date(end)
    scoped = []
    for row in rows:
        eff = str(row.get("effective_date", ""))
        raw_end = str(row.get("end_date") or "9999-12-31")
        eff_date = parse_iso_date(eff)
        row_end_date = parse_iso_date(raw_end)
        if eff_date <= requested_end_date and row_end_date >= start_date:
            ticker = row.get("ticker")
            if ticker:
                scoped.append({
                    "ticker": str(ticker),
                    "effective_date": eff_date.isoformat(),
                    "end_date": row_end_date.isoformat(),
                })
    return scoped


def load_universe_tickers(path: Path, start: str, end: str) -> List[str]:
    tickers = {row["ticker"] for row in load_universe_rows(path, start, end)}
    return sorted(tickers)


def required_windows_by_ticker(rows: Sequence[Dict], start: str, end: str) -> Dict[str, Dict[str, str]]:
    start_date = parse_iso_date(start)
    end_date = parse_iso_date(end)
    windows: Dict[str, Dict[str, str]] = {}
    for row in rows:
        ticker = row["ticker"]
        eff = max(parse_iso_date(row["effective_date"]), start_date)
        row_end = min(parse_iso_date(row["end_date"]), end_date)
        if eff > row_end:
            continue
        current = windows.get(ticker)
        if current is None:
            windows[ticker] = {"start": eff.isoformat(), "end": row_end.isoformat()}
        else:
            current["start"] = min(current["start"], eff.isoformat())
            current["end"] = max(current["end"], row_end.isoformat())
    return windows


def read_price_cache(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=["date", "close"])
    df = pd.read_csv(path)
    if df.empty or not {"date", "close"}.issubset(df.columns):
        return pd.DataFrame(columns=["date", "close"])
    df["date"] = df["date"].astype(str)
    return df[["date", "close"]].dropna(subset=["date", "close"])


def cache_covers_required_window(frame: pd.DataFrame, required_start: str, required_end: str) -> bool:
    if frame.empty:
        return False
    return str(frame["date"].min()) <= required_start and str(frame["date"].max()) >= required_end


def normalize_stock_frame(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["date", "close"])
    out = df.copy()
    if "trade_date" not in out.columns or "close" not in out.columns:
        return pd.DataFrame(columns=["date", "close"])
    out["date"] = out["trade_date"].astype(str).map(dashed_date)
    out["close"] = pd.to_numeric(out["close"], errors="coerce")
    out = out[["date", "close"]].dropna(subset=["date", "close"])
    out = out.sort_values("date").drop_duplicates("date", keep="last")
    out["ticker"] = ticker
    return out[["date", "close"]]


def fetch_stock_frame(pro_api, ticker: str, start: str, end: str) -> pd.DataFrame:
    import tushare as ts  # noqa: WPS433

    del pro_api
    df = ts.pro_bar(
        ts_code=to_tushare_code(ticker),
        start_date=compact_date(start),
        end_date=compact_date(end),
        adj="qfq",
        freq="D",
    )
    return normalize_stock_frame(df, ticker)


def fetch_index_frame(pro_api, start: str, end: str) -> pd.DataFrame:
    df = pro_api.index_daily(
        ts_code=HS300_INDEX_CODE,
        start_date=compact_date(start),
        end_date=compact_date(end),
    )
    if df is None or df.empty:
        return pd.DataFrame(columns=["date", "close"])
    out = df.copy()
    out["date"] = out["trade_date"].astype(str).map(dashed_date)
    out["close"] = pd.to_numeric(out["close"], errors="coerce")
    out = out[["date", "close"]].dropna(subset=["date", "close"])
    return out.sort_values("date").drop_duplicates("date", keep="last")


def cache_path_for(ticker: str, raw_dir: Path) -> Path:
    safe = ticker.replace(".", "_")
    return raw_dir / f"{safe}.csv"


def fetch_or_load_stock(
    pro_api,
    ticker: str,
    start: str,
    end: str,
    raw_dir: Path,
    force_refresh: bool,
    sleep_seconds: float,
    required_start: str,
    required_end: str,
) -> tuple[pd.DataFrame, FetchResult]:
    path = cache_path_for(ticker, raw_dir)
    if path.exists() and not force_refresh:
        cached = read_price_cache(path)
        if cache_covers_required_window(cached, required_start, required_end):
            return cached, FetchResult(
                ticker=ticker,
                rows=len(cached),
                start=str(cached["date"].min()),
                end=str(cached["date"].max()),
                source=STOCK_PROVIDER,
                cached=True,
            )
    try:
        frame = fetch_stock_frame(pro_api, ticker, start, end)
        raw_dir.mkdir(parents=True, exist_ok=True)
        frame.to_csv(path, index=False)
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)
        return frame, FetchResult(
            ticker=ticker,
            rows=len(frame),
            start=str(frame["date"].min()) if not frame.empty else None,
            end=str(frame["date"].max()) if not frame.empty else None,
            source=STOCK_PROVIDER,
            cached=False,
        )
    except Exception as exc:  # noqa: BLE001
        return pd.DataFrame(columns=["date", "close"]), FetchResult(
            ticker=ticker,
            rows=0,
            start=None,
            end=None,
            source=STOCK_PROVIDER,
            cached=False,
            error=f"{type(exc).__name__}: {exc}",
        )


def build_price_matrix(frames: Dict[str, pd.DataFrame], benchmark: pd.DataFrame) -> pd.DataFrame:
    all_dates: Set[str] = set()
    for frame in frames.values():
        all_dates.update(frame["date"].astype(str).tolist())
    if not benchmark.empty:
        all_dates.update(benchmark["date"].astype(str).tolist())
    dates = sorted(all_dates)
    columns: Dict[str, pd.Series] = {}
    for ticker in sorted(frames):
        frame = frames[ticker]
        if frame.empty:
            columns[ticker] = pd.Series([pd.NA] * len(dates), index=dates)
            continue
        series = frame.drop_duplicates("date", keep="last").set_index("date")["close"]
        columns[ticker] = pd.Series(dates, index=dates).map(series)
    if not benchmark.empty:
        bench = benchmark.drop_duplicates("date", keep="last").set_index("date")["close"]
        columns[HS300_TICKER] = pd.Series(dates, index=dates).map(bench)
    matrix = pd.DataFrame(columns)
    matrix.insert(0, "date", dates)
    return matrix.reset_index(drop=True)


def fill_active_price_gaps(matrix: pd.DataFrame, required_windows: Dict[str, Dict[str, str]]) -> Dict:
    """Forward-fill active-period stock close gaps for MTM continuity.

    Tushare omits rows on suspended/no-trade days. The close matrix needs a
    mark-to-market value on those dates, while tradability should be handled by
    separate liquidity/volume gates.
    """
    filled_cells = 0
    filled_by_ticker = {}
    for ticker, required in required_windows.items():
        if ticker not in matrix.columns:
            continue
        active_mask = (matrix["date"] >= required["start"]) & (matrix["date"] <= required["end"])
        ffilled = matrix[ticker].ffill()
        fill_mask = active_mask & matrix[ticker].isna() & ffilled.notna()
        count = int(fill_mask.sum())
        if count:
            matrix.loc[fill_mask, ticker] = ffilled.loc[fill_mask]
            filled_cells += count
            filled_by_ticker[ticker] = count
    return {
        "fill_method": "ffill_active_period_from_tushare_qfq_close",
        "filled_active_gap_cells": filled_cells,
        "filled_ticker_count": len(filled_by_ticker),
        "filled_by_ticker": filled_by_ticker,
    }


def coverage_for_matrix(
    matrix: pd.DataFrame,
    tickers: Sequence[str],
    start: str,
    end: str,
    required_windows: Optional[Dict[str, Dict[str, str]]] = None,
    min_active_coverage_ratio: float = 0.90,
) -> Dict:
    if matrix.empty:
        return {
            "ok": False,
            "reason": "empty_matrix",
            "date_range": "",
            "rows": 0,
            "ticker_columns": 0,
            "missing_columns": list(tickers),
            "missing_data_columns": list(tickers),
            "benchmark_present": False,
            "partial_coverage": [],
        }
    stock_cols = [ticker for ticker in tickers if ticker in matrix.columns]
    missing_cols = sorted(set(tickers) - set(stock_cols))
    window = matrix[(matrix["date"] >= start) & (matrix["date"] <= end)]
    missing_data = []
    partial_coverage = []
    required_windows = required_windows or {ticker: {"start": start, "end": end} for ticker in tickers}
    for ticker in stock_cols:
        required = required_windows.get(ticker, {"start": start, "end": end})
        active = window[(window["date"] >= required["start"]) & (window["date"] <= required["end"])]
        non_null = int(active[ticker].notna().sum()) if ticker in active.columns else 0
        active_days = len(active)
        ratio = non_null / active_days if active_days else 0.0
        first_ok = active_days > 0 and pd.notna(active.iloc[0][ticker])
        last_ok = active_days > 0 and pd.notna(active.iloc[-1][ticker])
        if active_days == 0 or non_null == 0 or ratio < min_active_coverage_ratio or not first_ok or not last_ok:
            missing_data.append(ticker)
            partial_coverage.append({
                "ticker": ticker,
                "required_start": required["start"],
                "required_end": required["end"],
                "active_trading_days": active_days,
                "covered_days": non_null,
                "coverage_ratio": ratio,
                "first_active_price_present": bool(first_ok),
                "last_active_price_present": bool(last_ok),
            })
    benchmark_present = HS300_TICKER in matrix.columns and not window.get(HS300_TICKER, pd.Series(dtype=float)).dropna().empty
    ok = not missing_cols and not missing_data and benchmark_present
    return {
        "ok": ok,
        "reason": "ok" if ok else "coverage_gap",
        "provider": STOCK_PROVIDER,
        "benchmark_provider": BENCHMARK_PROVIDER,
        "stock_adjustment": "qfq",
        "benchmark_adjustment": "published_index_level",
        "active_gap_fill": "ffill_active_period_from_tushare_qfq_close",
        "date_range": f"{matrix['date'].min()} -> {matrix['date'].max()}",
        "requested": {"start": start, "end": end},
        "rows": len(matrix),
        "ticker_columns": len(stock_cols),
        "missing_columns": missing_cols,
        "missing_data_columns": sorted(missing_data),
        "benchmark_present": benchmark_present,
        "min_active_coverage_ratio": min_active_coverage_ratio,
        "partial_coverage": partial_coverage,
    }


def build_report(payload: Dict) -> str:
    coverage = payload["coverage"]
    lines = [
        "# H47 - Tushare QFQ Price Rebuild Report",
        "",
        f"**Generated:** {payload['generated_at']}",
        f"**Status:** {payload['status']}",
        f"**Provider:** {coverage['provider']}",
        f"**Stock adjustment:** {coverage['stock_adjustment']}",
        f"**Benchmark provider:** {coverage['benchmark_provider']}",
        f"**Benchmark adjustment:** {coverage['benchmark_adjustment']}",
        f"**Active gap fill:** {coverage['active_gap_fill']}",
        "",
        "## Coverage",
        "",
        f"- OK: {coverage['ok']}",
        f"- Date range: {coverage['date_range']}",
        f"- Requested: {coverage['requested']['start']} -> {coverage['requested']['end']}",
        f"- Rows: {coverage['rows']}",
        f"- Ticker columns: {coverage['ticker_columns']}",
        f"- Missing columns: {len(coverage['missing_columns'])}",
        f"- Missing data columns: {len(coverage['missing_data_columns'])}",
        f"- Benchmark present: {coverage['benchmark_present']}",
        f"- Minimum active coverage ratio: {coverage['min_active_coverage_ratio']:.2f}",
        f"- Filled active gap cells: {payload['fill_summary']['filled_active_gap_cells']}",
        f"- Filled ticker count: {payload['fill_summary']['filled_ticker_count']}",
        "",
        "## Fetch Summary",
        "",
        f"- Requested tickers: {payload['fetch_summary']['requested_tickers']}",
        f"- Successful tickers: {payload['fetch_summary']['successful_tickers']}",
        f"- Failed tickers: {payload['fetch_summary']['failed_tickers']}",
        f"- Cached tickers: {payload['fetch_summary']['cached_tickers']}",
        "",
        "## Safety",
        "",
        "- Did not overwrite H38 research prices.",
        "- Did not modify production trading config.",
        "- Did not place live orders.",
        "",
    ]
    failed = [row for row in payload["fetch_results"] if row.get("error")]
    if failed:
        lines.extend(["## Failed Tickers", ""])
        for row in failed[:50]:
            lines.append(f"- {row['ticker']}: {row['error']}")
        lines.append("")
    lines.extend([
        "## Verdict",
        "",
        "**CANDIDATE_DATASET** - H47 creates a production-candidate price matrix. It is not a strategy promotion.",
        "",
    ])
    return "\n".join(lines)


def build_payload(
    matrix: pd.DataFrame,
    tickers: List[str],
    results: List[FetchResult],
    start: str,
    end: str,
    required_windows: Optional[Dict[str, Dict[str, str]]] = None,
    fill_summary: Optional[Dict] = None,
) -> Dict:
    coverage = coverage_for_matrix(matrix, tickers, start, end, required_windows)
    successful = [row for row in results if row.rows > 0 and not row.error]
    failed = [row for row in results if row.error or row.rows == 0]
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ"),
        "task": "H47",
        "status": "CANDIDATE_DATASET",
        "coverage": coverage,
        "fill_summary": fill_summary or {
            "fill_method": "none",
            "filled_active_gap_cells": 0,
            "filled_ticker_count": 0,
            "filled_by_ticker": {},
        },
        "fetch_summary": {
            "requested_tickers": len(tickers),
            "successful_tickers": len(successful),
            "failed_tickers": len(failed),
            "cached_tickers": sum(1 for row in results if row.cached),
        },
        "fetch_results": [row.to_dict() for row in results],
    }


def create_tushare_client(token: str):
    import tushare as ts  # noqa: WPS433

    ts.set_token(token)
    return ts.pro_api(token)


def run(args: argparse.Namespace) -> Dict:
    universe_rows = load_universe_rows(args.universe, args.start, args.end)
    required_windows = required_windows_by_ticker(universe_rows, args.start, args.end)
    tickers = sorted(required_windows)
    if args.limit:
        tickers = tickers[: args.limit]
        required_windows = {ticker: required_windows[ticker] for ticker in tickers}
    token = get_tushare_token()
    pro = create_tushare_client(token)
    frames: Dict[str, pd.DataFrame] = {}
    results: List[FetchResult] = []
    for idx, ticker in enumerate(tickers, start=1):
        print(f"[H47] {idx}/{len(tickers)} {ticker}", flush=True)
        frame, result = fetch_or_load_stock(
            pro,
            ticker,
            args.start,
            args.end,
            args.raw_dir,
            args.force_refresh,
            args.sleep,
            required_windows[ticker]["start"],
            required_windows[ticker]["end"],
        )
        frames[ticker] = frame
        results.append(result)
    benchmark = fetch_index_frame(pro, args.start, args.end)
    matrix = build_price_matrix(frames, benchmark)
    fill_summary = fill_active_price_gaps(matrix, required_windows)
    payload = build_payload(matrix, tickers, results, args.start, args.end, required_windows, fill_summary)
    args.output_prices.parent.mkdir(parents=True, exist_ok=True)
    args.output_coverage.parent.mkdir(parents=True, exist_ok=True)
    args.output_report.parent.mkdir(parents=True, exist_ok=True)
    matrix.to_csv(args.output_prices, index=False)
    args.output_coverage.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    args.output_report.write_text(build_report(payload), encoding="utf-8")
    return payload


def is_protected_prior_artifact(path: Path) -> bool:
    text = str(path)
    patterns = [
        r"prices_h3[0-9]_",
        r"prices_h4[0-6]_",
        r"price_coverage_h[0-3][0-9]\.json$",
        r"price_coverage_h4[0-6]\.json$",
        r"reports/h[0-3][0-9]_",
        r"reports/h4[0-6]_",
        r"fundamental_value_h4[0-6]_",
        r"fundamental_value_h3[0-9]_",
    ]
    return any(re.search(pattern, text) for pattern in patterns)


def assert_safe_output_path(path: Path) -> None:
    resolved = path.resolve()
    if resolved == (DATA_DIR / "prices_h38_candidate.csv").resolve():
        raise SystemExit("Refusing to overwrite H38 research price file")
    if is_protected_prior_artifact(path):
        raise SystemExit(f"Refusing to overwrite prior Hxx artifact: {path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build H47 Tushare qfq price matrix")
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--end", default="2026-05-21")
    parser.add_argument("--limit", type=int, default=0, help="Limit tickers for smoke runs")
    parser.add_argument("--sleep", type=float, default=0.12, help="Seconds to sleep between Tushare calls")
    parser.add_argument("--force-refresh", action="store_true")
    parser.add_argument("--universe", type=Path, default=DEFAULT_UNIVERSE)
    parser.add_argument("--raw-dir", type=Path, default=RAW_DIR)
    parser.add_argument("--output-prices", type=Path, default=DEFAULT_PRICES_OUT)
    parser.add_argument("--output-coverage", type=Path, default=DEFAULT_COVERAGE_OUT)
    parser.add_argument("--output-report", type=Path, default=DEFAULT_REPORT_OUT)
    args = parser.parse_args()

    for output_path in (args.output_prices, args.output_coverage, args.output_report):
        assert_safe_output_path(output_path)
    payload = run(args)
    print(f"Wrote prices: {args.output_prices}")
    print(f"Wrote coverage: {args.output_coverage}")
    print(f"Wrote report: {args.output_report}")
    print(f"Coverage OK: {payload['coverage']['ok']}")
    print(f"Successful tickers: {payload['fetch_summary']['successful_tickers']}/{payload['fetch_summary']['requested_tickers']}")
    return 0 if payload["fetch_summary"]["successful_tickers"] > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
