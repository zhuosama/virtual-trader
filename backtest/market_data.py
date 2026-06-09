from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional

import pandas as pd


@dataclass
class ProviderResult:
    status: str
    prices: pd.DataFrame
    sources_tried: List[str] = field(default_factory=list)
    sources_used: Dict[str, str] = field(default_factory=dict)
    missing_symbols: List[str] = field(default_factory=list)
    cache_hit_ratio: float = 0.0
    cache_oldest_age_days: Optional[int] = None
    adjustment: str = "qfq"
    reason: Optional[str] = None
    fallback_reason: Optional[str] = None
    precheck_log: List[str] = field(default_factory=list)
    fallback_chain: List[str] = field(default_factory=list)


@dataclass
class OHLCVResult:
    status: str
    ohlcv: pd.DataFrame
    sources_tried: List[str] = field(default_factory=list)
    sources_used: Dict[str, str] = field(default_factory=dict)
    missing_pairs: List[tuple] = field(default_factory=list)
    fallback_chain: List[str] = field(default_factory=list)
    fallback_reason: Optional[str] = None
    precheck_log: List[str] = field(default_factory=list)
    sha256: Optional[str] = None
    adjustment: str = "qfq"
    reason: Optional[str] = None


@dataclass
class CacheEntryMeta:
    symbol: str
    provider: str
    frequency: str
    adjustment: str
    start: str
    end: str
    data_fetched_at: str


def to_plain_code(ticker: str) -> str:
    return ticker.split(".")[0]


def to_tushare_symbol(ticker: str) -> str:
    code = to_plain_code(ticker)
    if ticker.endswith(".SS"):
        return f"{code}.SH"
    if ticker.endswith(".SZ"):
        return f"{code}.SZ"
    raise ValueError(f"unsupported ticker for tushare: {ticker}")


def to_baostock_symbol(ticker: str) -> str:
    code = to_plain_code(ticker)
    if ticker.endswith(".SS"):
        return f"sh.{code}"
    if ticker.endswith(".SZ"):
        return f"sz.{code}"
    raise ValueError(f"unsupported ticker for baostock: {ticker}")


def cache_key(symbol: str, provider: str, frequency: str = "daily", adjustment: str = "qfq") -> str:
    safe_symbol = symbol.replace(".", "_")
    return f"{provider}_{safe_symbol}_{frequency}_{adjustment}"


def _parse_dt(value: str) -> datetime:
    text = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def is_cache_fresh(meta: CacheEntryMeta, today: Optional[datetime] = None) -> bool:
    today = today or datetime.now(timezone.utc)
    fetched_at = _parse_dt(meta.data_fetched_at)
    age_days = (today - fetched_at).days
    if meta.adjustment in ("qfq", "hfq"):
        return age_days <= 5
    return age_days <= 20


SOURCE_PROVIDERS: frozenset[str] = frozenset({
    "tushare:pro_bar:qfq",
    "tushare:daily",
    "akshare:stock_zh_a_hist",
    "akshare:stock_zh_a_hist_qfq",
    "baostock:query_history_k_data_plus",
    "yfinance:download",
    "static:in_memory",
    "cache:local_csv",
    "fallback:composite",
})


class LoaderBlockedError(RuntimeError):
    def __init__(self, provider: str, reason: str):
        self.provider = provider
        self.reason = reason
        super().__init__(f"[BLOCKER:loader] provider={provider} reason={reason}")


