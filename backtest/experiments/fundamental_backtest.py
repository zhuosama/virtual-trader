#!/usr/bin/env python3
"""基本面驱动回测引擎 v1.1

v1.1 (H15-H17):
- DataQuality gate: survivorship_bias/future_function/filing_delay/ungated_fundamentals
- PIT universe via data_source.get_universe(as_of_date), no hardcoded list
- FundamentalRecord with report_period/filing_date/source_url/ingested_at
- Deployment blocked on ANY data quality flag
"""

import json, math, os
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
import yfinance as yf


VT_DIR = os.path.expanduser("~/.hermes/virtual-trader")
HS300_TICKER = "000300.SS"
MIN_TRADING_DAYS = 126
MIN_TRADE_COUNT = 30
QLIB_STATIC_FLOOR_DATE = "2005-01-01"
QLIB_PRICE_START_GRACE_DAYS = 30
TUSHARE_SNAPSHOT_CARRY_FORWARD_DAYS = 45
COMMISSION_RATE = 0.0003
STAMP_TAX_RATE = 0.001
TRANSFER_FEE_RATE = 0.00002
SLIPPAGE_BPS = 5.0


# ---- Data Quality Gate (H15) ----
@dataclass
class DataQuality:
    survivorship_bias: bool = True
    future_function: bool = True
    filing_delay: bool = True
    ungated_fundamentals: bool = True

    @property
    def is_clean(self) -> bool:
        return not (self.survivorship_bias or self.future_function or
                    self.filing_delay or self.ungated_fundamentals)

    def to_dict(self) -> dict:
        return {
            "survivorship_bias": self.survivorship_bias,
            "future_function": self.future_function,
            "filing_delay": self.filing_delay,
            "ungated_fundamentals": self.ungated_fundamentals,
            "is_clean": self.is_clean,
        }


# ---- Fundamental Record (H17) ----
@dataclass
class FundamentalRecord:
    ticker: str
    report_period: str = ""
    filing_date: str = ""
    source_url: str = ""
    ingested_at: str = ""
    roe: Optional[float] = None
    fcf_yield: Optional[float] = None
    debt_to_equity: Optional[float] = None
    pe_ratio: Optional[float] = None
    pb_ratio: Optional[float] = None
    dividend_yield: Optional[float] = None
    market_cap: Optional[float] = None

    def is_visible_as_of(self, as_of_date: str) -> bool:
        if not self.filing_date:
            return False
        return self.filing_date <= as_of_date

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}


def normalize_cn_ticker(value: str) -> str:
    """Normalize A-share code/ticker to yfinance suffix format."""
    raw = str(value or "").strip().upper()
    if not raw:
        return raw
    if raw.endswith((".SS", ".SZ")):
        return raw
    code = raw.split(".")[0]
    suffix = ".SS" if code.startswith(("5", "6", "9")) else ".SZ"
    return f"{code}{suffix}"


# ---- Data Source Abstraction (H13+H16) ----
@dataclass
class DataSource:
    name: str
    market: str
    data_quality: DataQuality = field(default_factory=DataQuality)
    research_only: bool = False

    def get_price_history(self, tickers, start, end):
        raise NotImplementedError

    def get_universe(self, as_of_date: str) -> List[str]:
        raise NotImplementedError

    def get_price_universe(self, start_date: str, end_date: str) -> List[str]:
        """Tickers whose prices must be prefetched for the full window."""
        return self.get_universe(start_date)

    def get_fundamentals(self, tickers, as_of_date):
        raise NotImplementedError

    def data_quality_for_period(self, start_date: str, end_date: str) -> DataQuality:
        """Period-aware data quality. Default: return static quality.

        Subclasses that hold historical evidence should override this to
        verify that the requested backtest window is fully covered."""
        return self.data_quality


class US_EDGAR_Source(DataSource):
    def __init__(self):
        super().__init__(name="SEC_EDGAR", market="US",
                        data_quality=DataQuality(
                            survivorship_bias=True,
                            future_function=False,
                            filing_delay=False,
                            ungated_fundamentals=True,
                        ),
                        research_only=True)

    def get_price_history(self, tickers, start, end):
        data = yf.download(tickers, start=start, end=end, progress=False)
        prices = data.get("Close", data)
        if isinstance(prices, pd.Series): prices = prices.to_frame(tickers[0])
        return prices

    def get_universe(self, as_of_date):
        return []

    def get_fundamentals(self, tickers, as_of_date):
        return {t: {} for t in tickers}


class CN_YFinanceSource(DataSource):
    def __init__(self):
        super().__init__(name="yfinance_CN", market="CN",
                        data_quality=DataQuality(
                            survivorship_bias=True,
                            future_function=True,
                            filing_delay=True,
                            ungated_fundamentals=True,
                        ),
                        research_only=True)

    def get_price_history(self, tickers, start, end):
        data = yf.download(tickers, start=start, end=end, progress=False)
        prices = data.get("Close", data)
        if isinstance(prices, pd.Series): prices = prices.to_frame(tickers[0])
        return prices

    def get_universe(self, as_of_date):
        return [
            "600900.SS", "600036.SS", "601318.SS", "000333.SZ", "000651.SZ",
            "000858.SZ", "601088.SS", "600519.SS", "002415.SZ", "601006.SS",
            "600276.SS", "601398.SS", "600030.SS", "000002.SZ", "601857.SS",
            "600028.SS", "601899.SS", "600309.SS", "002371.SZ", "601919.SS",
        ]

    def get_fundamentals(self, tickers, as_of_date):
        result = {}
        for t in tickers:
            try:
                info = yf.Ticker(t).info
                result[t] = {
                    "roe": info.get("returnOnEquity", 0) * 100 if info.get("returnOnEquity") else None,
                    "fcf_yield": info.get("freeCashflow", 0) / info.get("marketCap", 1) * 100 if info.get("marketCap") else None,
                    "debt_to_equity": info.get("debtToEquity", 0) / 100 if info.get("debtToEquity") else None,
                    "pe_ratio": info.get("trailingPE"),
                    "pb_ratio": info.get("priceToBook"),
                    "dividend_yield": info.get("dividendYield", 0) * 100 if info.get("dividendYield") else None,
                    "market_cap": info.get("marketCap"),
                }
            except Exception:
                result[t] = {}
        return result


