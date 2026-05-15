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


class MarketDataProvider:
    name = "base"

    def get_close_prices(self, tickers: Iterable[str], start: str, end: str) -> ProviderResult:
        raise NotImplementedError


class StaticPriceProvider(MarketDataProvider):
    name = "static"

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
    name = "cache"

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
    name = "fallback"

    def __init__(self, providers: List[MarketDataProvider]):
        self.providers = providers

    def get_close_prices(self, tickers: Iterable[str], start: str, end: str) -> ProviderResult:
        wanted = list(tickers)
        remaining = list(wanted)
        combined = []
        sources_tried = []
        sources_used = {}
        cache_hits = []
        cache_ages = []
        adjustment = "qfq"

        for provider in self.providers:
            if not remaining:
                break
            result = provider.get_close_prices(remaining, start, end)
            sources_tried.extend(result.sources_tried or [provider.name])
            adjustment = result.adjustment or adjustment
            if result.cache_hit_ratio:
                cache_hits.append(result.cache_hit_ratio)
            if result.cache_oldest_age_days is not None:
                cache_ages.append(result.cache_oldest_age_days)
            if not result.prices.empty:
                combined.append(result.prices)
                sources_used.update(result.sources_used)
            remaining = [symbol for symbol in wanted if symbol not in sources_used]

        prices = pd.concat(combined, axis=1) if combined else pd.DataFrame()
        if not prices.empty:
            prices = prices.loc[:, [c for c in wanted if c in prices.columns]]
        missing = [symbol for symbol in wanted if symbol not in sources_used]
        return ProviderResult(
            status="OK" if not missing and not prices.empty else "INFRA_ERROR",
            reason=None if not missing and not prices.empty else "NO_PRICE_DATA",
            prices=prices,
            sources_tried=list(dict.fromkeys(sources_tried)),
            sources_used=sources_used,
            missing_symbols=missing,
            cache_hit_ratio=max(cache_hits) if cache_hits else 0.0,
            cache_oldest_age_days=max(cache_ages) if cache_ages else None,
            adjustment=adjustment,
        )


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
    name = "akshare"

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


class BaoStockProvider(MarketDataProvider):
    name = "baostock"

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


class TushareProvider(MarketDataProvider):
    name = "tushare"

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


class YFinanceProvider(MarketDataProvider):
    name = "yfinance"

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
