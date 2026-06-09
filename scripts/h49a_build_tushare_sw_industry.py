#!/usr/bin/env python3
"""H49a — SW L1 Industry Classification Ingestion from Tushare.

Ingest Shenwan L1 industry classification for every ticker in
universe_h30_candidate.jsonl using only Tushare index_classify(L1, SW2021)
+ index_member. Single snapshot, no time-series panel.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data/cn_pit"
DEFAULT_UNIVERSE = DATA_DIR / "universe_h30_candidate.jsonl"
DEFAULT_METADATA_OUT = DATA_DIR / "sector_metadata_sw_l1.csv"
DEFAULT_COVERAGE_OUT = DATA_DIR / "sector_coverage_h49a.json"
DEFAULT_REPORT_OUT = ROOT / "reports/h49a_sw_industry_ingestion_report.md"
PROVIDER_LABEL = "tushare:index_classify+index_member"
LEVEL = "L1"
SRC = "SW2021"


# ---------------------------------------------------------------------------
# Token discovery (same pattern as h47_build_tushare_qfq_prices.py)
# ---------------------------------------------------------------------------
def get_tushare_token() -> str:
    # 1) Env var
    token = os.environ.get("TUSHARE_TOKEN", "").strip()
    if token:
        return token
    # 2) Token file (one-line plaintext)
    token_paths = [
        ROOT / "scripts/.tushare_token",
        Path.home() / ".tushare.token",
    ]
    for tp in token_paths:
        if tp.exists():
            token = tp.read_text(encoding="utf-8").strip()
            if token:
                return token
    # 3) ingest_cn_pit_data fallback
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import ingest_cn_pit_data as ingest  # noqa: WPS433

        tok_fn = getattr(ingest, "_get_tushare_token", None)
        if tok_fn:
            token = (tok_fn() or "").strip()
            if token:
                return token
    except Exception:
        pass
    # 4) tushare built-in
    try:
        import tushare as ts  # noqa: WPS433
        token = (ts.get_token() or "").strip()
        if token:
            return token
    except Exception:
        pass
    tried = ["$TUSHARE_TOKEN"] + [str(tp) for tp in token_paths] + [
        "ingest_cn_pit_data._get_tushare_token()", "tushare.get_token()"
    ]
    raise RuntimeError(
        f"Tushare token missing — tried: {', '.join(tried)}. "
        f"Write token to {token_paths[0]} or export TUSHARE_TOKEN."
    )


# ---------------------------------------------------------------------------
# Ticker conversion
# ---------------------------------------------------------------------------
def yahoo_to_tushare_code(ticker: str) -> str:
    """Convert Yahoo ticker (000001.SZ) to Tushare code (000001.SH/000001.SZ)."""
    if ticker.endswith(".SS"):
        return ticker[:-3] + ".SH"
    if ticker.endswith(".SZ"):
        return ticker
    raise ValueError(f"unsupported ticker suffix: {ticker}")


# ---------------------------------------------------------------------------
# Universe loading
# ---------------------------------------------------------------------------
def load_universe_tickers(path: Path) -> List[str]:
    """Load unique tickers from universe JSONL, preserving original Yahoo format."""
    tickers: Dict[str, None] = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            ticker = str(row.get("ticker", ""))
            if ticker:
                tickers[ticker] = None
    return sorted(tickers)


def load_universe_with_weights(path: Path) -> Dict[str, float]:
    """Load ticker → weight mapping (latest effective_date per ticker wins)."""
    ticker_weight: Dict[str, Tuple[str, float]] = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            ticker = str(row.get("ticker", ""))
            weight = float(row.get("weight", 0))
            eff = str(row.get("effective_date", ""))
            if not ticker:
                continue
            if ticker not in ticker_weight or eff > ticker_weight[ticker][0]:
                ticker_weight[ticker] = (eff, weight)
    return {t: w for t, (_, w) in ticker_weight.items()}


# ---------------------------------------------------------------------------
# Tushare API calls
# ---------------------------------------------------------------------------
def fetch_sw_l1_codes(pro_api) -> List[Dict]:
    """Fetch SW L1 index classification codes from Tushare.

    Returns list of dicts with keys: index_code, industry_name, level, src, etc.
    """
    df = pro_api.index_classify(level=LEVEL, src=SRC)
    if df is None or df.empty:
        raise RuntimeError("Tushare index_classify returned no data for level=L1, src=SW2021")
    return df.to_dict(orient="records")


def fetch_index_members(pro_api, index_code: str) -> List[Dict]:
    """Fetch constituent tickers for a given SW L1 index code.

    Returns list of dicts with keys: index_code, con_code, in_date, out_date, etc.
    """
    df = pro_api.index_member(index_code=index_code)
    if df is None or df.empty:
        return []
    return df.to_dict(orient="records")


# ---------------------------------------------------------------------------
# Mapping logic
# ---------------------------------------------------------------------------
@dataclass
class MappingResult:
    ticker: str
    ts_code: str
    industry_code: Optional[str] = None
    industry_name: Optional[str] = None
    status: str = "unmapped"  # "mapped", "unmapped", "multi_mapped"
    reason: Optional[str] = None
    alternates: List[Dict] = field(default_factory=list)


def build_industry_mapping(
    universe_tickers: List[str],
    l1_codes: List[Dict],
    pro_api,
    snapshot_date: str,
) -> Tuple[List[MappingResult], Dict]:
    """Map each universe ticker to exactly one SW L1 industry.

    Returns (results, coverage_info).
    """
    # Step 1: Build a lookup: ts_code → [(industry_code, industry_name, in_date, out_date)]
    ts_to_industries: Dict[str, List[Dict]] = {}
    industry_names: Dict[str, str] = {}

    for code_info in l1_codes:
        idx_code = str(code_info["index_code"])
        idx_name = str(code_info.get("industry_name", ""))
        industry_names[idx_code] = idx_name

        print(f"  Fetching members for {idx_code} ({idx_name})...", end=" ", flush=True)
        try:
            members = fetch_index_members(pro_api, idx_code)
            print(f"{len(members)} members")
        except Exception as exc:
            print(f"ERROR: {exc}")
            continue

        for member in members:
            con_code = str(member.get("con_code", ""))
            if not con_code:
                continue
            ts_to_industries.setdefault(con_code, []).append({
                "industry_code": idx_code,
                "industry_name": idx_name,
                "in_date": str(member.get("in_date", "")),
                "out_date": str(member.get("out_date", "")),
            })
        time.sleep(0.3)  # rate-limit courtesy

    # Step 2: Map each universe ticker
    results: List[MappingResult] = []
    multi_mapped_log: List[Dict] = []
    unmapped_details: List[Dict] = []
    industry_histogram: Dict[str, int] = {}

    for ticker in universe_tickers:
        ts_code = yahoo_to_tushare_code(ticker)
        candidates = ts_to_industries.get(ts_code, [])

        result = MappingResult(ticker=ticker, ts_code=ts_code)

        if not candidates:
            result.status = "unmapped"
            result.reason = "not found in any SW L1 index_member set"
            unmapped_details.append({
                "ticker": ticker,
                "ts_code": ts_code,
                "reason": result.reason,
            })
            results.append(result)
            continue

        if len(candidates) == 1:
            c = candidates[0]
            result.industry_code = c["industry_code"]
            result.industry_name = c["industry_name"]
            result.status = "mapped"
            industry_histogram[result.industry_code] = (
                industry_histogram.get(result.industry_code, 0) + 1
            )
            results.append(result)
            continue

        # Multi-mapped: prefer the one active at snapshot_date
        # If multiple active, prefer latest in_date
        active = [c for c in candidates
                  if c["in_date"] <= snapshot_date
                  and (not c["out_date"] or c["out_date"] > snapshot_date)]

        if active:
            # Prefer latest in_date among active
            best = sorted(active, key=lambda c: c["in_date"], reverse=True)[0]
            result.industry_code = best["industry_code"]
            result.industry_name = best["industry_name"]
            result.status = "multi_mapped"
            result.alternates = [c for c in candidates if c != best]
        else:
            # None active: pick the one with latest in_date (most recent addition)
            best = sorted(candidates, key=lambda c: c["in_date"], reverse=True)[0]
            result.industry_code = best["industry_code"]
            result.industry_name = best["industry_name"]
            result.status = "multi_mapped"
            result.alternates = [c for c in candidates if c != best]

        multi_mapped_log.append({
            "ticker": ticker,
            "ts_code": ts_code,
            "selected": {
                "industry_code": result.industry_code,
                "industry_name": result.industry_name,
                "in_date": best["in_date"],
                "out_date": best.get("out_date", ""),
            },
            "alternates": [
                {
                    "industry_code": c["industry_code"],
                    "industry_name": c["industry_name"],
                    "in_date": c["in_date"],
                    "out_date": c.get("out_date", ""),
                }
                for c in candidates if c != best
            ],
        })

        industry_histogram[result.industry_code] = (
            industry_histogram.get(result.industry_code, 0) + 1
        )
        results.append(result)

    # ── Coverage info ──
    mapped_count = sum(1 for r in results if r.status in ("mapped", "multi_mapped"))
    coverage = {
        "provenance": {
            "provider": PROVIDER_LABEL,
            "level": LEVEL,
            "src": SRC,
            "snapshot_date": snapshot_date,
        },
        "universe_ticker_count": len(universe_tickers),
        "mapped_count": mapped_count,
        "unmapped_count": len(unmapped_details),
        "multi_mapped_count": len(multi_mapped_log),
        "coverage_pct": round(mapped_count / len(universe_tickers) * 100, 2) if universe_tickers else 0,
        "industry_histogram": {
            code: {"name": industry_names.get(code, code), "count": count}
            for code, count in sorted(
                industry_histogram.items(), key=lambda x: -x[1]
            )
        },
        "unmapped_tickers": unmapped_details,
        "multi_mapped": multi_mapped_log,
    }
    return results, coverage


# ---------------------------------------------------------------------------
# CSV / JSON / Report writers
# ---------------------------------------------------------------------------
def write_metadata_csv(results: List[MappingResult], snapshot_date: str, ingested_at: str, path: Path) -> None:
    """Write sector_metadata_sw_l1.csv."""
    lines = ["ticker,industry_code,industry_name,source_provider,snapshot_date,ingested_at"]
    for r in sorted(results, key=lambda x: x.ticker):
        lines.append(
            f"{r.ticker},{r.industry_code or ''},{r.industry_name or ''},"
            f"{PROVIDER_LABEL},{snapshot_date},{ingested_at}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_coverage_json(coverage: Dict, path: Path) -> None:
    """Write sector_coverage_h49a.json."""
    path.write_text(json.dumps(coverage, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_report(
    coverage: Dict,
    results: List[MappingResult],
    weights: Dict[str, float],
    path: Path,
) -> None:
    """Write h49a_sw_industry_ingestion_report.md."""
    prov = coverage["provenance"]
    mapped = coverage["mapped_count"]
    unmapped = coverage["unmapped_count"]
    multi = coverage["multi_mapped_count"]
    universe_n = coverage["universe_ticker_count"]
    cov_pct = coverage["coverage_pct"]
    gate_ok = cov_pct >= 98.0

    # Top 10 industries by universe weight
    industry_weight: Dict[str, float] = {}
    for r in results:
        if r.status in ("mapped", "multi_mapped") and r.industry_code:
            w = weights.get(r.ticker, 0)
            industry_weight[r.industry_code] = industry_weight.get(r.industry_code, 0) + w
    top_by_weight = sorted(industry_weight.items(), key=lambda x: -x[1])[:10]

    lines = [
        "# H49a — SW L1 Industry Classification Ingestion Report",
        "",
        "## Objective",
        "",
        "Ingest Shenwan L1 industry classification from Tushare for every ticker in "
        "`universe_h30_candidate.jsonl`. This is a data slice, not a strategy promotion.",
        "",
        "## Provenance",
        "",
        f"- **Provider:** {prov['provider']}",
        f"- **Level:** {prov['level']}",
        f"- **Source:** {prov['src']}",
        f"- **Snapshot date:** {prov['snapshot_date']}",
        f"- **Source URL:** https://tushare.pro/document/2?doc_id=180 (index_classify), "
        "https://tushare.pro/document/2?doc_id=181 (index_member)",
        "",
        "## Coverage Summary",
        "",
        f"- Universe tickers: {universe_n}",
        f"- Mapped (single): {mapped - multi}",
        f"- Multi-mapped: {multi}",
        f"- Unmapped: {unmapped}",
        f"- Coverage: {cov_pct}%",
        f"- Gate (≥98%): {'PASS ✅' if gate_ok else 'FAIL ❌'}",
        "",
        "## Industry Histogram",
        "",
        "| Code | Name | Count | % of Universe |",
        "|------|------|-------|---------------|",
    ]

    for code, info in coverage["industry_histogram"].items():
        pct = round(info["count"] / universe_n * 100, 1)
        lines.append(f"| {code} | {info['name']} | {info['count']} | {pct}% |")

    lines += [
        "",
        "## Top 10 Industries by H30 Weight",
        "",
        "| Code | Name | Weight Sum | % of Total Weight |",
        "|------|------|------------|-------------------|",
    ]
    total_weight = sum(w for _, w in top_by_weight)
    for code, w in top_by_weight:
        name = coverage["industry_histogram"].get(code, {}).get("name", code)
        pct = round(w / total_weight * 100, 1) if total_weight else 0
        lines.append(f"| {code} | {name} | {w:.4f} | {pct}% |")

    lines += [
        "",
        "## Unmapped Tickers",
        "",
    ]
    if coverage["unmapped_tickers"]:
        lines.append("| Ticker | Tushare Code | Reason |")
        lines.append("|--------|-------------|--------|")
        for u in coverage["unmapped_tickers"]:
            lines.append(f"| {u['ticker']} | {u['ts_code']} | {u['reason']} |")
    else:
        lines.append("None — all universe tickers mapped.")

    lines += [
        "",
        "## Multi-Mapped Tickers",
        "",
    ]
    if coverage["multi_mapped"]:
        lines.append("| Ticker | Selected Industry | Alternates |")
        lines.append("|--------|-------------------|-------------|")
        for m in coverage["multi_mapped"]:
            alt_str = ", ".join(
                f"{a['industry_code']}({a['industry_name']})" for a in m["alternates"]
            )
            lines.append(
                f"| {m['ticker']} | {m['selected']['industry_code']} "
                f"({m['selected']['industry_name']}) | {alt_str} |"
            )
    else:
        lines.append("None — no multi-mapped tickers.")

    verdict = "CANDIDATE_DATASET" if gate_ok else "RESEARCH_ONLY"
    lines += [
        "",
        "## Verdict",
        "",
        f"**{verdict}**",
        "",
    ]
    if not gate_ok:
        lines.append(
            f"Coverage {cov_pct}% is below the 98% gate. "
            f"{unmapped} tickers could not be mapped."
        )
        lines.append("")

    lines.append("## Note")
    lines.append("")
    lines.append("H49a is a data slice, not a strategy promotion. "
                 "H49b (sector-neutral RS search) is blocked on this output.")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Smoke validation (no network)
# ---------------------------------------------------------------------------
def run_smoke_validation(metadata_path: Path, coverage_path: Path, report_path: Path) -> int:
    """Load artifacts and validate shape without network calls."""
    errors = 0

    # Check CSV
    if not metadata_path.exists():
        print(f"FAIL: {metadata_path} missing")
        return 1
    csv_lines = metadata_path.read_text(encoding="utf-8").strip().splitlines()
    if len(csv_lines) < 2:
        print(f"FAIL: {metadata_path} has no data rows")
        errors += 1
    header = csv_lines[0]
    for col in ["ticker", "industry_code", "industry_name", "source_provider", "snapshot_date", "ingested_at"]:
        if col not in header:
            print(f"FAIL: column {col} missing from CSV")
            errors += 1

    # Check JSON
    if not coverage_path.exists():
        print(f"FAIL: {coverage_path} missing")
        errors += 1
    else:
        cov = json.loads(coverage_path.read_text(encoding="utf-8"))
        prov = cov.get("provenance", {})
        if prov.get("provider") != PROVIDER_LABEL:
            print(f"FAIL: wrong provider {prov.get('provider')}")
            errors += 1
        if "snapshot_date" not in prov:
            print("FAIL: snapshot_date missing from provenance")
            errors += 1
        if "mapped_count" not in cov:
            print("FAIL: mapped_count missing")
            errors += 1

    # Check Report
    if not report_path.exists():
        print(f"FAIL: {report_path} missing")
        errors += 1
    else:
        report = report_path.read_text(encoding="utf-8")
        for required in ["Provenance", "Coverage Summary", "Industry Histogram", "Verdict"]:
            if required not in report:
                print(f"FAIL: '{required}' section missing from report")
                errors += 1

    if errors:
        print(f"\n{errors} validation error(s)")
        return 1
    print("Smoke validation PASSED")
    return 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="H49a — SW L1 Industry Classification Ingestion"
    )
    parser.add_argument(
        "--universe",
        type=Path,
        default=DEFAULT_UNIVERSE,
        help="Path to universe JSONL",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limit to first N tickers (0 = all)",
    )
    parser.add_argument(
        "--raw-dir",
        type=Path,
        help="Cache raw Tushare responses (smoke mode)",
    )
    parser.add_argument(
        "--output-metadata",
        type=Path,
        default=DEFAULT_METADATA_OUT,
        help="Path for sector metadata CSV",
    )
    parser.add_argument(
        "--output-coverage",
        type=Path,
        default=DEFAULT_COVERAGE_OUT,
        help="Path for coverage JSON",
    )
    parser.add_argument(
        "--output-report",
        type=Path,
        default=DEFAULT_REPORT_OUT,
        help="Path for Markdown report",
    )
    parser.add_argument(
        "--smoke-only",
        action="store_true",
        help="Run smoke validation on existing artifacts only (no API calls)",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Run smoke validation after writing (implied by --smoke-only)",
    )
    args = parser.parse_args(argv)

    if args.smoke_only:
        return run_smoke_validation(
            args.output_metadata, args.output_coverage, args.output_report
        )

    # ── Setup ──
    token = get_tushare_token()
    import tushare as ts  # noqa: WPS433
    pro_api = ts.pro_api(token)

    today = date.today().isoformat()
    ingested_at = datetime.now(timezone.utc).isoformat()

    # ── Load universe ──
    all_tickers = load_universe_tickers(args.universe)
    tickers = all_tickers[:args.limit] if args.limit else all_tickers
    weights = load_universe_with_weights(args.universe)
    print(f"Universe: {len(all_tickers)} unique tickers"
          + (f", processing {len(tickers)}" if args.limit else ""))

    # ── Fetch SW L1 codes ──
    print("Fetching SW L1 index classification codes...")
    l1_codes = fetch_sw_l1_codes(pro_api)
    print(f"  Got {len(l1_codes)} L1 codes")

    # ── Build mapping ──
    print("Building ticker → industry mapping...")
    results, coverage = build_industry_mapping(tickers, l1_codes, pro_api, today)

    # ── Write outputs ──
    args.output_metadata.parent.mkdir(parents=True, exist_ok=True)
    args.output_coverage.parent.mkdir(parents=True, exist_ok=True)
    args.output_report.parent.mkdir(parents=True, exist_ok=True)

    write_metadata_csv(results, today, ingested_at, args.output_metadata)
    print(f"  Wrote {args.output_metadata}")

    write_coverage_json(coverage, args.output_coverage)
    print(f"  Wrote {args.output_coverage}")

    write_report(coverage, results, weights, args.output_report)
    print(f"  Wrote {args.output_report}")

    # ── Summary ──
    print(f"\nDone. Mapped: {coverage['mapped_count']}, "
          f"Unmapped: {coverage['unmapped_count']}, "
          f"Multi-mapped: {coverage['multi_mapped_count']}, "
          f"Coverage: {coverage['coverage_pct']}%")

    if args.validate:
        return run_smoke_validation(
            args.output_metadata, args.output_coverage, args.output_report
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