class CN_PIT_FileSource(DataSource):
    """Point-in-time A-share source backed by local audited files.

    Expected files under root:
      - universe.jsonl: ticker/code, effective_date, optional end_date, source_url, ingested_at
      - fundamentals.jsonl: FundamentalRecord fields, including filing_date and source_url
      - prices.csv: wide daily close table with date column and ticker columns
    """

    MIN_QLIB_UNIVERSE_ROWS = 200

    def __init__(
        self,
        root: Optional[str] = None,
        prices_path: Optional[str] = None,
        universe_path: Optional[str] = None,
        universe_snapshots_path: Optional[str] = None,
    ):
        self.root = Path(root or os.path.join(VT_DIR, "data", "cn_pit"))
        self.universe_path = self._resolve_optional_path(universe_path, "universe.jsonl")
        self.universe_snapshots_path = self._resolve_optional_path(
            universe_snapshots_path, "universe_snapshots.jsonl"
        )
        self.fundamentals_path = self.root / "fundamentals.jsonl"
        if prices_path:
            candidate_prices_path = Path(prices_path)
            if candidate_prices_path.is_absolute() or candidate_prices_path.exists():
                self.prices_path = candidate_prices_path
            else:
                self.prices_path = self.root / candidate_prices_path
        else:
            self.prices_path = self.root / "prices.csv"
        self.validation_errors: List[str] = []
        self._universe_rows = self._load_universe_rows()
        self._universe_snapshot_rows = self._read_jsonl(self.universe_snapshots_path)
        self._fundamental_records = self._load_fundamental_records()
        data_quality = self._build_data_quality()
        super().__init__(
            name="cn_pit_file",
            market="CN",
            data_quality=data_quality,
            research_only=not data_quality.is_clean,
        )

    def _resolve_optional_path(self, path: Optional[str], default_name: str) -> Path:
        if not path:
            return self.root / default_name
        candidate = Path(path)
        if candidate.is_absolute() or candidate.exists():
            return candidate
        return self.root / candidate

    @staticmethod
    def _read_jsonl(path: Path) -> List[Dict]:
        if not path.exists():
            return []
        rows = []
        with open(path) as f:
            for line_no, line in enumerate(f, 1):
                text = line.strip()
                if not text:
                    continue
                try:
                    rows.append(json.loads(text))
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{path}:{line_no}: invalid JSONL: {exc}") from exc
        return rows

    def _load_universe_rows(self) -> List[Dict]:
        rows = []
        for row in self._read_jsonl(self.universe_path):
            ticker = normalize_cn_ticker(row.get("ticker") or row.get("code"))
            effective_date = row.get("effective_date") or row.get("start_date")
            end_date = row.get("end_date") or row.get("removed_date")
            source_url = row.get("source_url")
            ingested_at = row.get("ingested_at")
            if not ticker:
                self.validation_errors.append("universe: missing ticker/code")
            if not effective_date:
                self.validation_errors.append(f"universe:{ticker}: missing effective_date")
            if not source_url:
                self.validation_errors.append(f"universe:{ticker}: missing source_url")
            if not ingested_at:
                self.validation_errors.append(f"universe:{ticker}: missing ingested_at")
            rows.append({
                **row,
                "ticker": ticker,
                "effective_date": effective_date or "",
                "end_date": end_date or "",
            })
        return rows

    @staticmethod
    def _has_survivorship_marker(row: Dict) -> bool:
        note = str(row.get("data_quality_note") or row.get("note") or "").upper()
        flags = row.get("data_quality_flags") or row.get("quality_flags") or []
        if isinstance(flags, str):
            flags = [flags]
        flag_text = " ".join(str(flag).upper() for flag in flags)
        return (
            "SURVIVORSHIP_BIAS" in note
            or "SURVIVORSHIP_BIAS" in flag_text
            or row.get("survivorship_bias") is True
        )

    @staticmethod
    def _has_positive_snapshot_count(row: Dict) -> bool:
        try:
            return int(row.get("snapshot_count") or 0) > 0
        except (TypeError, ValueError):
            return False

    ACCEPTED_HISTORICAL_PROVIDERS = frozenset({"tushare:index_weight", "qlib:instruments"})

    UNSAFE_CN_PIT_FIELDS = frozenset({
        "pe_ratio", "pb_ratio", "dividend_yield", "market_cap", "fcf_yield",
    })

    def _has_unsafe_fundamental_fields(self) -> bool:
        """True if any CN PIT fundamentals contain current-valuation fields.

        pe_ratio / pb_ratio / dividend_yield / market_cap / fcf_yield are
        not PIT-safe unless sourced from a future explicit PIT provider."""
        for records in self._fundamental_records.values():
            for rec in records:
                for field in self.UNSAFE_CN_PIT_FIELDS:
                    if getattr(rec, field, None) is not None:
                        return True
        return False

    def _historical_evidence_keys(self) -> set:
        keys = set()
        for row in self._universe_snapshot_rows:
            provider = row.get("source_provider")
            if provider not in self.ACCEPTED_HISTORICAL_PROVIDERS:
                continue
            ticker = normalize_cn_ticker(row.get("ticker") or row.get("code"))
            trade_date = row.get("trade_date") or row.get("effective_date") or row.get("date")
            if ticker and trade_date:
                    keys.add((provider, ticker, trade_date))
        return keys

    def _has_plausible_qlib_intervals(self) -> bool:
        """Reject static multi-row Qlib universes masquerading as history."""
        qlib_rows = [
            row for row in self._universe_rows
            if row.get("source_provider") == "qlib:instruments"
        ]
        if len(qlib_rows) <= 1:
            return True
        intervals = {
            (row.get("effective_date", ""), row.get("end_date", ""))
            for row in qlib_rows
        }
        return len(intervals) > 1

    def _price_first_dates(self) -> Tuple[Dict[str, str], Optional[str]]:
        """Return first non-null price date per ticker and the file start date."""
        if not self.prices_path.exists():
            return {}, None
        try:
            prices = pd.read_csv(self.prices_path)
            date_col = "date" if "date" in prices.columns else "Date"
            if date_col not in prices.columns:
                return {}, None
            prices[date_col] = pd.to_datetime(prices[date_col])
            prices = prices.set_index(date_col).sort_index()
        except Exception:
            return {}, None

        if prices.empty:
            return {}, None

        first_dates: Dict[str, str] = {}
        for col in prices.columns:
            if col == HS300_TICKER:
                continue
            non_null = prices[col].dropna()
            if not non_null.empty:
                first_dates[str(col)] = non_null.index.min().strftime("%Y-%m-%d")
        return first_dates, prices.index.min().strftime("%Y-%m-%d")

    def _has_qlib_price_start_conflicts(self) -> bool:
        """Detect Qlib floor-date intervals that predate local price evidence."""
        first_dates, price_start = self._price_first_dates()
        if not first_dates or not price_start:
            return False

        cutoff = (
            pd.Timestamp(price_start) + timedelta(days=QLIB_PRICE_START_GRACE_DAYS)
        ).strftime("%Y-%m-%d")
        for row in self._universe_rows:
            if row.get("source_provider") != "qlib:instruments":
                continue
            if row.get("effective_date") != QLIB_STATIC_FLOOR_DATE:
                continue
            end_date = row.get("end_date") or "9999-12-31"
            if end_date < price_start:
                continue
            ticker = normalize_cn_ticker(row.get("ticker") or row.get("code"))
            first_price = first_dates.get(ticker)
            if first_price and first_price > cutoff:
                return True
        return False

    def _has_historical_universe_evidence(self) -> bool:
        """Return True only if ALL universe rows come from an accepted historical provider.

        Accepted providers: tushare:index_weight, qlib:instruments.
        Unknown providers are rejected.
        Both universe.jsonl rows AND universe_snapshots.jsonl must exist and
        have positive snapshot_count and matching ticker/provider/date evidence.

        H28 (H2): Also enforces MIN_QLIB_UNIVERSE_ROWS for qlib providers.
        """
        if not self._universe_rows or not self._universe_snapshot_rows:
            return False
        if not self._has_plausible_qlib_intervals():
            return False
        if self._has_qlib_price_start_conflicts():
            return False
        # H28: qlib providers must meet minimum row threshold
        qlib_rows = [r for r in self._universe_rows
                     if r.get("source_provider") == "qlib:instruments"]
        if qlib_rows and len(qlib_rows) < self.MIN_QLIB_UNIVERSE_ROWS:
            return False
        evidence_keys = self._historical_evidence_keys()
        return all(
            (
                row.get("source_provider") in self.ACCEPTED_HISTORICAL_PROVIDERS
                and self._has_positive_snapshot_count(row)
                and (
                    row.get("source_provider"),
                    normalize_cn_ticker(row.get("ticker") or row.get("code")),
                    row.get("effective_date"),
                ) in evidence_keys
            )
            for row in self._universe_rows
        )

    def _load_fundamental_records(self) -> Dict[str, List[FundamentalRecord]]:
        records: Dict[str, List[FundamentalRecord]] = {}
        for row in self._read_jsonl(self.fundamentals_path):
            ticker = normalize_cn_ticker(row.get("ticker") or row.get("code"))
            if not ticker:
                self.validation_errors.append("fundamentals: missing ticker/code")
                continue
            for field_name in ("report_period", "filing_date", "source_url", "ingested_at"):
                if not row.get(field_name):
                    self.validation_errors.append(f"fundamentals:{ticker}: missing {field_name}")
            rec = FundamentalRecord(
                ticker=ticker,
                report_period=row.get("report_period", ""),
                filing_date=row.get("filing_date", ""),
                source_url=row.get("source_url", ""),
                ingested_at=row.get("ingested_at", ""),
                roe=row.get("roe"),
                fcf_yield=row.get("fcf_yield"),
                debt_to_equity=row.get("debt_to_equity"),
                pe_ratio=row.get("pe_ratio"),
                pb_ratio=row.get("pb_ratio"),
                dividend_yield=row.get("dividend_yield"),
                market_cap=row.get("market_cap"),
            )
            records.setdefault(ticker, []).append(rec)
        for ticker in records:
            records[ticker].sort(key=lambda r: (r.filing_date, r.report_period), reverse=True)
        return records

    def _build_data_quality(self) -> DataQuality:
        has_universe = bool(self._universe_rows)
        has_fundamentals = bool(self._fundamental_records)
        has_prices = self.prices_path.exists()
        survivorship_marked = any(self._has_survivorship_marker(row) for row in self._universe_rows)
        has_historical_universe = self._has_historical_universe_evidence()
        is_valid = has_universe and has_fundamentals and has_prices and not self.validation_errors
        has_unsafe = self._has_unsafe_fundamental_fields()
        return DataQuality(
            survivorship_bias=(
                not has_universe
                or bool(self.validation_errors)
                or survivorship_marked
                or not has_historical_universe
            ),
            future_function=not is_valid or has_unsafe,
            filing_delay=not has_fundamentals or bool(self.validation_errors),
            ungated_fundamentals=not has_fundamentals or bool(self.validation_errors) or has_unsafe,
        )

    @staticmethod
    def _active(row: Dict, as_of_date: str) -> bool:
        start = row.get("effective_date", "")
        end = row.get("end_date", "")
        return bool(start) and start <= as_of_date and (not end or as_of_date <= end)

    @staticmethod
    def _overlaps(row: Dict, start_date: str, end_date: str) -> bool:
        start = row.get("effective_date", "")
        end = row.get("end_date", "") or "9999-12-31"
        return bool(start) and start <= end_date and end >= start_date

    def _tushare_evidence_dates(self) -> List[str]:
        """All trade dates from tushare:index_weight snapshot evidence."""
        return sorted({
            row["trade_date"]
            for row in self._universe_snapshot_rows
            if row.get("source_provider") == "tushare:index_weight"
            and row.get("trade_date")
        })

    @staticmethod
    def _within_tushare_carry_forward(snapshot_date: str, end_date: str) -> bool:
        """Allow a short live window after the latest official index snapshot."""
        try:
            snap = datetime.strptime(snapshot_date, "%Y-%m-%d")
            end = datetime.strptime(end_date, "%Y-%m-%d")
        except ValueError:
            return False
        if end <= snap:
            return True
        return (end - snap).days <= TUSHARE_SNAPSHOT_CARRY_FORWARD_DAYS

    def _qlib_interval_covers(self, start_date: str, end_date: str) -> bool:
        """True when qlib:instruments universe interval spans [start_date, end_date].

        H28 (H2): Requires MIN_QLIB_UNIVERSE_ROWS minimum rows, at least one
        ticker active at BOTH start AND end, and all rows must be qlib:instruments.
        """
        qlib_rows = [
            r for r in self._universe_rows
            if r.get("source_provider") == "qlib:instruments"
        ]
        if not qlib_rows or len(qlib_rows) != len(self._universe_rows):
            return False
        # Minimum scale threshold — single-row / toy data should not clear survivorship_bias
        if len(qlib_rows) < self.MIN_QLIB_UNIVERSE_ROWS:
            return False
        active_at_start = [r for r in qlib_rows if self._active(r, start_date)]
        active_at_end = [r for r in qlib_rows if self._active(r, end_date)]
        if not active_at_start or not active_at_end:
            return False
        # Must have at least one interval that completely covers [start, end]
        spans_period = any(
            self._active(r, start_date) and self._active(r, end_date)
            for r in qlib_rows
        )
        return spans_period

    def data_quality_for_period(self, start_date: str, end_date: str) -> DataQuality:
        """Period-aware data quality gate for CN PIT file source.

        Foundation: start from structural data_quality then tighten:
        - tushare:index_weight: evidence date range must cover the window.
        - qlib:instruments: interval coverage must span start→end.
        - Mixed accepted + unknown providers → survivorship_bias=True.
        - Any missing/mismatched evidence blocks survivorship clearing.
        - Unsafe valuation fields keep future_function + ungated_fundamentals True.
        """
        base = self.data_quality

        # If structurally dirty, period check can't make it clean
        if not self._universe_rows or not self._universe_snapshot_rows:
            return base
        if base.survivorship_bias:
            return base

        # Check all universe rows for period-specific issues
        evidence_keys = self._historical_evidence_keys()
        seen_providers = set()
        for row in self._universe_rows:
            provider = row.get("source_provider", "")
            seen_providers.add(provider)

            if provider not in self.ACCEPTED_HISTORICAL_PROVIDERS:
                return DataQuality(
                    survivorship_bias=True,
                    future_function=base.future_function,
                    filing_delay=base.filing_delay,
                    ungated_fundamentals=base.ungated_fundamentals,
                )

            # snapshot_count as weak evidence: must be >0 AND matched by real rows
            if not self._has_positive_snapshot_count(row):
                return DataQuality(
                    survivorship_bias=True,
                    future_function=base.future_function,
                    filing_delay=base.filing_delay,
                    ungated_fundamentals=base.ungated_fundamentals,
                )

            # Evidence must match: (provider, ticker, effective_date)
            ticker = normalize_cn_ticker(row.get("ticker") or row.get("code"))
            eff_date = row.get("effective_date", "")
            if (provider, ticker, eff_date) not in evidence_keys:
                return DataQuality(
                    survivorship_bias=True,
                    future_function=base.future_function,
                    filing_delay=base.filing_delay,
                    ungated_fundamentals=base.ungated_fundamentals,
                )

        # Mixed providers (accepted + unknown) → blocked
        if seen_providers - self.ACCEPTED_HISTORICAL_PROVIDERS:
            return DataQuality(
                survivorship_bias=True,
                future_function=base.future_function,
                filing_delay=base.filing_delay,
                ungated_fundamentals=base.ungated_fundamentals,
            )

        # Period coverage checks per provider
        if "tushare:index_weight" in seen_providers:
            evidence_dates = self._tushare_evidence_dates()
            if not evidence_dates:
                return DataQuality(survivorship_bias=True,
                                   future_function=base.future_function,
                                   filing_delay=base.filing_delay,
                                   ungated_fundamentals=base.ungated_fundamentals)
            min_ev = min(evidence_dates)
            max_ev = max(evidence_dates)
            if min_ev > start_date or not self._within_tushare_carry_forward(max_ev, end_date):
                return DataQuality(survivorship_bias=True,
                                   future_function=base.future_function,
                                   filing_delay=base.filing_delay,
                                   ungated_fundamentals=base.ungated_fundamentals)

        if "qlib:instruments" in seen_providers:
            if not self._qlib_interval_covers(start_date, end_date):
                return DataQuality(survivorship_bias=True,
                                   future_function=base.future_function,
                                   filing_delay=base.filing_delay,
                                   ungated_fundamentals=base.ungated_fundamentals)

        return base

    def get_universe(self, as_of_date: str) -> List[str]:
        return sorted({r["ticker"] for r in self._universe_rows if self._active(r, as_of_date)})

    def get_price_universe(self, start_date: str, end_date: str) -> List[str]:
        return sorted({r["ticker"] for r in self._universe_rows if self._overlaps(r, start_date, end_date)})

    def get_fundamentals(self, tickers, as_of_date):
        result = {}
        for ticker in (normalize_cn_ticker(t) for t in tickers):
            visible = [
                rec for rec in self._fundamental_records.get(ticker, [])
                if rec.is_visible_as_of(as_of_date)
            ]
            if visible:
                result[ticker] = visible[0].to_dict()
        return result

    def get_price_history(self, tickers, start, end):
        if not self.prices_path.exists():
            raise FileNotFoundError(
                f"CN_PIT_FileSource requires prices.csv at {self.prices_path}. "
                "No live yfinance fallback is permitted for PIT backtesting."
            )
        prices = pd.read_csv(self.prices_path)
        date_col = "date" if "date" in prices.columns else "Date"
        if date_col not in prices.columns:
            raise ValueError(f"{self.prices_path}: missing date column")
        prices[date_col] = pd.to_datetime(prices[date_col])
        prices = prices.set_index(date_col).sort_index()
        cols = [t for t in tickers if t in prices.columns]
        prices = prices.loc[(prices.index >= start) & (prices.index <= end), cols]
        return prices

    def price_data_coverage_for_period(self, start_date: str, end_date: str) -> Dict:
        """Column and non-null price coverage for active universe endpoints."""
        if not self.prices_path.exists():
            return {"ok": False, "reason": f"missing_prices:{self.prices_path}"}
        prices = pd.read_csv(self.prices_path)
        date_col = "date" if "date" in prices.columns else "Date"
        if date_col not in prices.columns:
            return {"ok": False, "reason": "missing_date_column"}
        prices[date_col] = pd.to_datetime(prices[date_col])
        prices = prices.set_index(date_col).sort_index()
        if prices.empty:
            return {"ok": False, "reason": "empty_prices"}

        start_ts = prices.index[prices.index >= pd.Timestamp(start_date)]
        end_ts = prices.index[prices.index <= pd.Timestamp(end_date)]
        if len(start_ts) == 0 or len(end_ts) == 0:
            return {"ok": False, "reason": "period_outside_price_range"}

        start_px_date = start_ts[0]
        end_px_date = end_ts[-1]
        price_cols = set(prices.columns)

        def coverage_at(px_date: pd.Timestamp) -> Dict:
            as_of = px_date.strftime("%Y-%m-%d")
            active = set(self.get_universe(as_of))
            row = prices.loc[px_date]
            if isinstance(row, pd.DataFrame):
                row = row.iloc[0]
            data_cols = {c for c in price_cols if pd.notna(row.get(c))}
            col_covered = active & price_cols
            data_covered = active & data_cols
            return {
                "date": as_of,
                "active": len(active),
                "column_covered": len(col_covered),
                "data_covered": len(data_covered),
                "missing_columns": sorted(active - price_cols),
                "missing_data": sorted(col_covered - data_covered),
            }

        start_cov = coverage_at(start_px_date)
        end_cov = coverage_at(end_px_date)
        ok = (
            start_cov["active"] == start_cov["column_covered"] == start_cov["data_covered"]
            and end_cov["active"] == end_cov["column_covered"] == end_cov["data_covered"]
        )
        return {"ok": ok, "start": start_cov, "end": end_cov}


