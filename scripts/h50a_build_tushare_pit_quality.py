#!/usr/bin/env python3
"""H50a V2 — PIT Quality Metrics Ingestion from Tushare (4 endpoints).

V1 (2026-05-23) used fina_indicator only; BLOCKED because accruals_ratio = 0%
(no raw line items). V2 adds income / cashflow / balancesheet endpoints,
dedup-then-join pipeline (preventing Cartesian explosion from 会计差错更正),
gross_margin fallback derivation, and ROE year-end-only overlap check.

Still additive — does not touch data/cn_pit/fundamentals.jsonl (H28 baseline).
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data/cn_pit"
RAW_BASE = DATA_DIR / "raw"
ANOMALIES_PATH = DATA_DIR / "raw/h50a_anomalies.jsonl"
DEFAULT_UNIVERSE = DATA_DIR / "universe_h30_candidate.jsonl"
DEFAULT_FUNDAMENTALS = DATA_DIR / "fundamentals.jsonl"
DEFAULT_OUTPUT_JSONL = DATA_DIR / "fundamentals_h50a_pit_quality.jsonl"
DEFAULT_COVERAGE_OUT = DATA_DIR / "fundamentals_coverage_h50a.json"
DEFAULT_REPORT_OUT = ROOT / "reports/h50a_pit_quality_ingestion_report.md"
PROVIDER_LABEL = "tushare:fina_indicator+income+cashflow+balancesheet"
SOURCE_URL_FINA = "https://tushare.pro/document/2?doc_id=79"
SOURCE_URL_INCOME = "https://tushare.pro/document/2?doc_id=33"
SOURCE_URL_CASHFLOW = "https://tushare.pro/document/2?doc_id=34"
SOURCE_URL_BALANCESHEET = "https://tushare.pro/document/2?doc_id=36"
DEFAULT_START = "20191001"
DEFAULT_END = "20260331"

# ── Endpoint config ─────────────────────────────────────────────────────
ENDPOINT_CONFIGS = {
    "fina_indicator": {
        "url": SOURCE_URL_FINA,
        "cache_subdir": "h50a_tushare_fina_indicator",
        "fetch_fn": "fina_indicator",
    },
    "income": {
        "url": SOURCE_URL_INCOME,
        "cache_subdir": "h50a_tushare_income",
        "fetch_fn": "income",
    },
    "cashflow": {
        "url": SOURCE_URL_CASHFLOW,
        "cache_subdir": "h50a_tushare_cashflow",
        "fetch_fn": "cashflow",
    },
    "balancesheet": {
        "url": SOURCE_URL_BALANCESHEET,
        "cache_subdir": "h50a_tushare_balancesheet",
        "fetch_fn": "balancesheet",
    },
}
ENDPOINT_ORDER = ["fina_indicator", "income", "cashflow", "balancesheet"]

# ── Tushare → Output field mapping ─────────────────────────────────────
FIELD_MAP = {
    "roe": ("fina_indicator", "roe_waa"),
    "roa": ("fina_indicator", "roa"),
    "gross_margin": ("fina_indicator", "grossprofit_margin"),  # primary; fallback derived
    "operating_margin": ("fina_indicator", "op_of_gr"),        # primary; fallback derived
    "current_ratio": ("fina_indicator", "current_ratio"),
    "quick_ratio": ("fina_indicator", "quick_ratio"),
    "debt_to_equity": ("fina_indicator", "debt_to_eqt"),
    "operating_cash_flow_to_revenue": ("fina_indicator", "ocf_to_or"),
    "free_cash_flow": ("fina_indicator", "fcff"),
    "accruals_ratio": None,  # derived from intermediates
}

# Intermediate audit-only fields: (endpoint, tushare_key) pairs
INTERMEDIATE_MAP: Dict[str, Tuple[str, str]] = {
    "_net_income": ("income", "n_income"),
    "_net_cashflow_op": ("cashflow", "n_cashflow_act"),
    "_total_assets": ("balancesheet", "total_assets"),
    "_op_income": ("fina_indicator", "op_income"),  # primary
    "_total_revenue": ("income", "total_revenue"),
    "_total_cogs": ("income", "total_cogs"),
}

# Which score fields are "hard" (balance-sheet/profitability) vs "soft" (cash-flow)
HARD_FIELDS = ["roe", "roa", "gross_margin", "current_ratio", "debt_to_equity"]
SOFT_FIELDS = ["operating_cash_flow_to_revenue", "free_cash_flow", "accruals_ratio"]

ALL_SCORE_FIELDS = list(FIELD_MAP.keys())
ALL_INTERMEDIATE = list(INTERMEDIATE_MAP.keys())
ALL_OUTPUT_FIELDS = ALL_SCORE_FIELDS + ALL_INTERMEDIATE


# ═══════════════════════════════════════════════════════════════════════════
# Token discovery (same pattern as h49a_build_tushare_sw_industry.py)
# ═══════════════════════════════════════════════════════════════════════════
def get_tushare_token() -> str:
    token = os.environ.get("TUSHARE_TOKEN", "").strip()
    if token:
        return token
    token_paths = [
        ROOT / "scripts/.tushare_token",
        Path.home() / ".tushare.token",
    ]
    for tp in token_paths:
        if tp.exists():
            token = tp.read_text(encoding="utf-8").strip()
            if token:
                return token
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import ingest_cn_pit_data as ingest
        tok_fn = getattr(ingest, "_get_tushare_token", None)
        if tok_fn:
            token = (tok_fn() or "").strip()
            if token:
                return token
    except Exception:
        pass
    try:
        import tushare as ts
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


# ═══════════════════════════════════════════════════════════════════════════
# Ticker conversion
# ═══════════════════════════════════════════════════════════════════════════
def yahoo_to_tushare_code(ticker: str) -> str:
    """Convert Yahoo ticker (000001.SZ) → Tushare code (000001.SZ/000001.SH)."""
    if ticker.endswith(".SS"):
        return ticker[:-3] + ".SH"
    if ticker.endswith(".SZ"):
        return ticker
    raise ValueError(f"unsupported ticker suffix: {ticker}")


# ═══════════════════════════════════════════════════════════════════════════
# Universe loading
# ═══════════════════════════════════════════════════════════════════════════
def load_universe_tickers(path: Path) -> List[str]:
    """Load unique tickers from universe JSONL in sorted order."""
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


# ═══════════════════════════════════════════════════════════════════════════
# Raw per-ticker per-endpoint cache (resumable)
# ═══════════════════════════════════════════════════════════════════════════
def _cache_path(ticker: str, raw_dir: Path) -> Path:
    ts_code = yahoo_to_tushare_code(ticker)
    return raw_dir / f"{ts_code}.csv"


def _endpoint_cache_dir(base_dir: Path, endpoint: str) -> Path:
    return base_dir / ENDPOINT_CONFIGS[endpoint]["cache_subdir"]


def read_endpoint_cache(
    ticker: str, endpoint: str, raw_base_dir: Path, start: str, end: str
) -> Optional[List[Dict]]:
    """Read cached Tushare records for one endpoint. Returns None if incomplete."""
    cache_dir = _endpoint_cache_dir(raw_base_dir, endpoint)
    cp = _cache_path(ticker, cache_dir)
    if not cp.exists():
        return None
    try:
        import pandas as pd
        df = pd.read_csv(cp)
        if df.empty:
            return None
        if "end_date" not in df.columns:
            return None
        periods = sorted(df["end_date"].dropna().unique())
        if not periods:
            return None
        cache_min = str(periods[0]).replace("-", "")
        cache_max = str(periods[-1]).replace("-", "")
        if cache_min <= start and cache_max >= end:
            return df.to_dict(orient="records")
    except Exception:
        pass
    return None


def write_endpoint_cache(
    ticker: str, endpoint: str, raw_base_dir: Path, records: List[Dict]
) -> None:
    """Write raw Tushare records to per-ticker per-endpoint CSV cache."""
    if not records:
        return
    import pandas as pd
    cache_dir = _endpoint_cache_dir(raw_base_dir, endpoint)
    cp = _cache_path(ticker, cache_dir)
    cp.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(records)
    df.to_csv(cp, index=False)


# Backward-compat aliases for V1 cache reading
def read_cache(ticker: str, raw_dir: Path, start: str, end: str) -> Optional[List[Dict]]:
    return read_endpoint_cache(ticker, "fina_indicator", raw_dir, start, end)


def write_cache(ticker: str, raw_dir: Path, records: List[Dict]) -> None:
    write_endpoint_cache(ticker, "fina_indicator", raw_dir, records)


# ═══════════════════════════════════════════════════════════════════════════
# Tushare API with rate limiting
# ═══════════════════════════════════════════════════════════════════════════
@dataclass
class RateLimiter:
    """Hard-cap call rate at 5 calls/sec (single counter across all endpoints)."""
    min_interval: float = 0.2  # 5 calls/sec
    _last_call: float = 0.0

    def wait(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_call = time.monotonic()


def _all_fina_indicator_fields() -> List[str]:
    """Fields needed from fina_indicator: identity + mapped + op_income."""
    identity = ["ts_code", "end_date", "ann_date", "update_flag"]
    mapped = [v[1] for v in FIELD_MAP.values() if v is not None and v[0] == "fina_indicator"]
    intermediate = [
        INTERMEDIATE_MAP[k][1]
        for k in ALL_INTERMEDIATE
        if INTERMEDIATE_MAP[k][0] == "fina_indicator"
    ]
    # dedup: keep unique
    seen = set(identity)
    result = list(identity)
    for f in mapped + intermediate:
        if f not in seen:
            seen.add(f)
            result.append(f)
    return result


def _all_income_fields() -> List[str]:
    identity = ["ts_code", "end_date", "ann_date", "update_flag"]
    mapped = [v[1] for v in FIELD_MAP.values() if v is not None and v[0] == "income"]
    intermediate = [
        INTERMEDIATE_MAP[k][1]
        for k in ALL_INTERMEDIATE
        if INTERMEDIATE_MAP[k][0] == "income"
    ]
    seen = set(identity)
    result = list(identity)
    for f in mapped + intermediate:
        if f not in seen:
            seen.add(f)
            result.append(f)
    # Also fetch operate_profit for _op_income fallback
    if "operate_profit" not in seen:
        result.append("operate_profit")
    return result


def _all_cashflow_fields() -> List[str]:
    identity = ["ts_code", "end_date", "ann_date", "update_flag"]
    intermediate = [
        INTERMEDIATE_MAP[k][1]
        for k in ALL_INTERMEDIATE
        if INTERMEDIATE_MAP[k][0] == "cashflow"
    ]
    return identity + intermediate


def _all_balancesheet_fields() -> List[str]:
    identity = ["ts_code", "end_date", "ann_date", "update_flag"]
    intermediate = [
        INTERMEDIATE_MAP[k][1]
        for k in ALL_INTERMEDIATE
        if INTERMEDIATE_MAP[k][0] == "balancesheet"
    ]
    return identity + intermediate


ENDPOINT_FIELDS = {
    "fina_indicator": _all_fina_indicator_fields,
    "income": _all_income_fields,
    "cashflow": _all_cashflow_fields,
    "balancesheet": _all_balancesheet_fields,
}


def fetch_endpoint(
    pro_api,
    endpoint: str,
    ts_code: str,
    start: str,
    end: str,
    rate_limiter: RateLimiter,
    max_retries: int = 5,
) -> Tuple[Optional[List[Dict]], Optional[str]]:
    """Fetch one Tushare endpoint for one ticker with exponential backoff + jitter.

    Returns (records, error_reason). records is None if fetch failed irrevocably.
    Returns [] if the endpoint returned empty (no data for this ticker/period).
    """
    for attempt in range(max_retries):
        rate_limiter.wait()
        try:
            fetch_fn_name = ENDPOINT_CONFIGS[endpoint]["fetch_fn"]
            fields_fn = ENDPOINT_FIELDS[endpoint]
            fetch_fn = getattr(pro_api, fetch_fn_name)
            df = fetch_fn(
                ts_code=ts_code,
                start_date=start,
                end_date=end,
                fields=",".join(fields_fn()),
            )
            if df is None or df.empty:
                return [], None
            return df.to_dict(orient="records"), None
        except Exception as exc:
            msg = str(exc)
            is_rate_limit = (
                "429" in msg
                or "rate" in msg.lower()
                or "limit" in msg.lower()
                or "too many" in msg.lower()
                or "频繁" in msg
            )
            if not is_rate_limit:
                return None, f"fetch error (non-rate-limit): {msg[:200]}"

            if attempt < max_retries - 1:
                base = min(2 ** (attempt + 1), 60)
                jitter = random.uniform(0, base * 0.5)
                sleep_s = base + jitter
                print(f"  ⏳ Rate-limited {endpoint} for {ts_code}, retry {attempt+1}/{max_retries} "
                      f"after {sleep_s:.1f}s...", flush=True)
                time.sleep(sleep_s)
            else:
                return None, f"rate-limit exhausted after {max_retries} retries: {msg[:200]}"

    return None, "unknown fetch error"


# ═══════════════════════════════════════════════════════════════════════════
# Dedup-then-join pipeline (V2 — CRITICAL for preventing Cartesian explosion)
# ═══════════════════════════════════════════════════════════════════════════
def dedup_endpoint_df(records: List[Dict], endpoint_name: str) -> "pd.DataFrame":
    """Dedup one endpoint's records: sort + drop_duplicates + assert.

    Steps:
    1. Convert to DataFrame
    2. Sort by (ts_code, end_date, ann_date ASC, update_flag ASC)
    3. drop_duplicates(subset=['ts_code', 'end_date'], keep='last')
    4. Assert: df.groupby(['ts_code', 'end_date']).size().max() == 1

    Returns empty DataFrame if records is empty.
    """
    import pandas as pd
    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)

    # Ensure required columns exist
    for col in ["ts_code", "end_date"]:
        if col not in df.columns:
            raise ValueError(
                f"endpoint {endpoint_name}: missing required column '{col}'"
            )

    # Normalize update_flag for sorting (handle str/int/None)
    if "ann_date" not in df.columns:
        df["ann_date"] = df["end_date"]  # fallback
    if "update_flag" not in df.columns:
        df["update_flag"] = "1"  # default

    df["update_flag_str"] = df["update_flag"].fillna("1").astype(str)

    # Sort: ts_code ASC, end_date ASC, ann_date ASC, update_flag ASC
    df = df.sort_values(
        ["ts_code", "end_date", "ann_date", "update_flag_str"],
        ascending=[True, True, True, True],
    ).reset_index(drop=True)

    df = df.drop(columns=["update_flag_str"])

    # drop_duplicates with keep='last' — keeps the latest filing per (ticker, quarter)
    before = len(df)
    df = df.drop_duplicates(subset=["ts_code", "end_date"], keep="last").reset_index(
        drop=True
    )
    after = len(df)
    if before != after:
        print(f"  📎 {endpoint_name}: dedup removed {before - after} duplicate rows")

    # Assert no remaining duplicates
    dup_check = df.groupby(["ts_code", "end_date"]).size()
    if dup_check.max() > 1:
        offenders = dup_check[dup_check > 1].head(5).to_dict()
        raise ValueError(
            f"FATAL: {endpoint_name} still has duplicate (ts_code, end_date) rows after dedup. "
            f"Offending tuples: {offenders}"
        )

    return df


def join_four_endpoints(
    fina_df: "pd.DataFrame",
    income_df: "pd.DataFrame",
    cashflow_df: "pd.DataFrame",
    balancesheet_df: "pd.DataFrame",
) -> Tuple["pd.DataFrame", int]:
    """Dedup each endpoint's DataFrame, then LEFT JOIN on (ts_code, end_date).

    Driver: fina_indicator (its row set defines output row count).

    Returns (joined_df, ann_date_skew_count).
    Raises ValueError on dedup or join assertion failure.
    """
    import pandas as pd

    # Step 1-3: Dedup each endpoint
    dfs = {}
    for name, df in [
        ("fina_indicator", fina_df),
        ("income", income_df),
        ("cashflow", cashflow_df),
        ("balancesheet", balancesheet_df),
    ]:
        deduped = dedup_endpoint_df(df.to_dict(orient="records") if not df.empty else [], name)
        dfs[name] = deduped

    fina = dfs["fina_indicator"]
    if fina.empty:
        return fina, 0

    fina_len = len(fina)

    # Step 4: LEFT JOIN — driver is fina_indicator
    # Add endpoint suffix to avoid column collisions (except join keys)
    join_keys = ["ts_code", "end_date"]

    def _add_suffix(df: pd.DataFrame, suffix: str) -> pd.DataFrame:
        """Suffix all columns except join keys."""
        rename = {
            col: f"{col}_{suffix}"
            for col in df.columns
            if col not in join_keys
        }
        return df.rename(columns=rename)

    income_s = _add_suffix(dfs["income"], "income") if not dfs["income"].empty else None
    cashflow_s = _add_suffix(dfs["cashflow"], "cashflow") if not dfs["cashflow"].empty else None
    bs_s = _add_suffix(dfs["balancesheet"], "balancesheet") if not dfs["balancesheet"].empty else None

    joined = fina.copy()
    for suffix_df, label in [(income_s, "income"), (cashflow_s, "cashflow"), (bs_s, "balancesheet")]:
        if suffix_df is not None and not suffix_df.empty:
            joined = joined.merge(suffix_df, on=join_keys, how="left", suffixes=("", f"_{label}"))
        else:
            # No data from this endpoint — still need placeholder NULL columns for the join
            # Just leave them missing; build_row handles NULLs
            pass

    # Step 5: Assert row count
    if len(joined) != fina_len:
        raise ValueError(
            f"FATAL: join produced {len(joined)} rows, expected {fina_len} "
            f"(fina_indicator row count). Dedup assertion may have failed."
        )

    # ann_date reconciliation
    # Compute filing_date = MAX(ann_date) across endpoints
    skew_count = 0
    ann_cols = []
    if "ann_date" in joined.columns:
        ann_cols.append("ann_date")
    for suffix in ["income", "cashflow", "balancesheet"]:
        col = f"ann_date_{suffix}"
        if col in joined.columns:
            ann_cols.append(col)

    if len(ann_cols) >= 2:
        # Compute max ann_date
        joined["filing_date_ann"] = joined[ann_cols[0]]
        for col in ann_cols[1:]:
            # Coerce to comparable strings, take max
            mask = joined[col].notna()
            if mask.any():
                # Compare as strings (YYYYMMDD)
                for idx in joined.index:
                    vals = []
                    for ac in ann_cols:
                        v = joined.at[idx, ac]
                        if pd.notna(v):
                            vals.append(str(v))
                    if vals:
                        joined.at[idx, "filing_date_ann"] = max(vals)

        # Count skews > 7 days
        for col in ann_cols[1:]:
            # Count where the secondary ann_date differs from primary by more than 7 days
            try:
                primary = pd.to_datetime(joined[ann_cols[0]], errors="coerce")
                secondary = pd.to_datetime(joined[col], errors="coerce")
                mask = (primary.notna()) & (secondary.notna())
                if mask.any():
                    delta = (primary - secondary).abs().dt.days
                    skew_count += int((delta > 7).sum())
            except Exception:
                pass
    else:
        # Only one ann_date column — use it directly
        joined["filing_date_ann"] = joined.get("ann_date", joined.get("end_date", None))

    return joined, skew_count


# ═══════════════════════════════════════════════════════════════════════════
# Data quality helpers
# ═══════════════════════════════════════════════════════════════════════════
def nan_to_none(value: Any) -> Any:
    """Convert numpy.nan / pandas.NA → None for JSON compliance."""
    if value is None:
        return None
    try:
        if isinstance(value, float) and np.isnan(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        import pandas as pd
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def safe_float(value: Any) -> Optional[float]:
    """Convert to float or return None."""
    v = nan_to_none(value)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def safe_str(value: Any) -> Optional[str]:
    """Convert to stripped string or return None."""
    v = nan_to_none(value)
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


# ═══════════════════════════════════════════════════════════════════════════
# Row builder (V2: uses joined data from 4 endpoints)
# ═══════════════════════════════════════════════════════════════════════════
def build_row_v2(
    ticker: str,
    joined_row: Dict,
    ann_date_skew: Optional[Dict[str, str]],
    ingested_at: str,
    anomalies: List[Dict],
    allow_future_filing: bool,
) -> Tuple[Optional[Dict], bool]:
    """Build one output JSONL row from a joined 4-endpoint row.

    joined_row: a dict from the joined DataFrame (one row).
    ann_date_skew: if present, per-endpoint ann_date tuples for data_quality_note.

    Returns (row_dict, is_anomalous). Row is None if hard-fail (identity issues).
    """
    notes: List[str] = []
    ts_code = yahoo_to_tushare_code(ticker)
    code = ts_code

    # ── Identity fields ──
    end_date = safe_str(joined_row.get("end_date"))
    ann_date = safe_str(joined_row.get("ann_date"))

    if not end_date or not ticker or not code:
        raise ValueError(
            f"NULL identity field: ticker={ticker!r}, "
            f"code={code!r}, end_date={end_date!r}"
        )

    # filing_date from joined data (MAX of all ann_dates)
    filing_date = ann_date
    if "filing_date_ann" in joined_row:
        fd_val = joined_row.get("filing_date_ann")
        if pd_is_notna(fd_val):
            filing_date = safe_str(fd_val)

    if not filing_date:
        # filing hasn't happened yet for this period — skip row, don't abort
        return None, False

    report_period = end_date
    # Normalize to YYYY-MM-DD
    if len(report_period) == 8 and "-" not in report_period:
        report_period = f"{report_period[0:4]}-{report_period[4:6]}-{report_period[6:8]}"
    if len(filing_date) == 8 and "-" not in filing_date:
        filing_date = f"{filing_date[0:4]}-{filing_date[4:6]}-{filing_date[6:8]}"

    # ── Date range assertion ──
    today_str = date.today().strftime("%Y%m%d")
    rp_clean = report_period.replace("-", "")
    if rp_clean < "20191001" or rp_clean > today_str:
        raise ValueError(
            f"report_period {report_period} out of range [2019-10-01, {date.today().isoformat()}] "
            f"for {ticker}"
        )

    # ── filing_date >= report_period assertion ──
    is_anomalous = False
    fd_clean = filing_date.replace("-", "")
    rp_clean2 = report_period.replace("-", "")
    if fd_clean < rp_clean2:
        anomaly = {
            "ticker": ticker,
            "report_period": report_period,
            "ann_date": filing_date,
            "reason": "filing_date < report_period",
        }
        if not allow_future_filing:
            raise ValueError(
                f"filing_date {filing_date} < report_period {report_period} "
                f"for {ticker}. Use --allow-future-filing-anomalies to quarantine."
            )
        anomalies.append(anomaly)
        notes.append(f"ANOMALY: filing_date {filing_date} < report_period {report_period}")
        is_anomalous = True

    # ── Assemble row ──
    row: Dict[str, Any] = {
        "ticker": ticker,
        "code": code,
        "report_period": report_period,
        "filing_date": filing_date,
        "source_url": SOURCE_URL_FINA,  # primary endpoint
        "source_provider": PROVIDER_LABEL,
        "ingested_at": ingested_at,
    }

    # ── Score component fields ──
    for out_field, pin in FIELD_MAP.items():
        if out_field in ("operating_margin", "accruals_ratio", "gross_margin"):
            continue  # handled separately
        if pin is None:
            continue  # derived field, no direct Tushare source
        endpoint, ts_key = pin
        if endpoint == "fina_indicator":
            val = safe_float(joined_row.get(ts_key))
        else:
            # Other endpoint fields use suffix convention: ts_key_endpoint
            val = safe_float(joined_row.get(f"{ts_key}_{endpoint}"))
        row[out_field] = val
        if val is None and out_field != "accruals_ratio":
            notes.append(f"{out_field}: NULL from Tushare {endpoint}.{ts_key}")

    # ── Intermediate fields (from 4 endpoints) ──
    for out_field, (endpoint, ts_key) in INTERMEDIATE_MAP.items():
        if endpoint == "fina_indicator":
            val = safe_float(joined_row.get(ts_key))
        else:
            val = safe_float(joined_row.get(f"{ts_key}_{endpoint}"))
        row[out_field] = val
        if val is None:
            notes.append(f"{out_field}: NULL from Tushare {endpoint}.{ts_key}")

    # ── gross_margin: primary + fallback ──
    gm_primary = safe_float(joined_row.get("grossprofit_margin"))
    if gm_primary is not None:
        row["gross_margin"] = gm_primary
    else:
        # Fallback: (_total_revenue - _total_cogs) / _total_revenue * 100
        tr = row.get("_total_revenue")
        tc = row.get("_total_cogs")
        if tr is not None and tc is not None and tr != 0:
            row["gross_margin"] = round((tr - tc) / tr * 100, 6)
            notes.append(
                "gross_margin: fallback derived from (_total_revenue - _total_cogs) / "
                "_total_revenue * 100 (grossprofit_margin NULL)"
            )
        else:
            row["gross_margin"] = None
            missing = []
            if tr is None:
                missing.append("_total_revenue")
            if tc is None:
                missing.append("_total_cogs")
            notes.append(
                f"gross_margin: NULL — grossprofit_margin NULL and fallback "
                f"intermediates missing: {', '.join(missing) if missing else '_total_revenue=0'}"
            )

    # ── operating_margin: primary + fallback ──
    op_margin_primary = safe_float(joined_row.get("op_of_gr"))
    if op_margin_primary is not None:
        row["operating_margin"] = op_margin_primary
    else:
        # Fallback: _op_income / _total_revenue
        op_income = row.get("_op_income")
        # If fina_indicator.op_income was NULL, try income.operate_profit as fallback
        if op_income is None:
            op_income_fb = safe_float(joined_row.get("operate_profit_income"))
            if op_income_fb is not None:
                op_income = op_income_fb
                row["_op_income"] = op_income_fb  # persist the fallback value
                notes.append("_op_income: fallback from income.operate_profit")

        total_revenue = row.get("_total_revenue")
        if op_income is not None and total_revenue is not None and total_revenue != 0:
            row["operating_margin"] = round(op_income / total_revenue, 6)
            notes.append(
                "operating_margin: fallback formula _op_income / _total_revenue "
                "used because op_of_gr NULL"
            )
        else:
            row["operating_margin"] = None
            missing = []
            if op_income is None:
                missing.append("_op_income")
            if total_revenue is None:
                missing.append("_total_revenue")
            notes.append(
                f"operating_margin: NULL — op_of_gr NULL and fallback "
                f"intermediates missing: {', '.join(missing) if missing else '_total_revenue=0'}"
            )

    # ── accruals_ratio derivation ──
    net_income = row.get("_net_income")
    net_cf_op = row.get("_net_cashflow_op")
    total_assets = row.get("_total_assets")
    if (
        net_income is not None
        and net_cf_op is not None
        and total_assets is not None
        and total_assets != 0
    ):
        row["accruals_ratio"] = round((net_income - net_cf_op) / total_assets, 6)
    else:
        row["accruals_ratio"] = None
        missing = []
        if net_income is None:
            missing.append("_net_income")
        if net_cf_op is None:
            missing.append("_net_cashflow_op")
        if total_assets is None:
            missing.append("_total_assets")
        if not missing and total_assets == 0:
            missing.append("_total_assets=0")
        if missing:
            notes.append(
                f"accruals_ratio: NULL — intermediates missing: {', '.join(missing)}"
            )

    # ── free_cash_flow reason ──
    if row["free_cash_flow"] is None:
        notes.append(
            "free_cash_flow: fcff not reported by Tushare fina_indicator "
            "for this ticker/period"
        )

    # ── ann_date skew note ──
    if ann_date_skew:
        skew_parts = [f"{ep}={dt}" for ep, dt in sorted(ann_date_skew.items())]
        notes.append(f"ann_date_skew: {', '.join(skew_parts)}")

    # ── Assemble data_quality_note ──
    row["data_quality_note"] = "; ".join(notes) if notes else ""

    return row, is_anomalous


def pd_is_notna(val: Any) -> bool:
    """Check if a value is not NaN/None in pandas sense."""
    if val is None:
        return False
    try:
        import pandas as pd
        return bool(pd.notna(val))
    except Exception:
        return True


# ═══════════════════════════════════════════════════════════════════════════
# ROE overlap cross-check (V2: year-end-only join)
# ═══════════════════════════════════════════════════════════════════════════
def load_existing_fundamentals(path: Path) -> Dict[Tuple[str, str], float]:
    """Load existing fundamentals.jsonl, returning {(ticker, report_period): roe}."""
    if not path.exists():
        return {}
    existing: Dict[Tuple[str, str], float] = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            ticker = str(row.get("ticker", ""))
            period = str(row.get("report_period", ""))
            roe = row.get("roe")
            if ticker and period and roe is not None:
                try:
                    existing[(ticker, period)] = float(roe)
                except (TypeError, ValueError):
                    pass
    return existing


def roe_overlap_check(
    h50a_rows: List[Dict],
    existing_fundamentals: Path,
) -> Dict:
    """Cross-check H50a ROE vs existing fundamentals.jsonl ROE.

    V2 change: Subset H50a rows to report_period matching YYYY-12-31 only.
    Requires ≥100 overlap rows (BLOCKER if fewer).
    """
    # V2: filter to year-end periods only
    year_end_rows = [
        row for row in h50a_rows
        if str(row.get("report_period", "")).endswith("-12-31")
    ]

    existing = load_existing_fundamentals(existing_fundamentals)
    anomalies: List[Dict] = []
    overlap_count = 0
    within_tolerance = 0

    for row in year_end_rows:
        key = (row["ticker"], row["report_period"])
        h28_roe = existing.get(key)
        if h28_roe is None:
            continue
        h50a_roe = row.get("roe")
        if h50a_roe is None:
            anomalies.append({
                "ticker": row["ticker"],
                "period": row["report_period"],
                "H28_roe": h28_roe,
                "H50a_roe": None,
            })
            continue
        overlap_count += 1
        delta = abs(h28_roe - h50a_roe)
        if delta <= 0.5:
            within_tolerance += 1
        else:
            anomalies.append({
                "ticker": row["ticker"],
                "period": row["report_period"],
                "H28_roe": h28_roe,
                "H50a_roe": h50a_roe,
                "abs_delta": round(delta, 4),
            })

    pct_in_tolerance = (
        round(within_tolerance / overlap_count * 100, 2) if overlap_count else 100.0
    )
    return {
        "overlap_count": overlap_count,
        "within_tolerance": within_tolerance,
        "anomaly_count": len(anomalies),
        "pct_within_tolerance": pct_in_tolerance,
        "gate_ok": pct_in_tolerance >= 95.0,
        "gate_ok_overlap_count": overlap_count >= 100,  # V2: must have ≥100 overlap rows
        "anomalies": anomalies,
        "year_end_rows_available": len(year_end_rows),
    }


# ═══════════════════════════════════════════════════════════════════════════
# Coverage computation (V2: added intermediates gate, gross_margin fallback)
# ═══════════════════════════════════════════════════════════════════════════
@dataclass
class CoverageResult:
    provenance: Dict
    universe_ticker_count: int
    processed_ticker_count: int
    ticker_coverage_pct: float
    total_rows: int
    period_count: int
    avg_periods_per_ticker: float
    per_field_non_null_pct: Dict[str, float]
    hard_field_min_pct: float
    soft_field_min_pct: float
    intermediate_min_pct: float
    roe_overlap: Dict
    fetch_failures: List[Dict]
    anomaly_count: int
    op_margin_fallback_count: int
    gross_margin_fallback_count: int
    ann_date_skew_count: int


def compute_coverage(
    rows: List[Dict],
    universe_tickers: List[str],
    fetch_failures: List[Dict],
    anomaly_count: int,
    op_margin_fallback_count: int,
    gross_margin_fallback_count: int = 0,
    ann_date_skew_count: int = 0,
    existing_fundamentals_path: Optional[Path] = None,
    ingested_at: str = "",
) -> CoverageResult:
    """Compute coverage statistics from output rows."""
    tickers_with_data = sorted(set(row["ticker"] for row in rows))
    processed = len(tickers_with_data) + len(fetch_failures)
    ticker_cov = round(len(tickers_with_data) / len(universe_tickers) * 100, 2)

    periods = sorted(set(row["report_period"] for row in rows))
    avg_periods = round(len(rows) / len(tickers_with_data), 2) if tickers_with_data else 0

    per_field: Dict[str, float] = {}
    for field in ALL_SCORE_FIELDS + ALL_INTERMEDIATE:
        non_null = sum(1 for row in rows if row.get(field) is not None)
        per_field[field] = round(non_null / len(rows) * 100, 2) if rows else 0

    hard_pcts = [per_field.get(f, 0) for f in HARD_FIELDS]
    hard_min = min(hard_pcts) if hard_pcts else 0

    soft_pcts = [per_field.get(f, 0) for f in SOFT_FIELDS]
    soft_min = min(soft_pcts) if soft_pcts else 0

    intermediate_pcts = [per_field.get(f, 0) for f in ALL_INTERMEDIATE]
    intermediate_min = min(intermediate_pcts) if intermediate_pcts else 0

    roe_overlap = roe_overlap_check(rows, existing_fundamentals_path or DEFAULT_FUNDAMENTALS)

    return CoverageResult(
        provenance={
            "provider": PROVIDER_LABEL,
            "endpoints": ["fina_indicator", "income", "cashflow", "balancesheet"],
            "doc_urls": {
                "fina_indicator": SOURCE_URL_FINA,
                "income": SOURCE_URL_INCOME,
                "cashflow": SOURCE_URL_CASHFLOW,
                "balancesheet": SOURCE_URL_BALANCESHEET,
            },
            "start_date": DEFAULT_START,
            "end_date": DEFAULT_END,
            "ingested_at": ingested_at,
            "snapshot_timestamp": datetime.now(timezone.utc).isoformat(),
            "revision": "V2",
        },
        universe_ticker_count=len(universe_tickers),
        processed_ticker_count=processed,
        ticker_coverage_pct=ticker_cov,
        total_rows=len(rows),
        period_count=len(periods),
        avg_periods_per_ticker=avg_periods,
        per_field_non_null_pct=per_field,
        hard_field_min_pct=hard_min,
        soft_field_min_pct=soft_min,
        intermediate_min_pct=intermediate_min,
        roe_overlap=roe_overlap,
        fetch_failures=fetch_failures,
        anomaly_count=anomaly_count,
        op_margin_fallback_count=op_margin_fallback_count,
        gross_margin_fallback_count=gross_margin_fallback_count,
        ann_date_skew_count=ann_date_skew_count,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Output writers
# ═══════════════════════════════════════════════════════════════════════════
def write_jsonl(rows: List[Dict], path: Path) -> None:
    """Write rows as JSONL, ensuring no NaN in output."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_anomalies(anomalies: List[Dict], path: Path) -> None:
    """Write anomaly records to JSONL."""
    if not anomalies:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for a in anomalies:
            fh.write(json.dumps(a, ensure_ascii=False) + "\n")


