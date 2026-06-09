#!/usr/bin/env python3
"""SEC EDGAR Ingestion Module

提供：
1. Ticker → CIK 映射
2. Submissions API (公司提交历史)
3. CompanyFacts API (XBRL 财务数据)
4. 10-K / 10-Q 申报下载
5. Point-in-time 时间戳记录
"""

import json
import os
import re
import time
import urllib.request
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field


# ---- Constants ----
SEC_HEADERS = {
    "User-Agent": "ValueResearch/1.0 (research@example.com)",
    "Accept": "application/json",
}
CIK_LOOKUP_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_API = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
COMPANYFACTS_API = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
FILING_ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession_dash}/{primary_doc}"

CACHE_DIR = os.path.expanduser("~/.hermes/virtual-trader/sec_edgar/cache")


@dataclass
class CIKMapping:
    ticker: str
    cik: str
    name: str
    exchange: str = ""
    fetched_at: str = ""

    @property
    def cik_padded(self) -> str:
        return str(int(self.cik)).zfill(10)


@dataclass
class FilingRecord:
    accession: str
    form: str          # 10-K, 10-Q, 8-K, etc.
    filing_date: str
    report_date: str   # Period end date
    primary_doc: str
    items: str = ""
    fetched_at: str = ""

    @property
    def accession_dash(self) -> str:
        return self.accession.replace("-", "")

    @property
    def archive_url(self) -> str:
        cik = self.accession.split("-")[0]
        return (f"https://www.sec.gov/Archives/edgar/data/{cik}/"
                f"{self.accession_dash}/{self.primary_doc}")


