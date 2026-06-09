#!/usr/bin/env python3
"""H52d — CSI500 PIT Quality Metrics Ingestion (per-ticker, 4 Tushare endpoints).

Architecture: per-ticker iteration (Tushare financial endpoints require ts_code —
the _vip endpoints that support whole-market period queries are rate-limited to
1 call/hour). For each of 1074 CSI500 tickers, fetch all 4 endpoints via
date-range queries (start_date→end_date, one call per endpoint per ticker).
Call count: 1074 × 4 = ~4296 calls; ~14 min wall at 5 calls/sec.

Output schema matches H50a V2 verbatim. Dedup-then-join pipeline per ticker
prevents Cartesian explosion from restated quarters.

Additive — does not touch H28/H30/H50a/H52a-c artifacts.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data/cn_pit"
RAW_BASE = DATA_DIR / "raw"
ANOMALIES_PATH = DATA_DIR / "raw/h52d_anomalies.jsonl"
DEFAULT_UNIVERSE = DATA_DIR / "universe_h52a_csi500.jsonl"
H50A_FUNDAMENTALS = DATA_DIR / "fundamentals_h50a_pit_quality.jsonl"
DEFAULT_OUTPUT_JSONL = DATA_DIR / "fundamentals_h52d_csi500_pit_quality.jsonl"
DEFAULT_COVERAGE_OUT = DATA_DIR / "fundamentals_coverage_h52d.json"
DEFAULT_REPORT_OUT = ROOT / "reports/h52d_csi500_pit_quality_ingestion_report.md"
PROVIDER_LABEL = "tushare:fina_indicator+income+cashflow+balancesheet"
DEFAULT_START = "20191001"
DEFAULT_END = "20260331"

# ── Endpoint config ─────────────────────────────────────────────────────
ENDPOINT_CONFIGS = {
    "fina_indicator": {
        "cache_subdir": "h52d_tushare_fina_indicator",
        "fetch_fn": "fina_indicator",
    },
    "income": {
        "cache_subdir": "h52d_tushare_income",
        "fetch_fn": "income",
    },
    "cashflow": {
        "cache_subdir": "h52d_tushare_cashflow",
        "fetch_fn": "cashflow",
    },
    "balancesheet": {
        "cache_subdir": "h52d_tushare_balancesheet",
        "fetch_fn": "balancesheet",
    },
}
ENDPOINT_ORDER = ["fina_indicator", "income", "cashflow", "balancesheet"]

# ── Tushare → Output field mapping (identical to H50a V2) ────────────────
FIELD_MAP = {
    "roe": ("fina_indicator", "roe_waa"),
    "roa": ("fina_indicator", "roa"),
    "gross_margin": ("fina_indicator", "grossprofit_margin"),
    "operating_margin": ("fina_indicator", "op_of_gr"),
    "current_ratio": ("fina_indicator", "current_ratio"),
    "quick_ratio": ("fina_indicator", "quick_ratio"),
    "debt_to_equity": ("fina_indicator", "debt_to_eqt"),
    "operating_cash_flow_to_revenue": ("fina_indicator", "ocf_to_or"),
    "free_cash_flow": ("fina_indicator", "fcff"),
    "accruals_ratio": None,  # derived from intermediates
}

INTERMEDIATE_MAP: Dict[str, Tuple[str, str]] = {
    "_net_income": ("income", "n_income"),
    "_net_cashflow_op": ("cashflow", "n_cashflow_act"),
    "_total_assets": ("balancesheet", "total_assets"),
    "_op_income": ("fina_indicator", "op_income"),
    "_total_revenue": ("income", "total_revenue"),
    "_total_cogs": ("income", "total_cogs"),
}

HARD_FIELDS = ["roe", "roa", "gross_margin", "current_ratio", "debt_to_equity"]
SOFT_FIELDS = ["operating_cash_flow_to_revenue", "free_cash_flow", "accruals_ratio"]
ALL_SCORE_FIELDS = list(FIELD_MAP.keys())
ALL_INTERMEDIATE = list(INTERMEDIATE_MAP.keys())
ALL_OUTPUT_FIELDS = ALL_SCORE_FIELDS + ALL_INTERMEDIATE


# ═══════════════════════════════════════════════════════════════════════════
# Token discovery
# ═══════════════════════════════════════════════════════════════════════════
def get_tushare_token() -> str:
    token = os.environ.get("TUSHARE_TOKEN", "").strip()
    if token:
        return token
    token_paths = [ROOT / "scripts/.tushare_token", Path.home() / ".tushare.token"]
    for tp in token_paths:
        if tp.exists():
            token = tp.read_text(encoding="utf-8").strip()
            if token:
                return token
    try:
        import tushare as ts
        token = (ts.get_token() or "").strip()
        if token:
            return token
    except Exception:
        pass
    raise RuntimeError(f"Tushare token missing — tried env, {token_paths}")


# ═══════════════════════════════════════════════════════════════════════════
# Universe loading
# ═══════════════════════════════════════════════════════════════════════════
def load_h52a_tickers(path: Path) -> List[str]:
    """Load unique tickers from H52a universe JSONL (Yahoo format: 600872.SS)."""
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


def yahoo_to_ts_code(ticker: str) -> str:
    """Convert Yahoo ticker (600872.SS) → Tushare code (600872.SH)."""
    if ticker.endswith(".SS"):
        return ticker[:-3] + ".SH"
    if ticker.endswith(".SZ"):
        return ticker
    raise ValueError(f"unsupported ticker suffix: {ticker}")


# ═══════════════════════════════════════════════════════════════════════════
# Per-ticker per-endpoint cache (H50a pattern)
# ═══════════════════════════════════════════════════════════════════════════
def _cache_path(ticker: str, raw_base: Path, endpoint: str) -> Path:
    cache_dir = raw_base / ENDPOINT_CONFIGS[endpoint]["cache_subdir"]
    ts_code = yahoo_to_ts_code(ticker)
    return cache_dir / f"{ts_code}.csv"


def read_endpoint_cache(
    ticker: str, endpoint: str, raw_base: Path, start: str, end: str
) -> Optional[List[Dict]]:
    """Read cached Tushare records for one endpoint. Returns None if incomplete."""
    cp = _cache_path(ticker, raw_base, endpoint)
    if not cp.exists():
        return None
    try:
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
    ticker: str, endpoint: str, raw_base: Path, records: List[Dict]
) -> None:
    if not records:
        return
    cp = _cache_path(ticker, raw_base, endpoint)
    cp.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(records).to_csv(cp, index=False)


# ═══════════════════════════════════════════════════════════════════════════
# Endpoint field lists (identical to H50a V2)
# ═══════════════════════════════════════════════════════════════════════════
def _all_fina_indicator_fields() -> List[str]:
    identity = ["ts_code", "end_date", "ann_date", "update_flag"]
    mapped = [v[1] for v in FIELD_MAP.values() if v is not None and v[0] == "fina_indicator"]
    intermediate = [
        INTERMEDIATE_MAP[k][1] for k in ALL_INTERMEDIATE if INTERMEDIATE_MAP[k][0] == "fina_indicator"
    ]
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
        INTERMEDIATE_MAP[k][1] for k in ALL_INTERMEDIATE if INTERMEDIATE_MAP[k][0] == "income"
    ]
    seen = set(identity)
    result = list(identity)
    for f in mapped + intermediate:
        if f not in seen:
            seen.add(f)
            result.append(f)
    if "operate_profit" not in seen:
        result.append("operate_profit")
    return result


def _all_cashflow_fields() -> List[str]:
    identity = ["ts_code", "end_date", "ann_date", "update_flag"]
    intermediate = [
        INTERMEDIATE_MAP[k][1] for k in ALL_INTERMEDIATE if INTERMEDIATE_MAP[k][0] == "cashflow"
    ]
    return identity + intermediate


def _all_balancesheet_fields() -> List[str]:
    identity = ["ts_code", "end_date", "ann_date", "update_flag"]
    intermediate = [
        INTERMEDIATE_MAP[k][1] for k in ALL_INTERMEDIATE if INTERMEDIATE_MAP[k][0] == "balancesheet"
    ]
    return identity + intermediate


ENDPOINT_FIELDS = {
    "fina_indicator": _all_fina_indicator_fields,
    "income": _all_income_fields,
    "cashflow": _all_cashflow_fields,
    "balancesheet": _all_balancesheet_fields,
}


# ═══════════════════════════════════════════════════════════════════════════
# Rate limiter
# ═══════════════════════════════════════════════════════════════════════════
@dataclass
class RateLimiter:
    min_interval: float = 0.6  # ~1.5 calls/sec; calibrated for Tushare 2000+ financial endpoint quota (~80-200 calls/min)
    _last_call: float = 0.0

    def wait(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_call = time.monotonic()


# ═══════════════════════════════════════════════════════════════════════════
# Tushare API fetch (per-ticker, per-endpoint, date range query)
# ═══════════════════════════════════════════════════════════════════════════
def fetch_endpoint(
    pro_api,
    endpoint: str,
    ts_code: str,
    start: str,
    end: str,
    rate_limiter: RateLimiter,
    max_retries: int = 5,
) -> Tuple[Optional[List[Dict]], Optional[str]]:
    """Fetch one endpoint for one ticker with exponential backoff."""
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
                "429" in msg or "rate" in msg.lower()
                or "limit" in msg.lower() or "too many" in msg.lower()
                or "频繁" in msg
            )
            if not is_rate_limit:
                return None, f"fetch error: {msg[:200]}"
            if attempt < max_retries - 1:
                base = 2 + 2 * attempt  # linear backoff: 2, 4, 6, 8s — avoids 60s collapse on transient 429s
                jitter = random.uniform(0, 1.0)
                sleep_s = base + jitter
                print(f"  Rate-limited {endpoint} for {ts_code}, retry {attempt+1}/{max_retries} "
                      f"after {sleep_s:.1f}s...", flush=True)
                time.sleep(sleep_s)
            else:
                return None, f"rate-limit exhausted after {max_retries} retries: {msg[:200]}"
    return None, "unknown fetch error"


# ═══════════════════════════════════════════════════════════════════════════
# Dedup-then-join pipeline (per ticker, identical to H50a V2)
# ═══════════════════════════════════════════════════════════════════════════
def dedup_endpoint_df(records: List[Dict], endpoint_name: str) -> pd.DataFrame:
    if not records:
        return pd.DataFrame()
    df = pd.DataFrame(records)
    for col in ["ts_code", "end_date"]:
        if col not in df.columns:
            raise ValueError(f"endpoint {endpoint_name}: missing required column '{col}'")
    if "ann_date" not in df.columns:
        df["ann_date"] = df["end_date"]
    if "update_flag" not in df.columns:
        df["update_flag"] = "1"
    df["update_flag_str"] = df["update_flag"].fillna("1").astype(str)
    df = df.sort_values(
        ["ts_code", "end_date", "ann_date", "update_flag_str"],
        ascending=[True, True, True, True],
    ).reset_index(drop=True)
    df = df.drop(columns=["update_flag_str"])
    before = len(df)
    df = df.drop_duplicates(subset=["ts_code", "end_date"], keep="last").reset_index(drop=True)
    after = len(df)
    if before != after:
        print(f"  dedup {endpoint_name}: removed {before - after} duplicates")
    dup_check = df.groupby(["ts_code", "end_date"]).size()
    if dup_check.max() > 1:
        offenders = dup_check[dup_check > 1].head(5).to_dict()
        raise ValueError(f"FATAL: {endpoint_name} still has duplicates after dedup: {offenders}")
    return df


def join_four_endpoints(
    fina_df: pd.DataFrame,
    income_df: pd.DataFrame,
    cashflow_df: pd.DataFrame,
    balancesheet_df: pd.DataFrame,
) -> Tuple[pd.DataFrame, int]:
    """Dedup each endpoint, then LEFT JOIN. Returns (joined_df, ann_date_skew_count)."""
    dfs = {}
    for name, df in [
        ("fina_indicator", fina_df),
        ("income", income_df),
        ("cashflow", cashflow_df),
        ("balancesheet", balancesheet_df),
    ]:
        dfs[name] = dedup_endpoint_df(df.to_dict(orient="records") if not df.empty else [], name)

    fina = dfs["fina_indicator"]
    if fina.empty:
        return fina, 0
    fina_len = len(fina)
    join_keys = ["ts_code", "end_date"]

    def _add_suffix(df: pd.DataFrame, suffix: str) -> pd.DataFrame:
        rename = {col: f"{col}_{suffix}" for col in df.columns if col not in join_keys}
        return df.rename(columns=rename)

    income_s = _add_suffix(dfs["income"], "income") if not dfs["income"].empty else None
    cashflow_s = _add_suffix(dfs["cashflow"], "cashflow") if not dfs["cashflow"].empty else None
    bs_s = _add_suffix(dfs["balancesheet"], "balancesheet") if not dfs["balancesheet"].empty else None

    joined = fina.copy()
    for suffix_df, label in [
        (income_s, "income"), (cashflow_s, "cashflow"), (bs_s, "balancesheet")
    ]:
        if suffix_df is not None and not suffix_df.empty:
            joined = joined.merge(suffix_df, on=join_keys, how="left", suffixes=("", f"_{label}"))

    if len(joined) != fina_len:
        raise ValueError(f"FATAL: join produced {len(joined)} rows, expected {fina_len}")

    # ann_date reconciliation
    skew_count = 0
    ann_cols = []
    if "ann_date" in joined.columns:
        ann_cols.append("ann_date")
    for suffix in ["income", "cashflow", "balancesheet"]:
        col = f"ann_date_{suffix}"
        if col in joined.columns:
            ann_cols.append(col)

    if len(ann_cols) >= 2:
        joined["filing_date_ann"] = joined[ann_cols[0]]
        for idx in joined.index:
            vals = []
            for ac in ann_cols:
                v = joined.at[idx, ac]
                if pd.notna(v):
                    vals.append(str(v))
            if vals:
                joined.at[idx, "filing_date_ann"] = max(vals)

        try:
            from datetime import datetime as dt
            primary = pd.to_datetime(joined[ann_cols[0]], errors="coerce")
            for col in ann_cols[1:]:
                secondary = pd.to_datetime(joined[col], errors="coerce")
                mask = primary.notna() & secondary.notna()
                if mask.any():
                    delta = (primary - secondary).abs().dt.days
                    skew_count += int((delta > 7).sum())
        except Exception:
            pass
    else:
        joined["filing_date_ann"] = joined.get("ann_date", joined.get("end_date", None))

    return joined, skew_count


# ═══════════════════════════════════════════════════════════════════════════
# Data quality helpers
# ═══════════════════════════════════════════════════════════════════════════
def nan_to_none(value: Any) -> Any:
    if value is None:
        return None
    try:
        if isinstance(value, float) and np.isnan(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def safe_float(value: Any) -> Optional[float]:
    v = nan_to_none(value)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def safe_str(value: Any) -> Optional[str]:
    v = nan_to_none(value)
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


def pd_is_notna(val: Any) -> bool:
    if val is None:
        return False
    try:
        return bool(pd.notna(val))
    except Exception:
        return True


# ═══════════════════════════════════════════════════════════════════════════
# Row builder (identical to H50a V2 build_row_v2)
# ═══════════════════════════════════════════════════════════════════════════
def build_row(
    ticker: str,
    joined_row: Dict,
    ann_date_skew: Optional[Dict[str, str]],
    ingested_at: str,
    anomalies: List[Dict],
    allow_future_filing: bool,
) -> Tuple[Optional[Dict], bool]:
    notes: List[str] = []
    ts_code = yahoo_to_ts_code(ticker)
    code = ts_code

    end_date = safe_str(joined_row.get("end_date"))
    if not end_date or not ticker or not code:
        raise ValueError(f"NULL identity: ticker={ticker!r}, code={code!r}, end_date={end_date!r}")

    ann_date = safe_str(joined_row.get("ann_date"))
    filing_date = ann_date
    if "filing_date_ann" in joined_row:
        fd_val = joined_row.get("filing_date_ann")
        if pd_is_notna(fd_val):
            filing_date = safe_str(fd_val)
    if not filing_date:
        return None, False

    report_period = end_date
    if len(report_period) == 8 and "-" not in report_period:
        report_period = f"{report_period[0:4]}-{report_period[4:6]}-{report_period[6:8]}"
    if len(filing_date) == 8 and "-" not in filing_date:
        filing_date = f"{filing_date[0:4]}-{filing_date[4:6]}-{filing_date[6:8]}"

    today_str = date.today().strftime("%Y%m%d")
    rp_clean = report_period.replace("-", "")
    if rp_clean < "20191001" or rp_clean > today_str:
        raise ValueError(f"report_period {report_period} out of range for {ticker}")

    is_anomalous = False
    fd_clean = filing_date.replace("-", "")
    rp_clean2 = report_period.replace("-", "")
    if fd_clean < rp_clean2:
        anomaly = {
            "ticker": ticker, "report_period": report_period,
            "ann_date": filing_date, "reason": "filing_date < report_period",
        }
        if not allow_future_filing:
            raise ValueError(f"filing_date {filing_date} < report_period {report_period}")
        anomalies.append(anomaly)
        notes.append(f"ANOMALY: filing_date {filing_date} < report_period {report_period}")
        is_anomalous = True

    row: Dict[str, Any] = {
        "ticker": ticker, "code": code,
        "report_period": report_period, "filing_date": filing_date,
        "source_url": "https://tushare.pro/document/2?doc_id=79",
        "source_provider": PROVIDER_LABEL, "ingested_at": ingested_at,
    }

    # Score fields from fina_indicator
    for out_field, pin in FIELD_MAP.items():
        if out_field in ("operating_margin", "accruals_ratio", "gross_margin"):
            continue
        if pin is None:
            continue
        endpoint, ts_key = pin
        val = safe_float(joined_row.get(ts_key)) if endpoint == "fina_indicator" else safe_float(
            joined_row.get(f"{ts_key}_{endpoint}")
        )
        row[out_field] = val
        if val is None and out_field != "accruals_ratio":
            notes.append(f"{out_field}: NULL from {endpoint}.{ts_key}")

    # Intermediates
    for out_field, (endpoint, ts_key) in INTERMEDIATE_MAP.items():
        val = safe_float(joined_row.get(ts_key)) if endpoint == "fina_indicator" else safe_float(
            joined_row.get(f"{ts_key}_{endpoint}")
        )
        row[out_field] = val
        if val is None:
            notes.append(f"{out_field}: NULL from {endpoint}.{ts_key}")

    # gross_margin
    gm_primary = safe_float(joined_row.get("grossprofit_margin"))
    if gm_primary is not None:
        row["gross_margin"] = gm_primary
    else:
        tr, tc = row.get("_total_revenue"), row.get("_total_cogs")
        if tr is not None and tc is not None and tr != 0:
            row["gross_margin"] = round((tr - tc) / tr * 100, 6)
            notes.append("gross_margin: fallback derived from (_total_revenue - _total_cogs) / _total_revenue * 100")
        else:
            row["gross_margin"] = None
            missing = []
            if tr is None: missing.append("_total_revenue")
            if tc is None: missing.append("_total_cogs")
            notes.append(f"gross_margin: NULL — fallback intermediates missing: {', '.join(missing) if missing else '_total_revenue=0'}")

    # operating_margin
    op_margin_primary = safe_float(joined_row.get("op_of_gr"))
    if op_margin_primary is not None:
        row["operating_margin"] = op_margin_primary
    else:
        op_income = row.get("_op_income")
        if op_income is None:
            op_income_fb = safe_float(joined_row.get("operate_profit_income"))
            if op_income_fb is not None:
                op_income = op_income_fb
                row["_op_income"] = op_income_fb
                notes.append("_op_income: fallback from income.operate_profit")
        total_revenue = row.get("_total_revenue")
        if op_income is not None and total_revenue is not None and total_revenue != 0:
            row["operating_margin"] = round(op_income / total_revenue, 6)
            notes.append("operating_margin: fallback _op_income / _total_revenue")
        else:
            row["operating_margin"] = None
            missing = []
            if op_income is None: missing.append("_op_income")
            if total_revenue is None: missing.append("_total_revenue")
            notes.append(f"operating_margin: NULL — fallback missing: {', '.join(missing) if missing else '_total_revenue=0'}")

    # accruals_ratio (raw signed, NO abs())
    ni, ncf, ta = row.get("_net_income"), row.get("_net_cashflow_op"), row.get("_total_assets")
    if ni is not None and ncf is not None and ta is not None and ta != 0:
        row["accruals_ratio"] = round((ni - ncf) / ta, 6)
    else:
        row["accruals_ratio"] = None
        missing = []
        if ni is None: missing.append("_net_income")
        if ncf is None: missing.append("_net_cashflow_op")
        if ta is None: missing.append("_total_assets")
        if not missing and ta == 0: missing.append("_total_assets=0")
        if missing:
            notes.append(f"accruals_ratio: NULL — missing: {', '.join(missing)}")

    if row.get("free_cash_flow") is None:
        notes.append("free_cash_flow: fcff not reported")

    if ann_date_skew:
        skew_parts = [f"{ep}={dt}" for ep, dt in sorted(ann_date_skew.items())]
        notes.append(f"ann_date_skew: {', '.join(skew_parts)}")

    row["data_quality_note"] = "; ".join(notes) if notes else ""
    return row, is_anomalous


# ═══════════════════════════════════════════════════════════════════════════
# ROE overlap check (year-end only, vs H50a fundamentals)
# ═══════════════════════════════════════════════════════════════════════════
def load_h50a_roe(path: Optional[Path]) -> Dict[Tuple[str, str], float]:
    if not path or not path.exists():
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


def roe_overlap_check(h52d_rows: List[Dict], h50a_path: Optional[Path]) -> Dict:
    """Cross-check H52d ROE vs H50a ROE. Year-end periods only."""
    year_end_rows = [row for row in h52d_rows if str(row.get("report_period", "")).endswith("-12-31")]
    existing = load_h50a_roe(h50a_path)
    anomalies: List[Dict] = []
    overlap_count = 0
    within_tolerance = 0

    for row in year_end_rows:
        key = (row["ticker"], row["report_period"])
        h50a_roe = existing.get(key)
        if h50a_roe is None:
            continue
        h52d_roe = row.get("roe")
        if h52d_roe is None:
            anomalies.append({
                "ticker": row["ticker"], "report_period": row["report_period"],
                "h50a_roe": h50a_roe, "h52d_roe": None,
            })
            continue
        overlap_count += 1
        delta = abs(float(h50a_roe) - float(h52d_roe))
        if delta <= 0.5:
            within_tolerance += 1
        else:
            anomalies.append({
                "ticker": row["ticker"], "report_period": row["report_period"],
                "h50a_roe": float(h50a_roe), "h52d_roe": float(h52d_roe),
                "abs_delta": round(delta, 4),
            })

    pct = round(within_tolerance / overlap_count * 100, 2) if overlap_count else 100.0
    return {
        "overlap_count": overlap_count,
        "pct_within_tolerance": pct,
        "anomaly_count": len(anomalies),
        "anomalies": anomalies,
        "year_end_rows_available": len(year_end_rows),
    }


# ═══════════════════════════════════════════════════════════════════════════
# Coverage computation
# ═══════════════════════════════════════════════════════════════════════════
def compute_coverage(
    rows: List[Dict],
    universe_tickers: List[str],
    fetch_failures: List[Dict],
    anomaly_count: int,
    op_margin_fb: int,
    gross_margin_fb: int,
    ann_date_skew: int,
    h50a_path: Optional[Path],
    ingested_at: str,
) -> Dict:
    tickers_with_data = sorted(set(row["ticker"] for row in rows))
    ticker_cov = round(len(tickers_with_data) / len(universe_tickers) * 100, 2) if universe_tickers else 0
    periods = sorted(set(row["report_period"] for row in rows))
    avg_periods = round(len(rows) / len(tickers_with_data), 2) if tickers_with_data else 0

    per_field: Dict[str, float] = {}
    for field in ALL_SCORE_FIELDS + ALL_INTERMEDIATE:
        non_null = sum(1 for row in rows if row.get(field) is not None)
        per_field[field] = round(non_null / len(rows) * 100, 2) if rows else 0

    hard_min = min(per_field.get(f, 0) for f in HARD_FIELDS)
    soft_min = min(per_field.get(f, 0) for f in SOFT_FIELDS)
    intermediate_min = min(per_field.get(f, 0) for f in ALL_INTERMEDIATE)

    roe_overlap = roe_overlap_check(rows, h50a_path)

    ticker_ok = ticker_cov >= 98.0
    hard_ok = hard_min >= 85.0
    soft_ok = soft_min >= 50.0
    intermediate_ok = intermediate_min >= 85.0
    accruals_ok = per_field.get("accruals_ratio", 0) >= 50.0
    roe_overlap_ok = roe_overlap.get("overlap_count", 0) < 30 or roe_overlap.get("pct_within_tolerance", 0) >= 99.0
    all_ok = ticker_ok and hard_ok and soft_ok and intermediate_ok and accruals_ok and roe_overlap_ok
    verdict = "CANDIDATE_DATASET" if all_ok else "BLOCKED"

    failures_by_endpoint: Dict[str, int] = {}
    for f in fetch_failures:
        ep = f.get("endpoint", "unknown")
        failures_by_endpoint[ep] = failures_by_endpoint.get(ep, 0) + 1

    return {
        "provenance": {
            "provider": PROVIDER_LABEL,
            "endpoints": ["fina_indicator", "income", "cashflow", "balancesheet"],
            "axis": "ticker",
            "universe_source": "data/cn_pit/universe_h52a_csi500.jsonl",
            "date_range": f"{DEFAULT_START} -> {DEFAULT_END}",
            "snapshot_timestamp": datetime.now(timezone.utc).isoformat(),
            "revision": "V1",
        },
        "universe_ticker_count": len(universe_tickers),
        "processed_ticker_count": len(tickers_with_data),
        "ticker_coverage_pct": ticker_cov,
        "total_rows": len(rows),
        "period_count": len(periods),
        "avg_periods_per_ticker": avg_periods,
        "per_field_non_null_pct": per_field,
        "hard_field_min_pct": hard_min,
        "soft_field_min_pct": soft_min,
        "intermediate_min_pct": intermediate_min,
        "gates": {
            "ticker_coverage_ge_98pct": ticker_ok,
            "hard_fields_ge_85pct": hard_ok,
            "soft_fields_ge_50pct": soft_ok,
            "intermediates_ge_85pct": intermediate_ok,
            "accruals_ratio_ge_50pct": accruals_ok,
            "h50a_overlap_ge_99pct": roe_overlap_ok,
        },
        "op_margin_fallback_count": op_margin_fb,
        "gross_margin_fallback_count": gross_margin_fb,
        "ann_date_skew_count": ann_date_skew,
        "h50a_overlap": roe_overlap,
        "fetch_failures": fetch_failures,
        "fetch_failures_by_endpoint": failures_by_endpoint,
        "anomalies_quarantined": anomaly_count,
        "verdict": verdict,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Output writers
# ═══════════════════════════════════════════════════════════════════════════
def write_jsonl(rows: List[Dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_anomalies(anomalies: List[Dict], path: Path) -> None:
    if not anomalies:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for a in anomalies:
            fh.write(json.dumps(a, ensure_ascii=False) + "\n")


def write_coverage_json(coverage: Dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(coverage, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_report(coverage: Dict, rows: List[Dict], path: Path) -> None:
    per_field = coverage["per_field_non_null_pct"]
    verdict = coverage["verdict"]
    gates = coverage["gates"]
    prov = coverage["provenance"]
    ticker_rows = Counter(row["ticker"] for row in rows)
    top10 = ticker_rows.most_common(10)

    lines = [
        "# H52d — CSI500 PIT Quality Metrics Ingestion Report",
        "",
        "## Objective",
        "",
        "Ingest PIT-safe per-filing-period quality metrics from Tushare (4 endpoints: "
        "fina_indicator + income + cashflow + balancesheet) for every ticker in "
        "`universe_h52a_csi500.jsonl`. Per-ticker iteration (Tushare API requires ts_code "
        "for financial endpoints).",
        "",
        "Output schema matches H50a V2 verbatim.",
        "",
        "## Provenance",
        "",
        f"- **Provider:** {prov['provider']}",
        f"- **Endpoints:** {', '.join(prov['endpoints'])}",
        f"- **Axis:** {prov['axis']}",
        f"- **Universe source:** {prov['universe_source']}",
        f"- **Date range:** {prov['date_range']}",
        f"- **Snapshot:** {prov['snapshot_timestamp']}",
        "",
        "## Coverage Summary",
        "",
        f"- Universe tickers: {coverage['universe_ticker_count']}",
        f"- Tickers with data: {coverage['processed_ticker_count']}",
        f"- Ticker coverage: {coverage['ticker_coverage_pct']}%",
        f"- Total rows: {coverage['total_rows']}",
        f"- Unique periods: {coverage['period_count']}",
        f"- Avg periods per ticker: {coverage['avg_periods_per_ticker']}",
        f"- ann_date skew > 7 days: {coverage['ann_date_skew_count']}",
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
        f"- gross_margin fallback: {coverage['gross_margin_fallback_count']} rows",
        f"- operating_margin fallback: {coverage['op_margin_fallback_count']} rows",
        "",
        "## Coverage Gates",
        "",
        "| Gate | Threshold | Actual | Pass |",
        "|------|-----------|--------|------|",
        f"| Ticker coverage | ≥ 98% | {coverage['ticker_coverage_pct']}% | {'✅' if gates['ticker_coverage_ge_98pct'] else '❌'} |",
        f"| Hard fields | ≥ 85% | {coverage['hard_field_min_pct']:.1f}% | {'✅' if gates['hard_fields_ge_85pct'] else '❌'} |",
        f"| Soft fields | ≥ 50% | {coverage['soft_field_min_pct']:.1f}% | {'✅' if gates['soft_fields_ge_50pct'] else '❌'} |",
        f"| Intermediates | ≥ 85% | {coverage['intermediate_min_pct']:.1f}% | {'✅' if gates['intermediates_ge_85pct'] else '❌'} |",
        f"| accruals_ratio | ≥ 50% | {per_field.get('accruals_ratio', 0):.1f}% | {'✅' if gates['accruals_ratio_ge_50pct'] else '❌'} |",
        f"| H50a ROE overlap | ≥ 99% | {coverage['h50a_overlap'].get('pct_within_tolerance', 0):.1f}% | {'✅' if gates['h50a_overlap_ge_99pct'] else '⚠️'} |",
        "",
        "## H50a ROE Overlap Analysis",
        f"- Year-end H52d rows: {coverage['h50a_overlap'].get('year_end_rows_available', 0)}",
        f"- Overlap with H50a: {coverage['h50a_overlap']['overlap_count']}",
        f"- Within ±0.5pp: {coverage['h50a_overlap'].get('pct_within_tolerance', 0):.1f}%",
        f"- Anomalies: {coverage['h50a_overlap']['anomaly_count']}",
        "",
    ]

    anom_list = coverage["h50a_overlap"].get("anomalies", [])
    if anom_list:
        lines.extend([
            "| Ticker | Period | H50a ROE | H52d ROE | Δ |",
            "|--------|--------|----------|----------|---|",
        ])
        for a in anom_list[:20]:
            h52d_str = f"{a['h52d_roe']:.2f}" if a["h52d_roe"] is not None else "NULL"
            lines.append(f"| {a['ticker']} | {a['report_period']} | {a['h50a_roe']:.2f} | {h52d_str} | {a.get('abs_delta','N/A')} |")
        lines.append("")
    else:
        lines.append("No ROE overlap anomalies.\n")

    ff = coverage.get("fetch_failures", [])
    if ff:
        lines += ["## Fetch Failures", "", "| Ticker | Endpoint | Reason |", "|--------|----------|--------|"]
        for f in ff[:30]:
            lines.append(f"| {f.get('ticker','?')} | {f.get('endpoint','?')} | {f['reason']} |")
        lines.append("")
    else:
        lines += ["## Fetch Failures", "", "None.", ""]

    lines += [
        "## Top 10 Tickers by Period Count",
        "",
        "| Ticker | Periods |",
        "|--------|---------|",
    ]
    for t, n in top10:
        lines.append(f"| {t} | {n} |")

    lines += [
        "",
        f"## Anomaly Count: {coverage['anomalies_quarantined']}",
        f"## Operating Margin Fallback: {coverage['op_margin_fallback_count']} rows",
        f"## Gross Margin Fallback: {coverage['gross_margin_fallback_count']} rows",
        f"## ann_date Skew: {coverage['ann_date_skew_count']} rows",
        "",
        "## Verdict",
        "",
        f"**{verdict}**",
        "",
    ]
    if verdict == "BLOCKED":
        lines.append("### Blockers")
        for gate_name, passed in gates.items():
            if not passed:
                lines.append(f"- {gate_name}: FAILED")
        lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ═══════════════════════════════════════════════════════════════════════════
# Smoke validation
# ═══════════════════════════════════════════════════════════════════════════
def run_smoke_validation(jsonl_path: Path, coverage_path: Path, report_path: Path) -> int:
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
                print(f"FAIL: line {i}: {exc}")
                errors += 1
                continue
            for key in ["ticker", "code", "report_period", "filing_date",
                        "source_url", "source_provider", "ingested_at", "data_quality_note"]:
                if key not in row:
                    print(f"FAIL: line {i} missing {key}")
                    errors += 1
            for key in ALL_SCORE_FIELDS + ALL_INTERMEDIATE:
                if key not in row:
                    print(f"FAIL: line {i} missing field {key}")
                    errors += 1
            for key, val in row.items():
                if isinstance(val, float) and np.isnan(val):
                    print(f"FAIL: line {i} field {key} is NaN")
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
                     "hard_field_min_pct", "soft_field_min_pct", "intermediate_min_pct", "verdict"]:
            if key not in cov:
                print(f"FAIL: coverage missing {key}")
                errors += 1

    if not report_path.exists():
        print(f"FAIL: {report_path} missing")
        errors += 1
    else:
        report = report_path.read_text(encoding="utf-8")
        for required in ["Provenance", "Coverage Summary", "Per-Field", "Coverage Gates", "Verdict"]:
            if required not in report:
                print(f"FAIL: '{required}' section missing from report")
                errors += 1

    if errors:
        print(f"\n{errors} validation error(s)")
        return 1
    print("Smoke validation PASSED")
    return 0


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════
def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="H52d — CSI500 PIT Quality Metrics Ingestion")
    parser.add_argument("--universe", type=Path, default=DEFAULT_UNIVERSE)
    parser.add_argument("--limit", type=int, default=0, help="Limit to first N tickers (0=all)")
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--raw-dir", type=Path, default=RAW_BASE)
    parser.add_argument("--output-jsonl", type=Path, default=DEFAULT_OUTPUT_JSONL)
    parser.add_argument("--output-coverage", type=Path, default=DEFAULT_COVERAGE_OUT)
    parser.add_argument("--output-report", type=Path, default=DEFAULT_REPORT_OUT)
    parser.add_argument("--allow-future-filing-anomalies", action="store_true")
    parser.add_argument("--smoke-only", action="store_true")
    args = parser.parse_args(argv)

    if args.smoke_only:
        return run_smoke_validation(args.output_jsonl, args.output_coverage, args.output_report)

    ingested_at = datetime.now(timezone.utc).isoformat()

    # ── Setup Tushare ──
    token = get_tushare_token()
    import tushare as ts
    pro_api = ts.pro_api(token)
    rate_limiter = RateLimiter()

    # ── Load universe ──
    all_tickers = load_h52a_tickers(args.universe)
    tickers = all_tickers[:args.limit] if args.limit else all_tickers
    print(f"Universe: {len(all_tickers)} unique tickers, processing {len(tickers)}")

    # ── Process each ticker ──
    all_rows: List[Dict] = []
    fetch_failures: List[Dict] = []
    anomalies: List[Dict] = []
    op_margin_fb = 0
    gross_margin_fb = 0
    total_skew = 0

    for i, ticker in enumerate(tickers, 1):
        progress = f"[{i}/{len(tickers)}]"
        ts_code = yahoo_to_ts_code(ticker)

        endpoint_data: Dict[str, List[Dict]] = {}
        ticker_failed = False

        for endpoint in ENDPOINT_ORDER:
            cached = read_endpoint_cache(ticker, endpoint, args.raw_dir, args.start, args.end)
            if cached is not None:
                endpoint_data[endpoint] = cached
                if args.limit:
                    print(f"  {progress} {ticker} {endpoint}: {len(cached)} (cached)")
                continue

            records, err = fetch_endpoint(pro_api, endpoint, ts_code, args.start, args.end, rate_limiter)
            if err:
                print(f"  {progress} {ticker} {endpoint}: FAILED — {err}")
                fetch_failures.append({"ticker": ticker, "endpoint": endpoint, "reason": err})
                ticker_failed = True
                break
            if records:
                write_endpoint_cache(ticker, endpoint, args.raw_dir, records)
            endpoint_data[endpoint] = records
            print(f"  {progress} {ticker} {endpoint}: {len(records)} rows")

        if ticker_failed:
            continue

        # Dedup-then-join
        try:
            fina_df = pd.DataFrame(endpoint_data.get("fina_indicator", []))
            income_df = pd.DataFrame(endpoint_data.get("income", []))
            cashflow_df = pd.DataFrame(endpoint_data.get("cashflow", []))
            bs_df = pd.DataFrame(endpoint_data.get("balancesheet", []))
            joined_df, skew_count = join_four_endpoints(fina_df, income_df, cashflow_df, bs_df)
            total_skew += skew_count
        except ValueError as exc:
            print(f"❌ Dedup/join FAILED for {ticker}: {exc}")
            return 1

        if joined_df.empty:
            continue

        joined_records = joined_df.to_dict(orient="records")
        for rec in joined_records:
            ann_skew = None
            ann_vals = {}
            for col, ep_name in [
                ("ann_date", "fi"), ("ann_date_income", "income"),
                ("ann_date_cashflow", "cashflow"), ("ann_date_balancesheet", "balancesheet"),
            ]:
                if col in rec:
                    val = safe_str(rec.get(col))
                    if val:
                        ann_vals[ep_name] = val
            if len(ann_vals) >= 2:
                from datetime import datetime as dt
                try:
                    dates_parsed = []
                    for v in ann_vals.values():
                        dates_parsed.append(dt.strptime(v.replace("-", ""), "%Y%m%d"))
                    if len(dates_parsed) >= 2 and (max(dates_parsed) - min(dates_parsed)).days > 7:
                        ann_skew = ann_vals
                except Exception:
                    pass

            try:
                row, is_anom = build_row(ticker, rec, ann_skew, ingested_at, anomalies, args.allow_future_filing_anomalies)
                if row is None:
                    continue
                if is_anom:
                    continue
                note = row.get("data_quality_note", "")
                if "operating_margin: fallback" in note:
                    op_margin_fb += 1
                if "gross_margin: fallback derived" in note:
                    gross_margin_fb += 1
                all_rows.append(row)
            except ValueError as exc:
                print(f"❌ Data quality FAILED: {exc}")
                return 1

    if not all_rows:
        print("ERROR: No rows produced.")
        return 1

    print(f"\nTotal output rows: {len(all_rows)}")
    print(f"Fetch failures: {len(fetch_failures)}")

    # ── Compute coverage ──
    coverage = compute_coverage(
        all_rows, all_tickers, fetch_failures, len(anomalies),
        op_margin_fb, gross_margin_fb, total_skew, H50A_FUNDAMENTALS, ingested_at,
    )

    # ── Write outputs ──
    print(f"\nWriting {args.output_jsonl} ...")
    write_jsonl(all_rows, args.output_jsonl)
    print(f"Writing {args.output_coverage} ...")
    write_coverage_json(coverage, args.output_coverage)
    print(f"Writing {args.output_report} ...")
    write_report(coverage, all_rows, args.output_report)

    if anomalies:
        print(f"Writing {len(anomalies)} anomalies to {ANOMALIES_PATH}")
        write_anomalies(anomalies, ANOMALIES_PATH)

    # ── Print summary ──
    print(f"\n{'='*60}")
    print(f"Verdict: {coverage['verdict']}")
    print(f"Ticker coverage: {coverage['ticker_coverage_pct']}%")
    print(f"Total rows: {coverage['total_rows']} ({coverage['period_count']} periods)")
    for gate, ok in coverage["gates"].items():
        print(f"  {gate}: {'PASS' if ok else 'FAIL'}")
    print(f"Hard field min: {coverage['hard_field_min_pct']:.1f}%")
    print(f"Soft field min: {coverage['soft_field_min_pct']:.1f}%")
    print(f"Intermediate min: {coverage['intermediate_min_pct']:.1f}%")
    roe = coverage["h50a_overlap"]
    print(f"H50a ROE overlap: {roe['overlap_count']} rows, {roe['pct_within_tolerance']:.1f}% within ±0.5pp")
    print(f"Fallbacks: gross_margin={gross_margin_fb}, operating_margin={op_margin_fb}")
    print(f"Fetch failures: {len(fetch_failures)}")
    print(f"{'='*60}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