# ---- Value Ranker ----
@dataclass
class ValueScore:
    ticker: str
    roe_score: float = 0.0
    fcf_score: float = 0.0
    leverage_score: float = 0.0
    dividend_score: float = 0.0
    value_score: float = 0.0
    total: float = 0.0

    @classmethod
    def from_fundamentals(cls, ticker, fundamentals):
        f = fundamentals.get(ticker, {})
        if not f: return None
        roe = f.get("roe"); fcf_y = f.get("fcf_yield")
        de = f.get("debt_to_equity"); div_y = f.get("dividend_yield")
        pe = f.get("pe_ratio"); pb = f.get("pb_ratio")
        available = sum(v is not None for v in [roe, fcf_y, de])
        if available < 2: return None
        roe_score = min(max((roe or 0) / 25, 0), 1) * 0.30
        fcf_score = min(max((fcf_y or 0) / 8, 0), 1) * 0.25
        lev_score = max(0, min(1, (2.0 - (de or 2.0)) / 2.0)) * 0.15
        div_score = min(max((div_y or 0) / 6, 0), 1) * 0.10
        val = 0.20
        if pe and pe > 0: val *= min(max(25 / pe, 0.5), 1.5)
        if pb and pb > 0: val *= min(max(3 / pb, 0.5), 1.5)
        val_score = min(val, 0.25)
        total = roe_score + fcf_score + lev_score + div_score + val_score
        return cls(ticker=ticker, roe_score=roe_score, fcf_score=fcf_score,
                   leverage_score=lev_score, dividend_score=div_score,
                   value_score=val_score, total=total)