class SECEdgarClient:
    """Client for SEC EDGAR API with caching."""

    def __init__(self, cache_dir: str = CACHE_DIR):
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)
        self._cik_map: Optional[Dict[str, CIKMapping]] = None

    def _rate_limit_delay(self):
        """SEC limits: 10 requests/second."""
        time.sleep(0.12)

    def _cache_path(self, key: str) -> str:
        return os.path.join(self.cache_dir, f"{key}.json")

    def _cache_meta_path(self, key: str) -> str:
        return os.path.join(self.cache_dir, f"{key}.meta.json")

    def _read_cache(self, key: str, max_age_hours: int = 24) -> Optional[Dict]:
        cache_file = self._cache_path(key)
        meta_file = self._cache_meta_path(key)
        if not os.path.exists(cache_file) or not os.path.exists(meta_file):
            return None
        with open(meta_file) as f:
            meta = json.load(f)
        fetched_at = datetime.fromisoformat(meta["fetched_at"])
        age_hours = (datetime.now(timezone.utc) - fetched_at).total_seconds() / 3600
        if age_hours > max_age_hours:
            return None
        with open(cache_file) as f:
            return json.load(f)

    def _write_cache(self, key: str, data, fetched_at: Optional[str] = None):
        cache_file = self._cache_path(key)
        meta_file = self._cache_meta_path(key)
        if fetched_at is None:
            fetched_at = datetime.now(timezone.utc).isoformat()
        with open(cache_file, "w") as f:
            json.dump(data, f, indent=2)
        with open(meta_file, "w") as f:
            json.dump({"fetched_at": fetched_at, "key": key}, f)

    def _fetch_json(self, url: str, retries: int = 3) -> Dict:
        for attempt in range(retries):
            try:
                self._rate_limit_delay()
                req = urllib.request.Request(url, headers=SEC_HEADERS)
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return json.loads(resp.read().decode())
            except Exception as e:
                if attempt == retries - 1:
                    raise e
                time.sleep(2 ** attempt)

    def fetch_cik_map(self, force_refresh: bool = False) -> Dict[str, CIKMapping]:
        """Fetch ticker→CIK mapping from SEC."""
        if self._cik_map and not force_refresh:
            return self._cik_map

        cache_key = "cik_map"
        if not force_refresh:
            cached = self._read_cache(cache_key, max_age_hours=168)  # 1 week
            if cached:
                self._cik_map = {}
                for tkr, d in cached.items():
                    self._cik_map[tkr] = CIKMapping(**d)
                return self._cik_map

        data = self._fetch_json(CIK_LOOKUP_URL)
        self._cik_map = {}
        cache_data = {}
        for cik_str, info in data.items():
            ticker = info.get("ticker", "").upper().strip()
            if not ticker:
                continue
            mapping = CIKMapping(
                ticker=ticker,
                cik=str(info.get("cik_str", cik_str)),
                name=info.get("title", ""),
                exchange=info.get("exchange", ""),
                fetched_at=datetime.now(timezone.utc).isoformat(),
            )
            self._cik_map[ticker] = mapping
            cache_data[ticker] = {
                "ticker": mapping.ticker, "cik": mapping.cik,
                "name": mapping.name, "exchange": mapping.exchange,
                "fetched_at": mapping.fetched_at,
            }

        self._write_cache(cache_key, cache_data)
        return self._cik_map

    def lookup_cik(self, ticker: str) -> Optional[CIKMapping]:
        """Get CIK for a ticker symbol."""
        cik_map = self.fetch_cik_map()
        return cik_map.get(ticker.upper())

    def get_submissions(self, cik: str, force_refresh: bool = False) -> Dict:
        """Get company submissions (all filings history)."""
        cache_key = f"submissions_{cik}"
        if not force_refresh:
            cached = self._read_cache(cache_key, max_age_hours=12)
            if cached:
                return cached

        url = SUBMISSIONS_API.format(cik=int(cik))
        data = self._fetch_json(url)
        self._write_cache(cache_key, data)
        return data

    def get_companyfacts(self, cik: str, force_refresh: bool = False) -> Dict:
        """Get XBRL company facts (all reported financial metrics)."""
        cache_key = f"companyfacts_{cik}"
        if not force_refresh:
            cached = self._read_cache(cache_key, max_age_hours=12)
            if cached:
                return cached

        url = COMPANYFACTS_API.format(cik=int(cik))
        data = self._fetch_json(url)
        self._write_cache(cache_key, data)
        return data

    def get_filings(self, ticker: str, forms: Optional[List[str]] = None,
                    limit: int = 20) -> List[FilingRecord]:
        """Get recent filings for a ticker, filtered by form type."""
        if forms is None:
            forms = ["10-K", "10-Q"]

        mapping = self.lookup_cik(ticker)
        if not mapping:
            return []

        submissions = self.get_submissions(mapping.cik)
        filings_data = submissions.get("filings", {}).get("recent", {})
        if not filings_data:
            return []

        records = []
        n = len(filings_data.get("form", []))
        for i in range(min(n, limit * 3)):  # Over-sample to allow filtering
            form = filings_data["form"][i] if i < len(filings_data["form"]) else ""
            if form not in forms:
                continue
            record = FilingRecord(
                accession=filings_data.get("accessionNumber", [""])[i],
                form=form,
                filing_date=filings_data.get("filingDate", [""])[i],
                report_date=filings_data.get("reportDate", [""])[i],
                primary_doc=filings_data.get("primaryDocument", [""])[i],
                items=filings_data.get("items", [""])[i] if i < len(filings_data.get("items", [""])) else "",
                fetched_at=datetime.now(timezone.utc).isoformat(),
            )
            records.append(record)
            if len(records) >= limit:
                break

        return records

    def download_filing(self, cik: str, accession: str,
                        primary_doc: str) -> Optional[bytes]:
        """Download a specific filing document."""
        accession_dash = accession.replace("-", "")
        url = FILING_ARCHIVE.format(
            cik=cik, accession_dash=accession_dash,
            primary_doc=primary_doc,
        )
        try:
            self._rate_limit_delay()
            req = urllib.request.Request(url, headers=SEC_HEADERS)
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read()
        except Exception as e:
            print(f"  Download failed: {e}")
            return None

    def download_latest_10k(self, ticker: str) -> Optional[Tuple[FilingRecord, bytes]]:
        """Download latest 10-K filing for a ticker."""
        filings = self.get_filings(ticker, forms=["10-K"], limit=1)
        if not filings:
            return None
        record = filings[0]
        mapping = self.lookup_cik(ticker)
        if not mapping:
            return None
        content = self.download_filing(mapping.cik, record.accession, record.primary_doc)
        if content:
            return record, content
        return None

    def get_annual_metrics(self, ticker: str) -> Dict:
        """Extract key annual metrics from companyfacts."""
        mapping = self.lookup_cik(ticker)
        if not mapping:
            return {"error": "CIK not found", "ticker": ticker}

        facts = self.get_companyfacts(mapping.cik)
        units = facts.get("facts", {}).get("us-gaap", {})
        if not units:
            return {"error": "No US-GAAP data", "ticker": ticker}

        # Map common financial concepts to their XBRL tags
        concept_map = {
            "Revenue": "Revenues",
            "NetIncome": "NetIncomeLoss",
            "TotalAssets": "Assets",
            "TotalLiabilities": "Liabilities",
            "CurrentAssets": "AssetsCurrent",
            "CurrentLiabilities": "LiabilitiesCurrent",
            "OperatingIncome": "OperatingIncomeLoss",
            "OperatingCashFlow": "NetCashProvidedByUsedInOperatingActivities",
            "CapitalExpenditure": "PaymentsToAcquirePropertyPlantAndEquipment",
            "StockholdersEquity": "StockholdersEquity",
            "LongTermDebt": "LongTermDebtNoncurrent",
            "EPS": "EarningsPerShareBasic",
            "Dividends": "CommonStockDividendsPerShareDeclared",
        }

        metrics = {"ticker": ticker, "cik": mapping.cik, "name": mapping.name}
        for metric_name, concept in concept_map.items():
            if concept not in units:
                continue
            unit_data = units[concept].get("units", {})
            # Get USD values, prefer annual filings
            usd_data = unit_data.get("USD", [])
            if not usd_data:
                continue

            # Get the last 3 annual values
            annual = [d for d in usd_data if d.get("form", "") == "10-K"]
            if not annual:
                annual = usd_data  # fallback to all

            # Sort by date descending
            annual.sort(key=lambda x: x.get("end", ""), reverse=True)
            metrics[metric_name] = [
                {"date": d.get("end", ""), "value": d["val"],
                 "fiscal_year": d.get("fy", ""), "fiscal_period": d.get("fp", ""),
                 "filed": d.get("filed", "")}
                for d in annual[:5]
            ]

        return metrics


