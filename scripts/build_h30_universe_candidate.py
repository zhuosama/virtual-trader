#!/usr/bin/env python3
"""Build an H30 PIT CSI300 universe candidate from snapshot data.

This script is intentionally non-destructive: it never overwrites the official
data/cn_pit/universe*.jsonl files. It accepts a Tushare index_weight-like CSV
or JSONL file and emits H30-suffixed candidate artifacts.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

import ingest_cn_pit_data as ingest


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "cn_pit"
REPORTS_DIR = PROJECT_ROOT / "reports"

DEFAULT_SOURCE_URL = "https://tushare.pro/document/2?doc_id=96"
DEFAULT_PROVIDER = "tushare:index_weight"
DEFAULT_INDEX_CODE = "399300.SZ"
H29_BLOCKERS = {"302132.SZ", "600930.SS", "603296.SS"}


def _format_date(value) -> str:
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return ""
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    try:
        return pd.to_datetime(text).strftime("%Y-%m-%d")
    except Exception as exc:
        raise ValueError(f"invalid trade_date: {value!r}") from exc


def _ticker_from_row(row: Dict) -> str:
    raw = (
        row.get("ticker")
        or row.get("con_code")
        or row.get("ts_code")
        or row.get("code")
        or row.get("symbol")
        or ""
    )
    text = str(raw).strip()
    if not text or text.lower() == "nan":
        return ""
    return ingest.normalize_ticker(text)


def _code_from_ticker(ticker: str) -> str:
    return ticker.replace(".SS", "").replace(".SZ", "")


def load_snapshot_rows(path: Path, source_url: str) -> List[Dict]:
    if path.suffix.lower() == ".jsonl":
        raw_rows = ingest.read_jsonl(path)
    else:
        raw_rows = pd.read_csv(path).to_dict(orient="records")

    snapshots: List[Dict] = []
    for idx, row in enumerate(raw_rows):
        trade_date = _format_date(row.get("trade_date") or row.get("date"))
        ticker = _ticker_from_row(row)
        if not trade_date or not ticker:
            continue
        weight = row.get("weight")
        try:
            weight = float(weight) if weight is not None and str(weight) != "nan" else None
        except (TypeError, ValueError):
            weight = None
        snapshots.append({
            "index_code": str(row.get("index_code") or DEFAULT_INDEX_CODE),
            "con_code": str(row.get("con_code") or row.get("ts_code") or ticker),
            "code": _code_from_ticker(ticker),
            "ticker": ticker,
            "trade_date": trade_date,
            "weight": weight,
            "source_provider": str(row.get("source_provider") or DEFAULT_PROVIDER),
            "source_url": str(row.get("source_url") or source_url),
            "ingested_at": ingest.NOW_UTC,
            "source_row": idx,
        })
    return snapshots


def active_tickers(intervals: List[Dict], as_of_date: str) -> set:
    return {
        row["ticker"] for row in intervals
        if ingest._interval_active(row, as_of_date)
    }


def write_report(
    report_path: Path,
    source_path: Path,
    start: str,
    end: str,
    snapshots: List[Dict],
    intervals: List[Dict],
    rejected_reason: Optional[str],
) -> None:
    dates = sorted({s["trade_date"] for s in snapshots})
    min_date = dates[0] if dates else ""
    max_date = dates[-1] if dates else ""
    status = "BLOCKED" if rejected_reason else "CANDIDATE_WRITTEN"
    target_active = active_tickers(intervals, start) if intervals else set()
    blocker_presence = sorted(H29_BLOCKERS & target_active)

    lines = [
        "# H30 — Real PIT CSI300 Universe Candidate Report",
        "",
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Status:** {status}",
        "",
        "## Source",
        "",
        f"- Source file: `{source_path}`",
        f"- Requested coverage: `{start}` -> `{end}`",
        f"- Snapshot coverage: `{min_date or 'N/A'}` -> `{max_date or 'N/A'}`",
        f"- Snapshot rows: {len(snapshots)}",
        f"- Interval rows: {len(intervals)}",
        "",
        "## H29 Blocker Comparison",
        "",
        f"- Active on `{start}` among H29 blockers: {', '.join(blocker_presence) if blocker_presence else '(none)'}",
        "",
    ]
    if rejected_reason:
        lines.extend([
            "## Rejection",
            "",
            rejected_reason,
            "",
            "No candidate universe files were written.",
            "",
        ])
    else:
        lines.extend([
            "## Candidate Artifacts",
            "",
            "- `data/cn_pit/universe_h30_candidate.jsonl`",
            "- `data/cn_pit/universe_snapshots_h30_candidate.jsonl`",
            "",
            "These files are candidates only. Do not promote them until validation and backtest gates are rerun.",
            "",
        ])
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines))


def build_candidate(args: argparse.Namespace) -> int:
    source_path = Path(args.snapshots)
    if not source_path.is_absolute():
        source_path = PROJECT_ROOT / source_path
    if not source_path.exists():
        raise FileNotFoundError(source_path)

    snapshots = load_snapshot_rows(source_path, args.source_url)
    dates = sorted({s["trade_date"] for s in snapshots})
    rejected_reason = None
    intervals: List[Dict] = []

    if not snapshots:
        rejected_reason = "No valid snapshot rows were found in the source file."
    elif dates[0] > args.start or dates[-1] < args.end:
        rejected_reason = (
            f"Snapshot coverage `{dates[0]}` -> `{dates[-1]}` does not cover "
            f"requested `{args.start}` -> `{args.end}`."
        )
    else:
        intervals = ingest.snapshots_to_intervals(snapshots)

    report_path = REPORTS_DIR / "h30_real_pit_universe_candidate_report.md"
    write_report(report_path, source_path, args.start, args.end, snapshots, intervals, rejected_reason)

    if rejected_reason:
        print(f"BLOCKED: {rejected_reason}")
        print(f"Report: {report_path}")
        return 2

    universe_path = DATA_DIR / "universe_h30_candidate.jsonl"
    snapshots_path = DATA_DIR / "universe_snapshots_h30_candidate.jsonl"
    ingest.write_jsonl(universe_path, intervals)
    ingest.write_jsonl(snapshots_path, snapshots)
    print(f"Wrote {universe_path} ({len(intervals)} rows)")
    print(f"Wrote {snapshots_path} ({len(snapshots)} rows)")
    print(f"Report: {report_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build H30 CSI300 PIT universe candidate from index_weight snapshots"
    )
    parser.add_argument("--snapshots", required=True, help="Tushare/vendor snapshots CSV or JSONL")
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--end", default="2026-05-18")
    parser.add_argument("--source-url", default=DEFAULT_SOURCE_URL)
    return parser


def main() -> int:
    return build_candidate(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