# ---- Backtest Engine ----
@dataclass
class BacktestResult:
    equity_curve: pd.Series
    trades: List[Dict]
    metrics: Dict
    can_deploy: bool
    deploy_blockers: List[str]
    data_quality: DataQuality = field(default_factory=DataQuality)

    @property
    def n_days(self): return len(self.equity_curve)
    @property
    def n_trades(self): return len([t for t in self.trades if t["action"] == "sell"])


def run_fundamental_backtest(
    data_source: DataSource,
    start_date: str,
    end_date: str,
    universe: Optional[List[str]] = None,
    capital: float = 500000,
    top_n: int = 8,
    max_position_pct: float = 0.10,
    stop_loss_pct: float = 0.05,
    take_profit_pct: float = 0.18,
    rebalance_freq_days: int = 63,
    quality_filter: float = 0.40,
) -> BacktestResult:

    if universe is None:
        universe = data_source.get_price_universe(start_date, end_date)

    all_tickers = list(universe) + [HS300_TICKER]
    prices = data_source.get_price_history(all_tickers, start_date, end_date)
    trading_dates = prices.index

    cash = capital
    positions: Dict[str, Dict] = {}
    trades: List[Dict] = []
    equity = []
    total_fees = 0.0
    total_slippage = 0.0
    last_rebalance_idx = -999
    hs300_col = HS300_TICKER
    hs300_base = None
    hs300_last = None

    for idx, dt in enumerate(trading_dates):
        date_str = dt.strftime("%Y-%m-%d")
        day_prices = prices.iloc[idx]

        if hs300_col in day_prices.index:
            hs300_val = day_prices[hs300_col]
            if pd.notna(hs300_val):
                hs300_last = hs300_val
            if hs300_base is None and pd.notna(hs300_val):
                hs300_base = hs300_val

        # Exit
        for ticker in list(positions):
            pos = positions[ticker]
            px = day_prices.get(ticker, pos["avg_cost"])
            if isinstance(px, float) and math.isnan(px): continue
            ret = px / pos["avg_cost"] - 1
            held_days = idx - pos["entry_idx"]
            if ret >= take_profit_pct or ret <= -stop_loss_pct or held_days >= 252:
                sell_shares = pos["shares"]
                amount = px * sell_shares
                commission = max(amount * COMMISSION_RATE, 5)
                stamp_tax = amount * STAMP_TAX_RATE
                transfer_fee = amount * TRANSFER_FEE_RATE
                slippage = amount * SLIPPAGE_BPS / 10000
                net = amount - commission - stamp_tax - transfer_fee - slippage
                pnl = net - pos["avg_cost"] * sell_shares
                cash += net
                total_fees += commission + stamp_tax + transfer_fee
                total_slippage += slippage
                trades.append({
                    "date": date_str, "action": "sell", "ticker": ticker,
                    "price": px, "shares": sell_shares, "amount": amount,
                    "pnl": pnl, "pnl_pct": ret * 100,
                    "commission": commission, "stamp_tax": stamp_tax,
                    "slippage": slippage,
                    "exit_reason": "tp" if ret >= take_profit_pct else ("sl" if ret <= -stop_loss_pct else "time"),
                    "held_days": held_days,
                })
                del positions[ticker]

        # Rebalance
        if idx - last_rebalance_idx >= rebalance_freq_days:
            as_of = date_str
            # Re-fetch universe for current as_of date (H16 fix)
            live_universe = data_source.get_universe(as_of)
            # Only use tickers present in pre-fetched price data
            scoped = [t for t in live_universe if t in prices.columns]
            fundamentals = data_source.get_fundamentals(scoped, as_of)
            scores = []
            for t in scoped:
                vs = ValueScore.from_fundamentals(t, fundamentals)
                if vs and vs.total >= quality_filter and t not in positions:
                    scores.append(vs)
            scores.sort(key=lambda x: x.total, reverse=True)
            target_tickers = [s.ticker for s in scores[:top_n]]

            invested = 0.0
            for ticker, pos in positions.items():
                px = day_prices.get(ticker, pos["avg_cost"])
                invested += pos["shares"] * (px if not math.isnan(px) else pos["avg_cost"])

            for ticker in list(positions):
                if ticker not in target_tickers:
                    pos = positions[ticker]
                    px = day_prices.get(ticker, pos["avg_cost"])
                    sell_shares = pos["shares"]
                    amount = px * sell_shares
                    commission = max(amount * COMMISSION_RATE, 5)
                    stamp_tax = amount * STAMP_TAX_RATE
                    transfer_fee = amount * TRANSFER_FEE_RATE
                    slippage = amount * SLIPPAGE_BPS / 10000
                    net = amount - commission - stamp_tax - transfer_fee - slippage
                    pnl = net - pos["avg_cost"] * sell_shares
                    cash += net
                    total_fees += commission + stamp_tax + transfer_fee
                    total_slippage += slippage
                    trades.append({
                        "date": date_str, "action": "sell", "ticker": ticker,
                        "price": px, "shares": sell_shares, "amount": amount,
                        "pnl": pnl, "pnl_pct": pnl / (pos["avg_cost"] * sell_shares) * 100,
                        "commission": commission, "stamp_tax": stamp_tax,
                        "slippage": slippage, "exit_reason": "rebalance_out",
                        "held_days": idx - pos["entry_idx"],
                    })
                    del positions[ticker]

            n_slots = top_n - len(positions)
            budget_per_slot = cash / max(n_slots, 1) if n_slots > 0 else 0
            for ticker in target_tickers:
                if ticker in positions: continue
                px = day_prices.get(ticker)
                if px is None or (isinstance(px, float) and math.isnan(px)): continue
                target_amount = min(capital * max_position_pct, budget_per_slot)
                if target_amount <= 0 or cash < target_amount: continue
                shares = int(target_amount / px / 100) * 100
                if shares <= 0: continue
                cost = px * shares
                commission = max(cost * COMMISSION_RATE, 5)
                transfer_fee = cost * TRANSFER_FEE_RATE
                slippage = cost * SLIPPAGE_BPS / 10000
                total_cost = cost + commission + transfer_fee + slippage
                if total_cost > cash: continue
                cash -= total_cost
                total_fees += commission + transfer_fee
                total_slippage += slippage
                positions[ticker] = {"shares": shares, "avg_cost": px,
                                     "entry_idx": idx, "buy_date": date_str}
                trades.append({
                    "date": date_str, "action": "buy", "ticker": ticker,
                    "price": px, "shares": shares, "amount": cost,
                    "commission": commission, "transfer_fee": transfer_fee,
                    "slippage": slippage, "total_cost": total_cost,
                })
            last_rebalance_idx = idx

        total_val = cash
        for ticker, pos in positions.items():
            px = day_prices.get(ticker, pos["avg_cost"])
            total_val += pos["shares"] * (px if not isinstance(px, float) or not math.isnan(px) else pos["avg_cost"])
        equity.append(total_val)

    eq = pd.Series(equity, index=trading_dates)
    returns = eq.pct_change().dropna()
    n_days = len(eq)
    total_ret = eq.iloc[-1] / eq.iloc[0] - 1 if n_days > 0 else 0
    annual_ret = ((1 + total_ret) ** (252 / n_days) - 1) if n_days > 0 else 0
    volatility = float(returns.std() * math.sqrt(252)) if len(returns) > 1 else 0
    sharpe = float(returns.mean() / returns.std() * math.sqrt(252)) if len(returns) > 1 and returns.std() > 0 else 0
    peak = eq.expanding().max()
    max_dd = float(((eq - peak) / peak).min())
    hs300_ret = (hs300_last / hs300_base - 1) if hs300_base and hs300_last else 0

    sells = [t for t in trades if t["action"] == "sell"]
    n_sells = len(sells)
    wins = [t for t in sells if t.get("pnl", 0) > 0]
    losses = [t for t in sells if t.get("pnl", 0) <= 0]
    win_rate = len(wins) / n_sells if n_sells > 0 else 0
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    profit_factor = gross_win / gross_loss if gross_loss > 0 else (float("inf") if gross_win > 0 else 0)
    buys = [t for t in trades if t["action"] == "buy"]

    # ---- Deployment Gate (H15+H24A) ----
    # Use period-aware data quality when available
    if hasattr(data_source, "data_quality_for_period"):
        dq = data_source.data_quality_for_period(start_date, end_date)
    else:
        dq = data_source.data_quality
    blockers = []
    if dq.survivorship_bias:
        blockers.append("data_quality: survivorship_bias=true")
    if dq.future_function:
        blockers.append("data_quality: future_function=true")
    if dq.filing_delay:
        blockers.append("data_quality: filing_delay=true")
    if dq.ungated_fundamentals:
        blockers.append("data_quality: ungated_fundamentals=true")
    if data_source.research_only:
        blockers.append("research_only: no deployment permitted")
    price_coverage = None
    price_coverage_checker = getattr(data_source, "price_data_coverage_for_period", None)
    if callable(price_coverage_checker):
        price_coverage = price_coverage_checker(start_date, end_date)
        if not price_coverage.get("ok"):
            start_cov = price_coverage.get("start", {})
            end_cov = price_coverage.get("end", {})
            if start_cov and end_cov:
                blockers.append(
                    "price_coverage: "
                    f"start_data={start_cov.get('data_covered')}/{start_cov.get('active')} "
                    f"end_data={end_cov.get('data_covered')}/{end_cov.get('active')}"
                )
            else:
                blockers.append(
                    f"price_coverage: {price_coverage.get('reason', 'unknown')}"
                )
    if n_days < MIN_TRADING_DAYS:
        blockers.append(f"insufficient_trading_days: {n_days} < {MIN_TRADING_DAYS}")
    if n_sells < MIN_TRADE_COUNT:
        blockers.append(f"insufficient_trades: {n_sells} < {MIN_TRADE_COUNT}")
    if total_ret < 0:
        blockers.append(f"negative_total_return: {total_ret*100:.2f}%")
    if sharpe < 0:
        blockers.append(f"negative_sharpe: {sharpe:.2f}")
    can_deploy = len(blockers) == 0

    return BacktestResult(
        equity_curve=eq, trades=trades,
        metrics={
            "total_return": total_ret, "annual_return": annual_ret,
            "volatility": volatility, "sharpe_ratio": sharpe,
            "max_drawdown": max_dd, "hs300_return": hs300_ret,
            "excess_return": total_ret - hs300_ret,
            "trade_count": n_sells, "buy_count": len(buys),
            "win_rate": win_rate,
            "profit_factor": profit_factor if profit_factor != float("inf") else 999.0,
            "avg_win": gross_win / len(wins) if wins else 0,
            "avg_loss": gross_loss / len(losses) if losses else 0,
            "total_turnover": sum(t["amount"] for t in trades),
            "total_fees": total_fees, "total_slippage": total_slippage,
            "avg_hold_days": sum(t.get("held_days", 0) for t in sells) / n_sells if n_sells else 0,
            "trading_days": n_days,
            "can_deploy": can_deploy, "deploy_blockers": blockers,
            "data_quality": dq.to_dict(),
            "price_coverage": price_coverage,
        },
        can_deploy=can_deploy, deploy_blockers=blockers, data_quality=dq,
    )