def compute_prices_sha256(prices: pd.DataFrame) -> str:
    """Stable sha256 over a price frame. Used for audit at write and at compare."""
    import hashlib

    payload = prices.to_csv(index=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def compute_ohlcv_sha256(ohlcv: pd.DataFrame) -> str:
    """Stable sha256 over an OHLV frame. Used for audit at write and at compare."""
    import hashlib

    frame = ohlcv.sort_values(by=["date", "ticker"]).reset_index(drop=True)
    payload = frame.to_csv(index=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class ChecksumMismatchError(RuntimeError):
    def __init__(self, expected: str, actual: str, context: str):
        super().__init__(
            f"[BLOCKER:checksum] context={context} expected={expected[:12]}... actual={actual[:12]}..."
        )


class MarketDataProvider:
    name = "base"

    def precheck(self) -> None:
        """Raise LoaderBlockedError if this provider can't run (missing token, missing library, missing endpoint).
        Default: no-op. Subclasses override."""
        return None

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if cls.name not in SOURCE_PROVIDERS:
            raise ValueError(
                f"provider name '{cls.name}' ({cls.__name__}) not in SOURCE_PROVIDERS"
            )

    def get_close_prices(self, tickers: Iterable[str], start: str, end: str) -> ProviderResult:
        raise NotImplementedError

    def get_ohlcv(self, tickers: Iterable[str], start: str, end: str) -> OHLCVResult:
        raise NotImplementedError(f"{self.name} does not implement get_ohlcv")


class StaticPriceProvider(MarketDataProvider):
    name = "static:in_memory"

    def __init__(self, prices: pd.DataFrame):
        self._prices = prices.copy()
        self._prices.index = pd.to_datetime(self._prices.index)

    def get_close_prices(self, tickers: Iterable[str], start: str, end: str) -> ProviderResult:
        wanted = list(tickers)
        frame = self._prices.loc[
            (self._prices.index >= pd.Timestamp(start))
            & (self._prices.index <= pd.Timestamp(end))
        ]
        available = [t for t in wanted if t in frame.columns and not frame[t].dropna().empty]
        missing = [t for t in wanted if t not in available]
        out = frame[available].copy() if available else pd.DataFrame(index=frame.index)
        return ProviderResult(
            status="OK" if not missing and not out.empty else "INFRA_ERROR",
            reason=None if not missing and not out.empty else "NO_PRICE_DATA",
            prices=out,
            sources_tried=[self.name],
            sources_used={t: self.name for t in available},
            missing_symbols=missing,
        )


class CachedPriceProvider(MarketDataProvider):
    name = "cache:local_csv"

    def __init__(self, cache_dir: str, adjustment: str = "qfq", today: Optional[datetime] = None):
        self.cache_dir = cache_dir
        self.adjustment = adjustment
        self.today = today

    def _paths(self, symbol: str):
        key = cache_key(symbol, self.name, "daily", self.adjustment)
        return (
            os.path.join(self.cache_dir, f"{key}.csv"),
            os.path.join(self.cache_dir, f"{key}.meta.json"),
        )

    def get_close_prices(self, tickers: Iterable[str], start: str, end: str) -> ProviderResult:
        wanted = list(tickers)
        frames = []
        sources_used = {}
        missing = []
        ages = []
        today = self.today or datetime.now(timezone.utc)

        for symbol in wanted:
            csv_path, meta_path = self._paths(symbol)
            if not os.path.exists(csv_path) or not os.path.exists(meta_path):
                missing.append(symbol)
                continue
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = CacheEntryMeta(**json.load(f))
            fetched_at = _parse_dt(meta.data_fetched_at)
            ages.append((today - fetched_at).days)
            if not is_cache_fresh(meta, today):
                missing.append(symbol)
                continue
            series = pd.read_csv(csv_path, parse_dates=["date"]).set_index("date")["close"].rename(symbol)
            window = series.loc[(series.index >= pd.Timestamp(start)) & (series.index <= pd.Timestamp(end))]
            if window.empty:
                missing.append(symbol)
                continue
            frames.append(window)
            sources_used[symbol] = self.name

        prices = pd.concat(frames, axis=1) if frames else pd.DataFrame()
        hit_ratio = len(sources_used) / len(wanted) if wanted else 0.0
        return ProviderResult(
            status="OK" if not missing and not prices.empty else "INFRA_ERROR",
            reason=None if not missing and not prices.empty else "NO_PRICE_DATA",
            prices=prices,
            sources_tried=[self.name],
            sources_used=sources_used,
            missing_symbols=missing,
            cache_hit_ratio=hit_ratio,
            cache_oldest_age_days=max(ages) if ages else None,
            adjustment=self.adjustment,
        )


class FallbackMarketDataProvider(MarketDataProvider):
    name = "fallback:composite"

    def __init__(self, providers: List[MarketDataProvider]):
        self.providers = providers

    def get_close_prices(self, tickers: Iterable[str], start: str, end: str) -> ProviderResult:
        import sys
        wanted = list(tickers)
        precheck_log: List[str] = []
        fallback_chain: List[str] = []
        fallback_reason: Optional[str] = None
        sources_tried: List[str] = []
        adjustment = "qfq"

        for provider in self.providers:
            fallback_chain.append(provider.name)

            # --- precheck ---
            try:
                provider.precheck()
            except LoaderBlockedError as e:
                entry = f"precheck-blocked={provider.name} reason={e.reason}"
                precheck_log.append(entry)
                print(f"[WARN loader] {entry}", file=sys.stderr)
                fallback_reason = f"precheck-blocked: {provider.name}"
                sources_tried.append(provider.name)
                continue

            # --- precheck passed: call provider ---
            result = provider.get_close_prices(wanted, start, end)
            sources_tried.extend(result.sources_tried or [provider.name])
            adjustment = result.adjustment or adjustment

            if not result.prices.empty:
                # First data-bearing provider → return immediately
                combined = [result.prices]
                prices = pd.concat(combined, axis=1)
                prices = prices.loc[:, [c for c in wanted if c in prices.columns]]
                missing = [symbol for symbol in wanted if symbol not in result.sources_used]
                sources_used = dict(result.sources_used)
                sources_used["__selected"] = provider.name
                sources_used["__sha256"] = compute_prices_sha256(prices)

                cache_hits = []
                cache_ages = []
                if result.cache_hit_ratio:
                    cache_hits.append(result.cache_hit_ratio)
                if result.cache_oldest_age_days is not None:
                    cache_ages.append(result.cache_oldest_age_days)

                return ProviderResult(
                    status="OK" if not missing else "INFRA_ERROR",
                    reason=None if not missing else "NO_PRICE_DATA",
                    prices=prices,
                    sources_tried=list(dict.fromkeys(sources_tried)),
                    sources_used=sources_used,
                    missing_symbols=missing,
                    cache_hit_ratio=max(cache_hits) if cache_hits else 0.0,
                    cache_oldest_age_days=max(cache_ages) if cache_ages else None,
                    adjustment=adjustment,
                    fallback_reason=fallback_reason,
                    precheck_log=precheck_log,
                    fallback_chain=fallback_chain,
                )
            else:
                # Empty data from a provider that passed precheck → valid fallback signal
                fallback_reason = f"empty-data: {provider.name}"
                # Continue to next provider

        # All providers exhausted
        raise LoaderBlockedError("fallback", f"all providers exhausted: {precheck_log}")

    def get_ohlcv(self, tickers: Iterable[str], start: str, end: str) -> OHLCVResult:
        """Mirror of get_close_prices fallback semantics — precheck loop,
        first-success-wins, STOP-on-exhausted via LoaderBlockedError.
        """
        import sys
        wanted = list(tickers)
        precheck_log: List[str] = []
        fallback_chain: List[str] = []
        fallback_reason: Optional[str] = None
        sources_tried: List[str] = []
        adjustment = "qfq"

        for provider in self.providers:
            fallback_chain.append(provider.name)

            # --- precheck ---
            try:
                provider.precheck()
            except LoaderBlockedError as e:
                entry = f"precheck-blocked={provider.name} reason={e.reason}"
                precheck_log.append(entry)
                print(f"[WARN loader] {entry}", file=sys.stderr)
                fallback_reason = f"precheck-blocked: {provider.name}"
                sources_tried.append(provider.name)
                continue

            # --- precheck passed: call provider ---
            result = provider.get_ohlcv(wanted, start, end)
            sources_tried.extend(result.sources_tried or [provider.name])
            adjustment = result.adjustment or adjustment

            if not result.ohlcv.empty:
                # First data-bearing provider → return immediately
                sha = compute_ohlcv_sha256(result.ohlcv)
                sources_used = dict(result.sources_used)
                sources_used["__selected"] = provider.name
                sources_used["__sha256"] = sha
                return OHLCVResult(
                    status=result.status if result.status == "INFRA_ERROR" else "OK",
                    ohlcv=result.ohlcv,
                    sources_tried=list(dict.fromkeys(sources_tried)),
                    sources_used=sources_used,
                    missing_pairs=list(result.missing_pairs),
                    fallback_chain=fallback_chain,
                    fallback_reason=fallback_reason,
                    precheck_log=precheck_log,
                    sha256=sha,
                    adjustment=adjustment,
                    reason=result.reason,
                )
            else:
                # Empty data from a provider that passed precheck → valid fallback signal
                fallback_reason = f"empty-data: {provider.name}"
                # Continue to next provider

        # All providers exhausted
        raise LoaderBlockedError("fallback", f"all OHLCV providers exhausted: {precheck_log}")


def _optional_import(module_name: str):
    try:
        return __import__(module_name)
    except ImportError:
        return None


def _with_retries(callable_, attempts: int = 2, delay: float = 0.5):
    last_exc = None
    for attempt in range(attempts):
        try:
            return callable_()
        except Exception as exc:
            last_exc = exc
            if attempt < attempts - 1:
                time.sleep(delay * (attempt + 1))
    raise last_exc


def _normalize_series_frame(series_by_symbol: Dict[str, pd.Series]) -> pd.DataFrame:
    if not series_by_symbol:
        return pd.DataFrame()
    return pd.concat(series_by_symbol.values(), axis=1)


class AkshareProvider(MarketDataProvider):
    name = "akshare:stock_zh_a_hist_qfq"

    def precheck(self) -> None:
        try:
            import akshare  # noqa: F401
        except ImportError:
            raise LoaderBlockedError(self.name, "akshare library not installed")

    def __init__(self, adjustment: str = "qfq"):
        self.adjustment = adjustment

    def get_close_prices(self, tickers: Iterable[str], start: str, end: str) -> ProviderResult:
        ak = _optional_import("akshare")
        wanted = list(tickers)
        if ak is None:
            return ProviderResult(
                status="INFRA_ERROR",
                reason="PROVIDER_UNAVAILABLE",
                prices=pd.DataFrame(),
                sources_tried=[self.name],
                missing_symbols=wanted,
                adjustment=self.adjustment,
            )

        series_by_symbol = {}
        missing = []
        for ticker in wanted:
            code = to_plain_code(ticker)

            def fetch_one():
                return ak.stock_zh_a_hist(
                    symbol=code,
                    period="daily",
                    start_date=start.replace("-", ""),
                    end_date=end.replace("-", ""),
                    adjust=self.adjustment,
                )

            try:
                data = _with_retries(fetch_one)
                if data.empty or "收盘" not in data.columns:
                    missing.append(ticker)
                    continue
                series = data.assign(date=pd.to_datetime(data["日期"])).set_index("date")["收盘"]
                series_by_symbol[ticker] = series.rename(ticker)
            except Exception:
                missing.append(ticker)

        prices = _normalize_series_frame(series_by_symbol)
        return ProviderResult(
            status="OK" if not missing and not prices.empty else "INFRA_ERROR",
            reason=None if not missing and not prices.empty else "NO_PRICE_DATA",
            prices=prices,
            sources_tried=[self.name],
            sources_used={symbol: self.name for symbol in series_by_symbol},
            missing_symbols=missing,
            adjustment=self.adjustment,
        )

    def get_ohlcv(self, tickers: Iterable[str], start: str, end: str) -> OHLCVResult:
        import sys as _sys
        ak = _optional_import("akshare")
        wanted = list(tickers)
        if ak is None:
            return OHLCVResult(
                status="INFRA_ERROR",
                reason="PROVIDER_UNAVAILABLE",
                ohlcv=pd.DataFrame(),
                sources_tried=[self.name],
                adjustment=self.adjustment,
            )

        frames: List[pd.DataFrame] = []
        missing_pairs: List[tuple] = []
        for ticker in wanted:
            code = to_plain_code(ticker)

            def fetch_one():
                return ak.stock_zh_a_hist(
                    symbol=code,
                    period="daily",
                    start_date=start.replace("-", ""),
                    end_date=end.replace("-", ""),
                    adjust=self.adjustment,
                )

            try:
                data = _with_retries(fetch_one)
                if data.empty:
                    print(
                        f"[WARN provider] akshare:stock_zh_a_hist_qfq ticker={ticker} reason=empty",
                        file=_sys.stderr,
                    )
                    missing_pairs.append((None, ticker))
                    continue
                # Normalize Akshare Chinese column names → canonical OHLCV
                data = data.rename(columns={
                    "日期": "date",
                    "开盘": "open",
                    "最高": "high",
                    "最低": "low",
                    "收盘": "close",
                    "成交量": "volume",
                    "成交额": "amount",
                })
                data["date"] = pd.to_datetime(data["date"])
                data["ticker"] = ticker
                ohlcv_cols = ["date", "ticker", "open", "high", "low", "close", "volume", "amount"]
                frames.append(data[ohlcv_cols])
            except Exception as exc:
                print(
                    f"[WARN provider] akshare:stock_zh_a_hist_qfq ticker={ticker} reason=exception:{exc}",
                    file=_sys.stderr,
                )
                missing_pairs.append((None, ticker))

        # Status: >50% of tickers failed → INFRA_ERROR (bulk fetch broken)
        fail_count = len(missing_pairs)
        total = len(wanted)
        if fail_count > total / 2:
            status = "INFRA_ERROR"
            reason = f"PER_TICKER_FAILURE: {fail_count}/{total} tickers failed"
        elif fail_count > 0:
            status = "OK"
            reason = f"PARTIAL_DATA: {fail_count}/{total} tickers missing"
        else:
            status = "OK"
            reason = None

        if not frames:
            return OHLCVResult(
                status="INFRA_ERROR",
                reason=reason or "NO_PRICE_DATA",
                ohlcv=pd.DataFrame(),
                sources_tried=[self.name],
                adjustment=self.adjustment,
                missing_pairs=missing_pairs,
            )

        ohlcv = pd.concat(frames, ignore_index=True).sort_values(["date", "ticker"]).reset_index(drop=True)
        sha = compute_ohlcv_sha256(ohlcv)
        successful = [t for t in wanted if (None, t) not in missing_pairs]
        return OHLCVResult(
            status=status,
            ohlcv=ohlcv,
            sources_tried=[self.name],
            sources_used={t: self.name for t in successful},
            missing_pairs=missing_pairs,
            sha256=sha,
            adjustment=self.adjustment,
            reason=reason,
        )


class BaoStockProvider(MarketDataProvider):
    name = "baostock:query_history_k_data_plus"

    def precheck(self) -> None:
        try:
            import baostock  # noqa: F401
        except ImportError:
            raise LoaderBlockedError(self.name, "baostock library not installed")

    def __init__(self, adjustment: str = "qfq"):
        self.adjustment = adjustment

    def get_close_prices(self, tickers: Iterable[str], start: str, end: str) -> ProviderResult:
        bs = _optional_import("baostock")
        wanted = list(tickers)
        if bs is None:
            return ProviderResult(
                status="INFRA_ERROR",
                reason="PROVIDER_UNAVAILABLE",
                prices=pd.DataFrame(),
                sources_tried=[self.name],
                missing_symbols=wanted,
                adjustment=self.adjustment,
            )

        series_by_symbol = {}
        missing = []
        try:
            bs.login()
            for ticker in wanted:
                try:
                    rs = bs.query_history_k_data_plus(
                        to_baostock_symbol(ticker),
                        "date,close",
                        start_date=start,
                        end_date=end,
                        frequency="d",
                        adjustflag="2" if self.adjustment == "qfq" else "1" if self.adjustment == "hfq" else "3",
                    )
                    rows = []
                    while rs.error_code == "0" and rs.next():
                        rows.append(rs.get_row_data())
                    if not rows:
                        missing.append(ticker)
                        continue
                    frame = pd.DataFrame(rows, columns=["date", "close"])
                    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
                    series_by_symbol[ticker] = frame.assign(date=pd.to_datetime(frame["date"])).set_index("date")["close"].rename(ticker)
                except Exception:
                    missing.append(ticker)
        finally:
            try:
                bs.logout()
            except Exception:
                pass

        prices = _normalize_series_frame(series_by_symbol)
        return ProviderResult(
            status="OK" if not missing and not prices.empty else "INFRA_ERROR",
            reason=None if not missing and not prices.empty else "NO_PRICE_DATA",
            prices=prices,
            sources_tried=[self.name],
            sources_used={symbol: self.name for symbol in series_by_symbol},
            missing_symbols=missing,
            adjustment=self.adjustment,
        )

    def get_ohlcv(self, tickers: Iterable[str], start: str, end: str) -> OHLCVResult:
        import sys as _sys
        bs = _optional_import("baostock")
        wanted = list(tickers)
        if bs is None:
            return OHLCVResult(
                status="INFRA_ERROR",
                reason="PROVIDER_UNAVAILABLE",
                ohlcv=pd.DataFrame(),
                sources_tried=[self.name],
                adjustment=self.adjustment,
            )

        frames: List[pd.DataFrame] = []
        missing_pairs: List[tuple] = []
        try:
            bs.login()
            for ticker in wanted:
                try:
                    rs = bs.query_history_k_data_plus(
                        to_baostock_symbol(ticker),
                        "date,code,open,high,low,close,volume,amount",
                        start_date=start,
                        end_date=end,
                        frequency="d",
                        adjustflag="2" if self.adjustment == "qfq" else "1" if self.adjustment == "hfq" else "3",
                    )
                    rows = []
                    while rs.error_code == "0" and rs.next():
                        rows.append(rs.get_row_data())
                    if not rows:
                        print(
                            f"[WARN provider] baostock:query_history_k_data_plus ticker={ticker} reason=empty",
                            file=_sys.stderr,
                        )
                        missing_pairs.append((None, ticker))
                        continue
                    frame = pd.DataFrame(rows, columns=["date", "code", "open", "high", "low", "close", "volume", "amount"])
                    for col in ["open", "high", "low", "close", "volume", "amount"]:
                        frame[col] = pd.to_numeric(frame[col], errors="coerce")
                    frame["date"] = pd.to_datetime(frame["date"])
                    frame["ticker"] = ticker
                    ohlcv_cols = ["date", "ticker", "open", "high", "low", "close", "volume", "amount"]
                    frames.append(frame[ohlcv_cols])
                except Exception as exc:
                    print(
                        f"[WARN provider] baostock:query_history_k_data_plus ticker={ticker} reason=exception:{exc}",
                        file=_sys.stderr,
                    )
                    missing_pairs.append((None, ticker))
        finally:
            try:
                bs.logout()
            except Exception:
                pass

        # Status: >50% of tickers failed → INFRA_ERROR (bulk fetch broken)
        fail_count = len(missing_pairs)
        total = len(wanted)
        if fail_count > total / 2:
            status = "INFRA_ERROR"
            reason = f"PER_TICKER_FAILURE: {fail_count}/{total} tickers failed"
        elif fail_count > 0:
            status = "OK"
            reason = f"PARTIAL_DATA: {fail_count}/{total} tickers missing"
        else:
            status = "OK"
            reason = None

        if not frames:
            return OHLCVResult(
                status="INFRA_ERROR",
                reason=reason or "NO_PRICE_DATA",
                ohlcv=pd.DataFrame(),
                sources_tried=[self.name],
                adjustment=self.adjustment,
                missing_pairs=missing_pairs,
            )

        ohlcv = pd.concat(frames, ignore_index=True).sort_values(["date", "ticker"]).reset_index(drop=True)
        sha = compute_ohlcv_sha256(ohlcv)
        successful = [t for t in wanted if (None, t) not in missing_pairs]
        return OHLCVResult(
            status=status,
            ohlcv=ohlcv,
            sources_tried=[self.name],
            sources_used={t: self.name for t in successful},
            missing_pairs=missing_pairs,
            sha256=sha,
            adjustment=self.adjustment,
            reason=reason,
        )


class TushareProvider(MarketDataProvider):
    name = "tushare:daily"

    def precheck(self) -> None:
        if not os.environ.get("TUSHARE_TOKEN"):
            raise LoaderBlockedError(self.name, "TUSHARE_TOKEN env var missing")

    def __init__(self, token: Optional[str] = None, adjustment: str = "qfq"):
        self.token = token or os.environ.get("TUSHARE_TOKEN")
        self.adjustment = adjustment

    def get_close_prices(self, tickers: Iterable[str], start: str, end: str) -> ProviderResult:
        ts = _optional_import("tushare")
        wanted = list(tickers)
        if ts is None or not self.token:
            return ProviderResult(
                status="INFRA_ERROR",
                reason="PROVIDER_UNAVAILABLE",
                prices=pd.DataFrame(),
                sources_tried=[self.name],
                missing_symbols=wanted,
                adjustment=self.adjustment,
            )

        pro = ts.pro_api(self.token)
        series_by_symbol = {}
        missing = []
        for ticker in wanted:
            try:
                frame = _with_retries(
                    lambda: pro.daily(
                        ts_code=to_tushare_symbol(ticker),
                        start_date=start.replace("-", ""),
                        end_date=end.replace("-", ""),
                    )
                )
                if frame.empty or "close" not in frame.columns:
                    missing.append(ticker)
                    continue
                series = frame.assign(date=pd.to_datetime(frame["trade_date"])).set_index("date")["close"].sort_index()
                series_by_symbol[ticker] = series.rename(ticker)
            except Exception:
                missing.append(ticker)

        prices = _normalize_series_frame(series_by_symbol)
        return ProviderResult(
            status="OK" if not missing and not prices.empty else "INFRA_ERROR",
            reason=None if not missing and not prices.empty else "NO_PRICE_DATA",
            prices=prices,
            sources_tried=[self.name],
            sources_used={symbol: self.name for symbol in series_by_symbol},
            missing_symbols=missing,
            adjustment=self.adjustment,
        )

    def get_ohlcv(self, tickers: Iterable[str], start: str, end: str) -> OHLCVResult:
        import sys as _sys
        ts = _optional_import("tushare")
        wanted = list(tickers)
        if ts is None or not self.token:
            return OHLCVResult(
                status="INFRA_ERROR",
                reason="PROVIDER_UNAVAILABLE",
                ohlcv=pd.DataFrame(),
                sources_tried=[self.name],
                adjustment=self.adjustment,
            )

        pro = ts.pro_api(self.token)
        frames: List[pd.DataFrame] = []
        missing_pairs: List[tuple] = []
        for ticker in wanted:
            try:
                frame = _with_retries(
                    lambda: pro.daily(
                        ts_code=to_tushare_symbol(ticker),
                        start_date=start.replace("-", ""),
                        end_date=end.replace("-", ""),
                    )
                )
                if frame.empty:
                    print(
                        f"[WARN provider] tushare:daily ticker={ticker} reason=empty",
                        file=_sys.stderr,
                    )
                    missing_pairs.append((None, ticker))
                    continue
                # Normalize Tushare schema → canonical OHLCV:
                #   trade_date → date, vol → volume, drop ts_code (use input ticker)
                frame = frame.rename(columns={"trade_date": "date", "vol": "volume"})
                frame["date"] = pd.to_datetime(frame["date"])
                frame["ticker"] = ticker
                ohlcv_cols = ["date", "ticker", "open", "high", "low", "close", "volume", "amount"]
                frames.append(frame[ohlcv_cols])
            except Exception as exc:
                print(
                    f"[WARN provider] tushare:daily ticker={ticker} reason=exception:{exc}",
                    file=_sys.stderr,
                )
                missing_pairs.append((None, ticker))

        # Status: >50% of tickers failed → INFRA_ERROR (bulk fetch broken)
        fail_count = len(missing_pairs)
        total = len(wanted)
        if fail_count > total / 2:
            status = "INFRA_ERROR"
            reason = f"PER_TICKER_FAILURE: {fail_count}/{total} tickers failed"
        elif fail_count > 0:
            status = "OK"
            reason = f"PARTIAL_DATA: {fail_count}/{total} tickers missing"
        else:
            status = "OK"
            reason = None

        if not frames:
            return OHLCVResult(
                status="INFRA_ERROR",
                reason=reason or "NO_PRICE_DATA",
                ohlcv=pd.DataFrame(),
                sources_tried=[self.name],
                adjustment=self.adjustment,
                missing_pairs=missing_pairs,
            )

        ohlcv = pd.concat(frames, ignore_index=True).sort_values(["date", "ticker"]).reset_index(drop=True)
        sha = compute_ohlcv_sha256(ohlcv)
        successful = [t for t in wanted if (None, t) not in missing_pairs]
        return OHLCVResult(
            status=status,
            ohlcv=ohlcv,
            sources_tried=[self.name],
            sources_used={t: self.name for t in successful},
            missing_pairs=missing_pairs,
            sha256=sha,
            adjustment=self.adjustment,
            reason=reason,
        )


class YFinanceProvider(MarketDataProvider):
    name = "yfinance:download"

    def precheck(self) -> None:
        try:
            import yfinance  # noqa: F401
        except ImportError:
            raise LoaderBlockedError(self.name, "yfinance library not installed")

    def get_close_prices(self, tickers: Iterable[str], start: str, end: str) -> ProviderResult:
        wanted = list(tickers)
        yf = _optional_import("yfinance")
        if yf is None:
            return ProviderResult(
                status="INFRA_ERROR",
                reason="PROVIDER_UNAVAILABLE",
                prices=pd.DataFrame(),
                sources_tried=[self.name],
                missing_symbols=wanted,
            )
        data = yf.download(wanted, start=start, end=end, progress=False)
        prices = data["Close"] if "Close" in data else pd.DataFrame()
        if isinstance(prices, pd.Series):
            prices = prices.to_frame(wanted[0])
        available = [t for t in wanted if t in prices.columns and not prices[t].dropna().empty]
        missing = [t for t in wanted if t not in available]
        return ProviderResult(
            status="OK" if not missing and not prices.empty else "INFRA_ERROR",
            reason=None if not missing and not prices.empty else "NO_PRICE_DATA",
            prices=prices[available] if available else pd.DataFrame(),
            sources_tried=[self.name],
            sources_used={t: self.name for t in available},
            missing_symbols=missing,
            adjustment="qfq",
        )

    def get_ohlcv(self, tickers: Iterable[str], start: str, end: str) -> OHLCVResult:
        """Return long-format OHLCV via yfinance daily download.

        NOTE: YFinance does NOT provide 成交额 (amount/turnover).  The ``amount``
        column is filled with NaN.  Callers that need amount should use a provider
        that supplies it (Tushare / Akshare / BaoStock).
        """
        import sys as _sys
        wanted = list(tickers)
        yf = _optional_import("yfinance")
        if yf is None:
            return OHLCVResult(
                status="INFRA_ERROR",
                reason="PROVIDER_UNAVAILABLE",
                ohlcv=pd.DataFrame(),
                sources_tried=[self.name],
            )

        data = yf.download(wanted, start=start, end=end, progress=False, interval="1d")
        if data.empty:
            return OHLCVResult(
                status="INFRA_ERROR",
                reason="NO_PRICE_DATA",
                ohlcv=pd.DataFrame(),
                sources_tried=[self.name],
                adjustment="qfq",
            )

        is_multi = isinstance(data.columns, pd.MultiIndex)
        frames: List[pd.DataFrame] = []
        missing_pairs: List[tuple] = []

        for ticker in wanted:
            try:
                if is_multi:
                    if ticker not in data.columns.get_level_values(1):
                        print(
                            f"[WARN provider] yfinance:download ticker={ticker} reason=empty",
                            file=_sys.stderr,
                        )
                        missing_pairs.append((None, ticker))
                        continue
                    row = pd.DataFrame({
                        "date": data.index,
                        "ticker": ticker,
                        "open": data[("Open", ticker)],
                        "high": data[("High", ticker)],
                        "low": data[("Low", ticker)],
                        "close": data[("Close", ticker)],
                        "volume": data[("Volume", ticker)],
                    })
                else:
                    # Single-ticker download → flat columns
                    row = pd.DataFrame({
                        "date": data.index,
                        "ticker": ticker,
                        "open": data["Open"],
                        "high": data["High"],
                        "low": data["Low"],
                        "close": data["Close"],
                        "volume": data["Volume"],
                    })
                # YFinance does NOT provide amount (成交额); fill with NaN
                row["amount"] = float("nan")
                row = row.dropna(subset=["open", "close"])
                if not row.empty:
                    frames.append(row.reset_index(drop=True))
                else:
                    print(
                        f"[WARN provider] yfinance:download ticker={ticker} reason=empty",
                        file=_sys.stderr,
                    )
                    missing_pairs.append((None, ticker))
            except Exception as exc:
                print(
                    f"[WARN provider] yfinance:download ticker={ticker} reason=exception:{exc}",
                    file=_sys.stderr,
                )
                missing_pairs.append((None, ticker))

        # Status: >50% of tickers failed → INFRA_ERROR (bulk fetch broken)
        fail_count = len(missing_pairs)
        total = len(wanted)
        if fail_count > total / 2:
            status = "INFRA_ERROR"
            reason = f"PER_TICKER_FAILURE: {fail_count}/{total} tickers failed"
        elif fail_count > 0:
            status = "OK"
            reason = f"PARTIAL_DATA: {fail_count}/{total} tickers missing"
        else:
            status = "OK"
            reason = None

        reason_note = "YFinance does not provide amount (成交额); amount column is NaN"
        if reason:
            reason = f"{reason}; {reason_note}"
        else:
            reason = reason_note

        if not frames:
            reason = reason or "NO_PRICE_DATA"
            return OHLCVResult(
                status="INFRA_ERROR",
                reason=reason,
                ohlcv=pd.DataFrame(),
                sources_tried=[self.name],
                adjustment="qfq",
                missing_pairs=missing_pairs,
            )

        ohlcv = pd.concat(frames, ignore_index=True).sort_values(["date", "ticker"]).reset_index(drop=True)
        sha = compute_ohlcv_sha256(ohlcv)
        successful = [t for t in wanted if (None, t) not in missing_pairs]
        return OHLCVResult(
            status=status,
            ohlcv=ohlcv,
            sources_tried=[self.name],
            sources_used={t: self.name for t in successful},
            missing_pairs=missing_pairs,
            sha256=sha,
            adjustment="qfq",
            reason=reason,
        )