def write_coverage_json(coverage: CoverageResult, path: Path) -> None:
    """Write fundamentals_coverage_h50a.json (V2 schema)."""
    path.parent.mkdir(parents=True, exist_ok=True)

    ticker_ok = coverage.ticker_coverage_pct >= 98.0
    hard_ok = coverage.hard_field_min_pct >= 85.0   # V2: 85% gate
    soft_ok = coverage.soft_field_min_pct >= 50.0
    intermediate_ok = coverage.intermediate_min_pct >= 85.0
    roe_ok = coverage.roe_overlap.get("gate_ok", False)
    roe_count_ok = coverage.roe_overlap.get("gate_ok_overlap_count", False)

    all_ok = ticker_ok and hard_ok and soft_ok and intermediate_ok and roe_count_ok
    verdict = "CANDIDATE_DATASET" if all_ok else "BLOCKED"

    # Per-endpoint fetch failure breakdown
    failures_by_endpoint: Dict[str, int] = {}
    for f in coverage.fetch_failures:
        ep = f.get("endpoint", "unknown")
        failures_by_endpoint[ep] = failures_by_endpoint.get(ep, 0) + 1

    # Gross margin breakdown
    gm_primary = coverage.per_field_non_null_pct.get("gross_margin", 0)
    # (We track fallback count separately; primary vs fallback contribution is in the report)

    data = {
        "provenance": coverage.provenance,
        "universe_ticker_count": coverage.universe_ticker_count,
        "processed_ticker_count": coverage.processed_ticker_count,
        "ticker_coverage_pct": coverage.ticker_coverage_pct,
        "total_rows": coverage.total_rows,
        "period_count": coverage.period_count,
        "avg_periods_per_ticker": coverage.avg_periods_per_ticker,
        "per_field_non_null_pct": coverage.per_field_non_null_pct,
        "hard_field_min_pct": coverage.hard_field_min_pct,
        "soft_field_min_pct": coverage.soft_field_min_pct,
        "intermediate_min_pct": coverage.intermediate_min_pct,
        "gates": {
            "ticker_coverage_ge_98pct": ticker_ok,
            "hard_fields_ge_85pct": hard_ok,
            "soft_fields_ge_50pct": soft_ok,
            "intermediates_ge_85pct": intermediate_ok,
            "roe_overlap_ge_95pct": roe_ok,
            "roe_overlap_count_ge_100": roe_count_ok,
        },
        "verdict": verdict,
        "roe_overlap": {
            "overlap_count": coverage.roe_overlap["overlap_count"],
            "anomaly_count": coverage.roe_overlap["anomaly_count"],
            "pct_within_tolerance": coverage.roe_overlap["pct_within_tolerance"],
            "anomalies": coverage.roe_overlap["anomalies"],
            "year_end_rows_available": coverage.roe_overlap.get("year_end_rows_available", 0),
        },
        "fetch_failures": coverage.fetch_failures,
        "fetch_failures_by_endpoint": failures_by_endpoint,
        "anomaly_count": coverage.anomaly_count,
        "op_margin_fallback_count": coverage.op_margin_fallback_count,
        "gross_margin_fallback_count": coverage.gross_margin_fallback_count,
        "ann_date_skew_count": coverage.ann_date_skew_count,
    }
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_report(
    coverage: CoverageResult,
    rows: List[Dict],
    fetch_failures: List[Dict],
    path: Path,
) -> None:
    """Write h50a_pit_quality_ingestion_report.md (V2)."""
    prov = coverage.provenance
    per_field = coverage.per_field_non_null_pct
    ticker_ok = coverage.ticker_coverage_pct >= 98.0
    hard_ok = coverage.hard_field_min_pct >= 85.0
    soft_ok = coverage.soft_field_min_pct >= 50.0
    intermediate_ok = coverage.intermediate_min_pct >= 85.0
    roe_ok = coverage.roe_overlap.get("gate_ok", False)
    roe_count_ok = coverage.roe_overlap.get("gate_ok_overlap_count", False)
    all_ok = ticker_ok and hard_ok and soft_ok and intermediate_ok and roe_count_ok
    verdict = "CANDIDATE_DATASET" if all_ok else "BLOCKED"

    ticker_rows = Counter(row["ticker"] for row in rows)
    top10 = ticker_rows.most_common(10)
    bottom10 = ticker_rows.most_common()[-10:] if len(ticker_rows) >= 10 else []

    lines = [
        "# H50a V2 — PIT Quality Metrics Ingestion Report",
        "",
        "## Objective",
        "",
        "Ingest PIT-safe per-filing-period quality metrics from Tushare (4 endpoints: "
        "fina_indicator + income + cashflow + balancesheet) for every ticker in "
        "`universe_h30_candidate.jsonl`. V2 adds raw line-item endpoints to enable "
        "accruals_ratio derivation and gross_margin fallback.",
        "",
        "This is a data slice, not a strategy promotion.",
        "",
        "## Provenance",
        "",
        f"- **Provider:** {prov['provider']}",
        f"- **Endpoints:** {', '.join(prov['endpoints'])}",
        f"- **Doc URLs:**",
        f"  - fina_indicator: {prov['doc_urls']['fina_indicator']}",
        f"  - income: {prov['doc_urls']['income']}",
        f"  - cashflow: {prov['doc_urls']['cashflow']}",
        f"  - balancesheet: {prov['doc_urls']['balancesheet']}",
        f"- **Date range:** {prov['start_date']} → {prov['end_date']}",
        f"- **Ingested at:** {prov['ingested_at']}",
        f"- **Revision:** {prov.get('revision', 'V1')}",
        "",
        "## Coverage Summary",
        "",
        f"- Universe tickers: {coverage.universe_ticker_count}",
        f"- Tickers with data: {coverage.universe_ticker_count - len(fetch_failures)}",
        f"- Fetch failures: {len(fetch_failures)}",
        f"- Ticker coverage: {coverage.ticker_coverage_pct}%",
        f"- Total rows: {coverage.total_rows}",
        f"- Unique periods: {coverage.period_count}",
        f"- Avg periods per ticker: {coverage.avg_periods_per_ticker}",
        f"- ann_date skew > 7 days: {coverage.ann_date_skew_count} rows",
        "",
        "## Per-Field Non-Null Distribution",
        "",
        "### Score Component Fields",
        "",
        "| Field | Non-Null % | Category |",
        "|-------|-----------|----------|",
    ]

    for field in ALL_SCORE_FIELDS:
        cat = "hard" if field in HARD_FIELDS else "soft"
        lines.append(f"| {field} | {per_field.get(field, 0):.1f}% | {cat} |")

    lines += [
        "",
        "### Intermediate (Audit-Only) Fields",
        "",
        "| Field | Source Endpoint | Non-Null % |",
        "|-------|----------------|-----------|",
    ]
    for field in ALL_INTERMEDIATE:
        ep = INTERMEDIATE_MAP[field][0]
        lines.append(f"| {field} | {ep} | {per_field.get(field, 0):.1f}% |")

    lines += [
        "",
        "### Fallback Usage",
        "",
        f"- gross_margin fallback (income-derived): {coverage.gross_margin_fallback_count} rows",
        f"- operating_margin fallback: {coverage.op_margin_fallback_count} rows",
        "",
        "## Coverage Gates",
        "",
        f"| Gate | Threshold | Actual | Pass |",
        f"|------|-----------|--------|------|",
        f"| Ticker coverage | ≥ 98% | {coverage.ticker_coverage_pct}% | {'✅' if ticker_ok else '❌'} |",
        f"| Hard fields | ≥ 85% | {coverage.hard_field_min_pct:.1f}% | {'✅' if hard_ok else '❌'} |",
        f"| Soft (cash-flow) fields | ≥ 50% | {coverage.soft_field_min_pct:.1f}% | {'✅' if soft_ok else '❌'} |",
        f"| Intermediates | ≥ 85% | {coverage.intermediate_min_pct:.1f}% | {'✅' if intermediate_ok else '❌'} |",
        f"| ROE overlap tolerance | ≥ 95% | {coverage.roe_overlap['pct_within_tolerance']:.1f}% | {'✅' if roe_ok else '⚠️'} |",
        f"| ROE overlap count | ≥ 100 | {coverage.roe_overlap['overlap_count']} | {'✅' if roe_count_ok else '❌'} |",
        "",
    ]

    lines += [
        "## ROE Overlap Analysis",
        "",
        f"- Year-end H50a rows considered: {coverage.roe_overlap.get('year_end_rows_available', 0)}",
        f"- Overlap rows with existing fundamentals.jsonl: {coverage.roe_overlap['overlap_count']}",
        f"- Within ±0.5 pp tolerance: {coverage.roe_overlap['within_tolerance']} "
        f"({coverage.roe_overlap['pct_within_tolerance']:.1f}%)",
        f"- Anomalies (> 0.5 pp delta): {coverage.roe_overlap['anomaly_count']}",
        "",
    ]

    anomalies = coverage.roe_overlap.get("anomalies", [])
    if anomalies:
        lines.append("| Ticker | Period | H28 ROE | H50a ROE | Abs Delta |")
        lines.append("|--------|--------|---------|----------|-----------|")
        for a in anomalies[:20]:
            h50a_val = a.get("H50a_roe")
            h50a_str = f"{h50a_val:.2f}" if h50a_val is not None else "NULL"
            lines.append(
                f"| {a['ticker']} | {a['period']} | "
                f"{a['H28_roe']:.2f} | {h50a_str} | "
                f"{a.get('abs_delta', 'N/A')} |"
            )
        if len(anomalies) > 20:
            lines.append(f"| ... | ({len(anomalies) - 20} more) | | | |")
    else:
        lines.append("No ROE overlap anomalies found.")
    lines.append("")

    if fetch_failures:
        lines += [
            "## Fetch Failures",
            "",
            "| Ticker | Endpoint | Reason |",
            "|--------|----------|--------|",
        ]
        for f in fetch_failures[:30]:
            lines.append(f"| {f['ticker']} | {f.get('endpoint', '?')} | {f['reason']} |")
        if len(fetch_failures) > 30:
            lines.append(f"| ... | ({len(fetch_failures) - 30} more) |")
        lines.append("")
    else:
        lines += ["## Fetch Failures", "", "None — all tickers fetched successfully.", ""]

    lines += [
        "## Top 10 Tickers by Row Count",
        "",
        "| Ticker | Rows |",
        "|--------|------|",
    ]
    for t, n in top10:
        lines.append(f"| {t} | {n} |")
    lines.append("")

    if bottom10:
        lines += [
            "## Bottom 10 Tickers by Row Count",
            "",
            "| Ticker | Rows |",
            "|--------|------|",
        ]
        for t, n in sorted(bottom10, key=lambda x: x[1]):
            lines.append(f"| {t} | {n} |")
        lines.append("")

    lines += [
        f"## Anomaly Count: {coverage.anomaly_count}",
        f"## Operating Margin Fallback Usage: {coverage.op_margin_fallback_count} rows",
        f"## Gross Margin Fallback Usage: {coverage.gross_margin_fallback_count} rows",
        f"## ann_date Skew > 7 days: {coverage.ann_date_skew_count} rows",
        "",
        "## Verdict",
        "",
        f"**{verdict}**",
        "",
    ]

    if not all_ok:
        blockers = []
        if not ticker_ok:
            blockers.append(
                f"Ticker coverage {coverage.ticker_coverage_pct}% < 98% gate"
            )
        if not hard_ok:
            blockers.append(
                f"Hard field minimum {coverage.hard_field_min_pct:.1f}% < 85% gate"
            )
            # Identify binding field
            binding = min(
                [(per_field.get(f, 0), f) for f in HARD_FIELDS],
                key=lambda x: x[0],
            )
            blockers.append(
                f"  Binding hard field: {binding[1]} at {binding[0]:.1f}%"
            )
        if not soft_ok:
            blockers.append(
                f"Soft field minimum {coverage.soft_field_min_pct:.1f}% < 50% gate"
            )
        if not intermediate_ok:
            blockers.append(
                f"Intermediate minimum {coverage.intermediate_min_pct:.1f}% < 85% gate"
            )
            binding = min(
                [(per_field.get(f, 0), f) for f in ALL_INTERMEDIATE],
                key=lambda x: x[0],
            )
            blockers.append(
                f"  Binding intermediate: {binding[1]} at {binding[0]:.1f}%"
            )
        if not roe_count_ok:
            blockers.append(
                f"ROE overlap count {coverage.roe_overlap['overlap_count']} < 100"
            )
        for b in blockers:
            lines.append(f"- {b}")
        lines.append("")

    lines += [
        "## Note",
        "",
        "H50a V2 is a data slice, not a strategy promotion. "
        "H50b (Quality-Value Composite Redesign) is blocked on this output.",
        "",
    ]

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ═══════════════════════════════════════════════════════════════════════════
# Smoke validation
# ═══════════════════════════════════════════════════════════════════════════
def run_smoke_validation(
    jsonl_path: Path,
    coverage_path: Path,
    report_path: Path,
) -> int:
    """Load artifacts and validate shape without network calls."""
    errors = 0

    if not jsonl_path.exists():
        print(f"FAIL: {jsonl_path} missing")
        return 1
    rows = []
    with jsonl_path.open(encoding="utf-8") as fh:
        for i, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                print(f"FAIL: invalid JSONL line {i}: {exc}")
                errors += 1
                continue
            for key in ["ticker", "code", "report_period", "filing_date",
                        "source_url", "source_provider", "ingested_at",
                        "data_quality_note"]:
                if key not in row:
                    print(f"FAIL: line {i} missing identity field {key}")
                    errors += 1
            for key in ALL_SCORE_FIELDS:
                if key not in row:
                    print(f"FAIL: line {i} missing score field {key}")
                    errors += 1
            for key in ALL_INTERMEDIATE:
                if key not in row:
                    print(f"FAIL: line {i} missing intermediate field {key}")
                    errors += 1
            for key, val in row.items():
                if isinstance(val, float) and np.isnan(val):
                    print(f"FAIL: line {i} field {key} is NaN (should be null)")
                    errors += 1
            rows.append(row)
    print(f"  JSONL: {len(rows)} rows")

    if not coverage_path.exists():
        print(f"FAIL: {coverage_path} missing")
        errors += 1
    else:
        cov = json.loads(coverage_path.read_text(encoding="utf-8"))
        prov = cov.get("provenance", {})
        if prov.get("provider") != PROVIDER_LABEL:
            print(f"FAIL: wrong provider {prov.get('provider')}")
            errors += 1
        for key in ["ticker_coverage_pct", "total_rows", "per_field_non_null_pct",
                     "hard_field_min_pct", "soft_field_min_pct",
                     "intermediate_min_pct", "verdict"]:
            if key not in cov:
                print(f"FAIL: coverage JSON missing {key}")
                errors += 1

    if not report_path.exists():
        print(f"FAIL: {report_path} missing")
        errors += 1
    else:
        report = report_path.read_text(encoding="utf-8")
        for required in ["Provenance", "Coverage Summary", "Per-Field",
                         "Coverage Gates", "ROE Overlap", "Verdict"]:
            if required not in report:
                print(f"FAIL: '{required}' section missing from report")
                errors += 1

    if errors:
        print(f"\n{errors} validation error(s)")
        return 1
    print("Smoke validation PASSED")
    return 0