# ---- Strategy Variants ----
VALUE_STRATEGY_VARIANTS = {
    "quality_value": {"top_n": 8, "max_position_pct": 0.10, "stop_loss_pct": 0.05,
                      "take_profit_pct": 0.20, "quality_filter": 0.50},
    "deep_value": {"top_n": 10, "max_position_pct": 0.12, "stop_loss_pct": 0.08,
                   "take_profit_pct": 0.25, "quality_filter": 0.30},
    "deep_value_top8": {"top_n": 8, "max_position_pct": 0.12, "stop_loss_pct": 0.08,
                        "take_profit_pct": 0.25, "quality_filter": 0.30},
    "qarp": {"top_n": 8, "max_position_pct": 0.08, "stop_loss_pct": 0.06,
             "take_profit_pct": 0.15, "quality_filter": 0.55},
    "fcf_strength": {"top_n": 10, "max_position_pct": 0.06, "stop_loss_pct": 0.05,
                     "take_profit_pct": 0.18, "quality_filter": 0.45},
}


def build_value_data_source(
    data_source: str = "cn-yfinance",
    prices_path: Optional[str] = None,
    universe_path: Optional[str] = None,
    universe_snapshots_path: Optional[str] = None,
) -> DataSource:
    if data_source == "cn-yfinance":
        return CN_YFinanceSource()
    if data_source == "cn-pit":
        return CN_PIT_FileSource(
            prices_path=prices_path,
            universe_path=universe_path,
            universe_snapshots_path=universe_snapshots_path,
        )
    raise ValueError(f"Unsupported data source: {data_source}")