# ---- CLI ----
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="SEC EDGAR 数据获取")
    parser.add_argument("ticker", nargs="?", help="股票代码 (e.g., AAPL)")
    parser.add_argument("--cik", action="store_true", help="查询CIK映射")
    parser.add_argument("--filings", action="store_true", help="获取申报列表")
    parser.add_argument("--facts", action="store_true", help="获取财务数据")
    parser.add_argument("--metrics", action="store_true", help="提取关键指标")
    parser.add_argument("--10k", action="store_true", help="下载最新10-K")
    parser.add_argument("--refresh", action="store_true", help="强制刷新缓存")
    parser.add_argument("--output", type=str, help="输出文件路径 (for --10k)")
    args = parser.parse_args()

    client = SECEdgarClient()

    if not args.ticker:
        print("Usage: sec_edgar.py TICKER [--cik|--filings|--facts|--metrics|--10k]")
        parser.print_help()
        exit(1)

    ticker = args.ticker.upper()

    if args.cik or not any([args.filings, args.facts, args.metrics, args._10k]):
        mapping = client.lookup_cik(ticker)
        if mapping:
            print(f"  {mapping.ticker}: CIK={mapping.cik}, Name={mapping.name}")
        else:
            print(f"  CIK not found for {ticker}")

    if args.filings:
        filings = client.get_filings(ticker, forms=["10-K", "10-Q"], limit=10)
        for f in filings:
            print(f"  {f.form} | {f.report_date} | filed: {f.filing_date} | {f.accession}")

    if args.facts or args.metrics:
        metrics = client.get_annual_metrics(ticker)
        if "error" in metrics:
            print(json.dumps(metrics, indent=2))
        else:
            for key, values in metrics.items():
                if key in ("ticker", "cik", "name"):
                    print(f"  {key}: {values}")
                elif isinstance(values, list):
                    latest = values[0] if values else {}
                    print(f"  {key}: {latest.get('value', 'N/A')} (as of {latest.get('date', 'N/A')})")

    if args._10k:
        result = client.download_latest_10k(ticker)
        if result:
            record, content = result
            print(f"  10-K: {record.filing_date} | {len(content):,} bytes")
            output_path = args.output or f"{ticker}_10k.html"
            with open(output_path, "wb") as f:
                f.write(content)
            print(f"  Saved: {output_path}")
        else:
            print(f"  No 10-K found for {ticker}")