# ═══════════════════════════════════════════════════════════════════════════
# Main (V2: 4-endpoint ingestion loop with dedup-then-join)
# ═══════════════════════════════════════════════════════════════════════════
def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="H50a V2 — PIT Quality Metrics Ingestion (4 Tushare endpoints)"
    )
    parser.add_argument(
        "--universe", type=Path, default=DEFAULT_UNIVERSE,
        help="Path to universe JSONL",
    )
    parser.add_argument(
        "--limit", type=int, default=0,
        help="Limit to first N tickers (0 = all)",
    )
    parser.add_argument(
        "--start", default=DEFAULT_START,
        help="Start date (YYYYMMDD)",
    )
    parser.add_argument(
        "--end", default=DEFAULT_END,
        help="End date (YYYYMMDD)",
    )
    parser.add_argument(
        "--raw-dir", type=Path,
        help="Base dir for raw caches (default: data/cn_pit/raw/)",
    )
    parser.add_argument(
        "--output-jsonl", type=Path, default=DEFAULT_OUTPUT_JSONL,
        help="Path for output JSONL",
    )
    parser.add_argument(
        "--output-coverage", type=Path, default=DEFAULT_COVERAGE_OUT,
        help="Path for coverage JSON",
    )
    parser.add_argument(
        "--output-report", type=Path, default=DEFAULT_REPORT_OUT,
        help="Path for Markdown report",
    )
    parser.add_argument(
        "--allow-future-filing-anomalies", action="store_true",
        help="Quarantine filing_date < report_period rows instead of aborting",
    )
    parser.add_argument(
        "--smoke-only", action="store_true",
        help="Run smoke validation on existing artifacts only (no API calls)",
    )
    args = parser.parse_args(argv)

    if args.smoke_only:
        return run_smoke_validation(
            args.output_jsonl, args.output_coverage, args.output_report
        )

    raw_base_dir = args.raw_dir or RAW_BASE
    ingested_at = datetime.now(timezone.utc).isoformat()

    # ── Setup Tushare ──
    token = get_tushare_token()
    import tushare as ts
    pro_api = ts.pro_api(token)
    rate_limiter = RateLimiter()

    # ── Load universe ──
    all_tickers = load_universe_tickers(args.universe)
    tickers = all_tickers[:args.limit] if args.limit else all_tickers
    print(f"Universe: {len(all_tickers)} unique tickers"
          + (f", processing {len(tickers)}" if args.limit else ""))

    # ── Process each ticker ──
    all_rows: List[Dict] = []
    fetch_failures: List[Dict] = []
    anomalies: List[Dict] = []
    op_margin_fallback_count = 0
    gross_margin_fallback_count = 0
    total_ann_date_skew_count = 0

    for i, ticker in enumerate(tickers, 1):
        progress = f"[{i}/{len(tickers)}]"
        ts_code = yahoo_to_tushare_code(ticker)

        # ── Fetch all 4 endpoints for this ticker ──
        endpoint_data: Dict[str, List[Dict]] = {}
        ticker_failed = False

        for endpoint in ENDPOINT_ORDER:
            # Step 1: Check cache
            cached = read_endpoint_cache(
                ticker, endpoint, raw_base_dir, args.start, args.end
            )
            if cached is not None:
                endpoint_data[endpoint] = cached
                # Don't print for fina_indicator (cached for all 481)
                if endpoint != "fina_indicator" or args.limit:
                    print(f"  {progress} {ticker} ({ts_code}) {endpoint}: "
                          f"{len(cached)} rows (cached)")
                continue

            # Step 2: Fetch from API
            records, err = fetch_endpoint(
                pro_api, endpoint, ts_code, args.start, args.end, rate_limiter
            )
            if err:
                print(f"  {progress} {ticker} ({ts_code}) {endpoint}: FAILED — {err}")
                fetch_failures.append({
                    "ticker": ticker,
                    "endpoint": endpoint,
                    "reason": err,
                })
                ticker_failed = True
                break  # Don't try remaining endpoints for this ticker
            if records:
                write_endpoint_cache(ticker, endpoint, raw_base_dir, records)
            endpoint_data[endpoint] = records
            print(f"  {progress} {ticker} ({ts_code}) {endpoint}: {len(records)} rows")

        if ticker_failed:
            continue

        # ── Dedup-then-join pipeline ──
        import pandas as pd
        try:
            fina_df = pd.DataFrame(endpoint_data.get("fina_indicator", []))
            income_df = pd.DataFrame(endpoint_data.get("income", []))
            cashflow_df = pd.DataFrame(endpoint_data.get("cashflow", []))
            balancesheet_df = pd.DataFrame(endpoint_data.get("balancesheet", []))

            joined_df, skew_count = join_four_endpoints(
                fina_df, income_df, cashflow_df, balancesheet_df
            )
            total_ann_date_skew_count += skew_count
        except ValueError as exc:
            print(f"\n❌ Dedup/join assertion FAILED for {ticker}: {exc}")
            print("   Aborting ingestion.")
            return 1

        if joined_df.empty:
            print(f"  {progress} {ticker} ({ts_code}): no joined rows")
            continue

        # ── Build output rows ──
        joined_records = joined_df.to_dict(orient="records")
        for rec in joined_records:
            try:
                # Check for ann_date skew in this row
                ann_skew = None
                ann_vals = {}
                for col in ["ann_date", "ann_date_income", "ann_date_cashflow", "ann_date_balancesheet"]:
                    if col in rec:
                        val = safe_str(rec.get(col))
                        if val:
                            if col == "ann_date":
                                ep_name = "fina_indicator"
                            else:
                                ep_name = col.replace("ann_date_", "")
                            ann_vals[ep_name] = val
                if len(ann_vals) >= 2:
                    dates_parsed = []
                    for ep, v in ann_vals.items():
                        try:
                            from datetime import datetime as dt
                            dates_parsed.append(dt.strptime(v.replace("-", ""), "%Y%m%d"))
                        except Exception:
                            pass
                    if len(dates_parsed) >= 2:
                        delta = (max(dates_parsed) - min(dates_parsed)).days
                        if delta > 7:
                            ann_skew = ann_vals

                row, is_anom = build_row_v2(
                    ticker, rec, ann_skew, ingested_at, anomalies,
                    args.allow_future_filing_anomalies,
                )
                if row is None:
                    continue
                if is_anom:
                    continue

                # Track fallback counts
                note = row.get("data_quality_note", "")
                if "operating_margin: fallback formula" in note:
                    op_margin_fallback_count += 1
                if "gross_margin: fallback derived" in note:
                    gross_margin_fallback_count += 1

                all_rows.append(row)
            except ValueError as exc:
                print(f"\n❌ Data quality assertion FAILED: {exc}")
                print(f"   Ticker: {ticker}, ts_code: {ts_code}")
                print("   Aborting ingestion.")
                return 1

    if not all_rows:
        print("ERROR: No rows produced — check Tushare connectivity and token validity.")
        return 1

    print(f"\nTotal output rows: {len(all_rows)}")
    print(f"Fetch failures: {len(fetch_failures)}")
    print(f"Anomalies quarantined: {len(anomalies)}")
    print(f"Operating margin fallback count: {op_margin_fallback_count}")
    print(f"Gross margin fallback count: {gross_margin_fallback_count}")
    print(f"ann_date skew > 7 days: {total_ann_date_skew_count}")

    # ── Compute coverage ──
    coverage = compute_coverage(
        all_rows, all_tickers, fetch_failures, len(anomalies),
        op_margin_fallback_count, gross_margin_fallback_count,
        total_ann_date_skew_count, DEFAULT_FUNDAMENTALS, ingested_at,
    )

    print(f"\nCoverage:")
    print(f"  Ticker coverage: {coverage.ticker_coverage_pct}%")
    print(f"  Hard field min: {coverage.hard_field_min_pct:.1f}%")
    print(f"  Soft field min: {coverage.soft_field_min_pct:.1f}%")
    print(f"  Intermediate min: {coverage.intermediate_min_pct:.1f}%")
    print(f"  ROE overlap within tolerance: {coverage.roe_overlap['pct_within_tolerance']:.1f}% "
          f"({coverage.roe_overlap['anomaly_count']} anomalies out of "
          f"{coverage.roe_overlap['overlap_count']} overlap rows)")

    # ── Write outputs ──
    write_jsonl(all_rows, args.output_jsonl)
    print(f"  Wrote {args.output_jsonl}")

    write_coverage_json(coverage, args.output_coverage)
    print(f"  Wrote {args.output_coverage}")

    write_report(coverage, all_rows, fetch_failures, args.output_report)
    print(f"  Wrote {args.output_report}")

    if anomalies:
        write_anomalies(anomalies, ANOMALIES_PATH)
        print(f"  Wrote {len(anomalies)} anomalies to {ANOMALIES_PATH}")

    # ── Verdict ──
    ticker_ok = coverage.ticker_coverage_pct >= 98.0
    hard_ok = coverage.hard_field_min_pct >= 85.0
    soft_ok = coverage.soft_field_min_pct >= 50.0
    intermediate_ok = coverage.intermediate_min_pct >= 85.0
    roe_count_ok = coverage.roe_overlap.get("gate_ok_overlap_count", False)

    if not ticker_ok or not hard_ok or not soft_ok or not intermediate_ok or not roe_count_ok:
        print("\n⚠️  Coverage gate(s) FAILED:")
        if not ticker_ok:
            print(f"  - Ticker coverage {coverage.ticker_coverage_pct}% < 98%")
        if not hard_ok:
            print(f"  - Hard field min {coverage.hard_field_min_pct:.1f}% < 85%")
        if not soft_ok:
            print(f"  - Soft field min {coverage.soft_field_min_pct:.1f}% < 50%")
        if not intermediate_ok:
            print(f"  - Intermediate min {coverage.intermediate_min_pct:.1f}% < 85%")
        if not roe_count_ok:
            overlap_count = coverage.roe_overlap.get("overlap_count", 0)
            print(f"  - ROE overlap count {overlap_count} < 100")
        print("  Verdict: BLOCKED")
        return 1

    print("\n✅ All coverage gates PASSED. Verdict: CANDIDATE_DATASET")
    return 0


# ═══════════════════════════════════════════════════════════════════════════
# Backward-compat aliases (V1 tests reference these names)
# ═══════════════════════════════════════════════════════════════════════════
SOURCE_URL = SOURCE_URL_FINA

# V1 deduplicate_records kept for backward compat (not used in V2 pipeline)
def deduplicate_records(records):
    if not records:
        return []
    df = dedup_endpoint_df(records, "fina_indicator")
    return df.to_dict(orient="records") if not df.empty else []


def build_row(
    ticker: str,
    ts_record: Dict,
    ingested_at: str,
    anomalies: List[Dict],
    allow_future_filing: bool,
) -> Tuple[Optional[Dict], bool]:
    """Backward-compat wrapper: adapts V1 (5-arg) signature to V2 build_row_v2."""
    return build_row_v2(
        ticker, ts_record, None, ingested_at, anomalies, allow_future_filing
    )


if __name__ == "__main__":
    raise SystemExit(main())