def run_all_value_backtests(
    start="2025-01-01",
    end="2026-05-19",
    data_source="cn-yfinance",
    prices_path: Optional[str] = None,
    universe_path: Optional[str] = None,
    universe_snapshots_path: Optional[str] = None,
):
    source = build_value_data_source(
        data_source=data_source,
        prices_path=prices_path,
        universe_path=universe_path,
        universe_snapshots_path=universe_snapshots_path,
    )
    results = {}
    for name, params in VALUE_STRATEGY_VARIANTS.items():
        print(f"  Running {name}...")
        result = run_fundamental_backtest(
            data_source=source,
            start_date=start, end_date=end, capital=500000, **params,
        )
        results[name] = result
        print(f"    Return: {result.metrics['total_return']*100:+.2f}%")
        print(f"    Sharpe: {result.metrics['sharpe_ratio']:+.2f}")
        print(f"    Trades: {result.metrics['trade_count']}")
        print(f"    Can Deploy: {result.can_deploy}")
        for b in result.deploy_blockers:
            print(f"      BLOCKED: {b}")
    return results


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2025-01-01")
    p.add_argument("--end", default="2026-05-19")
    p.add_argument("--output", type=str)
    p.add_argument("--compare", action="store_true")
    p.add_argument("--data-source", choices=["cn-yfinance", "cn-pit"], default="cn-yfinance")
    p.add_argument("--prices-file", default=None)
    p.add_argument("--universe-file", default=None)
    p.add_argument("--universe-snapshots-file", default=None)
    args = p.parse_args()

    if args.compare:
        results = run_all_value_backtests(
            args.start,
            args.end,
            data_source=args.data_source,
            prices_path=args.prices_file,
            universe_path=args.universe_file,
            universe_snapshots_path=args.universe_snapshots_file,
        )
        print("\n" + "=" * 70)
        print("  Value Strategy Comparison (Fundamentals-Driven)")
        print("=" * 70)
        print(f"{'Strategy':18s} {'Return':>8s} {'Sharpe':>7s} {'MaxDD':>7s} {'Win%':>6s} {'PF':>6s} {'Trades':>6s} {'Deploy':>7s}")
        print("-" * 70)
        for name, result in results.items():
            m = result.metrics
            print(f"{name:18s} {m['total_return']*100:>+7.2f}% {m['sharpe_ratio']:>6.2f} "
                  f"{m['max_drawdown']*100:>6.2f}% {m['win_rate']*100:>5.1f}% "
                  f"{m['profit_factor']:>5.2f} {m['trade_count']:>5d} "
                  f"{'YES' if result.can_deploy else 'NO':>7s}")
        if args.output:
            summary = {}
            for name, r in results.items():
                summary[name] = r.metrics
            with open(args.output, "w") as f:
                json.dump(summary, f, indent=2, ensure_ascii=False, default=str)
            print(f"\nSaved: {args.output}")
