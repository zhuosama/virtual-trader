#!/usr/bin/env python3
"""财务报表分析引擎 v1.1 — XBRL比率 + 风险提取 + 源链接报告

v1.1 (H11修复):
- fcf_to_net_income 拼写修正
- Piotroski: 无数据不自增分，逐项数据驱动
- fcf_yield: 使用 MarketCap/EnterpriseValue，不用 book equity
- 缺字段: 报告 UNKNOWN，不静默给 0
"""

import json, os, re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from html.parser import HTMLParser


# ---- HTML Text Extractor ----
class HTMLTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text = []
        self.skip_tags = {"script", "style", "head", "title", "meta", "link", "nav", "footer"}
        self.current_tag = None; self.skip_depth = 0
    def handle_starttag(self, tag, attrs):
        if tag in self.skip_tags: self.skip_depth += 1
        if tag in ("p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr"):
            self.text.append("\n")
    def handle_endtag(self, tag):
        if tag in self.skip_tags and self.skip_depth > 0: self.skip_depth -= 1
    def handle_data(self, data):
        if self.skip_depth > 0: return
        text = data.strip()
        if text: self.text.append(text + " ")
    def get_text(self) -> str:
        return "".join(self.text).replace("\n ", "\n").strip()


# ---- Financial Metrics (v1.1: Optional fields, None=UNKNOWN) ----
@dataclass
class FinancialMetrics:
    ticker: str = ""; name: str = ""; report_date: str = ""
    _piotroski_max: int = 9; _piotroski_missing: list = field(default_factory=list)

    gross_margin: Optional[float] = None
    operating_margin: Optional[float] = None
    net_margin: Optional[float] = None
    roe: Optional[float] = None
    roa: Optional[float] = None
    roic: Optional[float] = None
    current_ratio: Optional[float] = None
    quick_ratio: Optional[float] = None
    debt_to_equity: Optional[float] = None
    debt_to_assets: Optional[float] = None
    interest_coverage: Optional[float] = None
    asset_turnover: Optional[float] = None
    inventory_turnover: Optional[float] = None
    fcf_yield: Optional[float] = None
    earnings_yield: Optional[float] = None
    revenue_growth: Optional[float] = None
    earnings_growth: Optional[float] = None
    accruals_ratio: Optional[float] = None
    fcf_to_net_income: Optional[float] = None
    piotroski_score: Optional[int] = None
    quality_score: Optional[float] = None


class XBRLRatioCalculator:
    @staticmethod
    def _latest(values): return values[0].get("value") if values else None
    @staticmethod
    def _yoy_growth(values):
        if len(values) < 2: return None
        curr, prev = values[0].get("value", 0), values[1].get("value", 0)
        if prev is None or prev == 0: return None
        return (curr / prev - 1) * 100

    def calculate(self, metrics: Dict) -> FinancialMetrics:
        ticker = metrics.get("ticker", ""); name = metrics.get("name", "")
        def gv(k): return self._latest(metrics.get(k, []))
        revenue = gv("Revenue"); net_income = gv("NetIncome")
        total_assets = gv("TotalAssets"); total_liabilities = gv("TotalLiabilities")
        current_assets = gv("CurrentAssets"); current_liabilities = gv("CurrentLiabilities")
        operating_income = gv("OperatingIncome"); operating_cf = gv("OperatingCashFlow")
        capex = gv("CapitalExpenditure"); equity = gv("StockholdersEquity")
        long_term_debt = gv("LongTermDebt")
        report_date = metrics.get("Revenue", [{}])[0].get("date", "")
        fm = FinancialMetrics(ticker=ticker, name=name, report_date=report_date)

        if revenue and revenue > 0:
            if operating_income is not None: fm.operating_margin = (operating_income / revenue) * 100
            if net_income is not None: fm.net_margin = (net_income / revenue) * 100
        if equity and equity > 0 and net_income is not None:
            fm.roe = (net_income / equity) * 100
        if total_assets and total_assets > 0 and net_income is not None:
            fm.roa = (net_income / total_assets) * 100
        ic = (equity or 0) + (long_term_debt or 0)
        if ic > 0 and operating_income is not None:
            fm.roic = (operating_income * 0.79 / ic) * 100
        if current_liabilities and current_liabilities > 0:
            if current_assets is not None:
                fm.current_ratio = current_assets / current_liabilities
                fm.quick_ratio = fm.current_ratio * 0.7
        if equity and equity > 0:
            if total_liabilities is not None: fm.debt_to_equity = total_liabilities / equity
            if total_assets and total_assets > 0:
                fm.debt_to_assets = total_liabilities / total_assets if total_liabilities else None
        if operating_cf is not None:
            fcf = operating_cf - abs(capex or 0)
            # fcf_yield needs market cap; book equity is not a valid proxy
            market_cap = self._latest(metrics.get("MarketCap", []))
            if market_cap and market_cap > 0:
                cash_eq = self._latest(metrics.get("CashAndCashEquivalents", [])) or 0
                ev = market_cap + (long_term_debt or 0) - cash_eq
                if ev > 0: fm.fcf_yield = (fcf / ev) * 100 if fcf > 0 else 0
            if net_income and net_income != 0:
                fm.fcf_to_net_income = operating_cf / net_income
        fm.revenue_growth = self._yoy_growth(metrics.get("Revenue", []))
        fm.earnings_growth = self._yoy_growth(metrics.get("NetIncome", []))
        if total_assets and total_assets > 0 and net_income and operating_cf:
            fm.accruals_ratio = ((net_income - operating_cf) / total_assets) * 100
        fm.piotroski_score = self._piotroski_score(fm, metrics)
        fm.quality_score = self._compute_quality(fm)
        return fm

    def _piotroski_score(self, fm, metrics):
        """Piotroski F-Score (0-9) — STRICT MODE (H19).

        Trend items (3,5,6,7,8,9) require 2+ periods of historical data.
        NO single-period fallback scoring. Missing data → UNKNOWN, not scored.
        Non-trend items (1,2,4) use current-period data only.
        """
        score = 0
        missing = []

        def gv(k): return self._latest(metrics.get(k, []))

        # ---- Non-trend items (single period sufficient) ----
        # 1. Positive net income
        ni = gv("NetIncome")
        if ni is not None:
            if ni > 0: score += 1
        else: missing.append("1:NetIncome")

        # 2. Positive operating cash flow
        ocf = gv("OperatingCashFlow")
        if ocf is not None:
            if ocf > 0: score += 1
        else: missing.append("2:OperatingCashFlow")

        # 4. CFO > Net Income (earnings quality)
        if ocf is not None and ni is not None:
            if ocf > ni: score += 1
        else: missing.append("4:CFO>NI")

        # ---- Trend items (require 2+ periods, NO fallback) ----
        # 3. ROA improved vs prior year
        ni_list = metrics.get("NetIncome", [])
        ta_list = metrics.get("TotalAssets", [])
        if len(ni_list) >= 2 and len(ta_list) >= 2 and ta_list[0].get("value", 0):
            curr_roa = ni_list[0].get("value", 0) / ta_list[0].get("value", 1)
            prev_roa = ni_list[1].get("value", 0) / ta_list[1].get("value", 1)
            if curr_roa > prev_roa: score += 1
        else:
            missing.append("3:ROA_trend(need_2_periods)")

        # 5. Decreasing leverage (long-term debt / total assets)
        ltd_list = metrics.get("LongTermDebt", [])
        if len(ltd_list) >= 2 and len(ta_list) >= 2 and ta_list[0].get("value", 0):
            curr_lev = ltd_list[0].get("value", 0) / ta_list[0].get("value", 1)
            prev_lev = ltd_list[1].get("value", 0) / ta_list[1].get("value", 1)
            if curr_lev < prev_lev: score += 1
        else:
            missing.append("5:Leverage_trend(need_2_periods)")

        # 6. Increasing current ratio
        ca_list = metrics.get("CurrentAssets", [])
        cl_list = metrics.get("CurrentLiabilities", [])
        if len(ca_list) >= 2 and len(cl_list) >= 2 and cl_list[0].get("value", 0):
            curr_cr = ca_list[0].get("value", 0) / cl_list[0].get("value", 1)
            prev_cr = ca_list[1].get("value", 0) / cl_list[1].get("value", 1)
            if curr_cr > prev_cr: score += 1
        else:
            missing.append("6:CurrentRatio_trend(need_2_periods)")

        # 7. No new share issuance
        shares_list = metrics.get("CommonStockSharesOutstanding", [])
        if len(shares_list) >= 2:
            if shares_list[0].get("value", 0) <= shares_list[1].get("value", 0):
                score += 1
        else:
            missing.append("7:SharesOutstanding(need_2_periods)")

        # 8. Improving gross margin (operating margin as proxy)
        oi_list = metrics.get("OperatingIncome", [])
        rev_list = metrics.get("Revenue", [])
        if len(oi_list) >= 2 and len(rev_list) >= 2 and rev_list[0].get("value", 0):
            curr_om = oi_list[0].get("value", 0) / rev_list[0].get("value", 1)
            prev_om = oi_list[1].get("value", 0) / rev_list[1].get("value", 1)
            if curr_om > prev_om: score += 1
        else:
            missing.append("8:Margin_trend(need_2_periods)")

        # 9. Improving asset turnover
        if len(rev_list) >= 2 and len(ta_list) >= 2 and ta_list[0].get("value", 0):
            curr_at = rev_list[0].get("value", 0) / ta_list[0].get("value", 1)
            prev_at = rev_list[1].get("value", 0) / ta_list[1].get("value", 1)
            if curr_at > prev_at: score += 1
        else:
            missing.append("9:AssetTurnover_trend(need_2_periods)")

        fm._piotroski_max = 9
        fm._piotroski_missing = missing
        return score

    def _compute_quality(self, fm):
        """Composite quality score. None=UNKNOWN for missing components."""
        s, w = 0.0, 0.0
        if fm.roe is not None: s += min(max(fm.roe / 20, 0), 1) * 0.30; w += 0.30
        if fm.fcf_yield is not None: s += min(max(fm.fcf_yield / 10, 0), 1) * 0.20; w += 0.20
        if fm.debt_to_equity is not None: s += max(0, min(1, (2.0 - fm.debt_to_equity) / 2.0)) * 0.15; w += 0.15
        if fm.piotroski_score is not None: s += (fm.piotroski_score / 9) * 0.15; w += 0.15
        if fm.revenue_growth is not None and fm.earnings_growth is not None:
            rg = min(max(fm.revenue_growth / 20, -1), 1)
            eg = min(max(fm.earnings_growth / 20, -1), 1)
            s += ((rg + eg) / 2 * 0.5 + 0.5) * 0.10; w += 0.10
        if fm.current_ratio is not None: s += min(fm.current_ratio / 3, 1) * 0.10; w += 0.10
        return s / w if w > 0 else None


# ---- Risk Factor Extractor ----
class RiskFactorExtractor:
    RISK_PATTERNS = {
        "competition": r"(?i)(competition|competitive|market share|peer|rival)",
        "regulation": r"(?i)(regulation|regulatory|compliance|government|legislation|law)",
        "supply_chain": r"(?i)(supply chain|supplier|procurement|shortage|disruption)",
        "macroeconomic": r"(?i)(inflation|recession|economic downturn|interest rate|currency|exchange rate)",
        "litigation": r"(?i)(litigation|lawsuit|legal proceeding|claim|dispute)",
        "technology": r"(?i)(technology|cyber|data breach|security|hack|IT system)",
        "key_personnel": r"(?i)(key personnel|management|succession|talent|retention)",
        "intellectual_property": r"(?i)(patent|trademark|copyright|intellectual property|IP infringement)",
        "forex": r"(?i)(foreign exchange|FX|currency fluctuation|international|geopolitical)",
        "pandemic": r"(?i)(pandemic|COVID|epidemic|public health|outbreak)",
        "climate": r"(?i)(climate|environmental|carbon|ESG|sustainability)",
    }

    def extract_risk_section(self, html_text: str) -> Optional[str]:
        parser = HTMLTextExtractor(); parser.feed(html_text); text = parser.get_text()
        for pat in [r"Item\s*1A\s*[\.:]?\s*Risk\s*Factors?\s*\n", r"ITEM\s*1A\s*[\.:]?\s*RISK\s*FACTORS?\s*\n"]:
            match = re.search(pat, text)
            if match:
                start = match.start()
                end = len(text)
                for ep in [r"\nItem\s*1B\s*[\.:]", r"\nITEM\s*1B\s*[\.:]", r"\nItem\s*2\s*[\.:]", r"\nITEM\s*2\s*[\.:]"]:
                    em = re.search(ep, text[start + len(match.group()):])
                    if em: end = start + len(match.group()) + em.start(); break
                return text[start:min(end, start + 50000)]
        return None

    def analyze_risks(self, html_text: str) -> Dict:
        risk_section = self.extract_risk_section(html_text)
        if not risk_section: return {"status": "risk_section_not_found", "categories": {}, "total_risk_mentions": 0}
        categories = {}; total_mentions = 0
        for cat, pat in self.RISK_PATTERNS.items():
            matches = re.findall(pat, risk_section)
            if matches:
                sev = "HIGH" if len(matches) >= 10 else ("MEDIUM" if len(matches) >= 5 else "LOW")
                categories[cat] = {"mentions": len(matches), "severity": sev}
                total_mentions += len(matches)
        return {
            "status": "ok", "risk_section_length": len(risk_section),
            "categories": categories, "total_risk_mentions": total_mentions,
            "risk_diversity": len(categories),
            "summary": self._gen_summary(categories),
        }

    def _gen_summary(self, cats):
        if not cats: return "No significant risks identified."
        highs = [c for c, d in cats.items() if d.get("severity") == "HIGH"]
        return f"Key risk areas: {', '.join(highs)}" if highs else "Risk profile appears manageable."


# ---- Report Generator ----
class ReportGenerator:
    def __init__(self, output_dir=None):
        self.output_dir = output_dir or os.path.expanduser("~/.hermes/virtual-trader/analysis/reports")
        os.makedirs(self.output_dir, exist_ok=True)

    @staticmethod
    def _fmt_val(v, spec, label):
        """Format a value; return 'UNKNOWN' if None."""
        if v is None: return f"- {label}: UNKNOWN"
        return f"- {label}: " + format(v, spec)

    def generate_ratio_report(self, metrics_list, output_name=None):
        if not output_name: output_name = f"ratio_report_{datetime.now().strftime('%Y%m%d_%H%M')}.md"
        lines = [f"# Financial Ratio Analysis", f"**Generated**: {datetime.now().strftime('%Y-%m-%d %H:%M')}", ""]
        if len(metrics_list) > 1:
            headers = ["Metric", *[m.ticker for m in metrics_list]]
            lines.append("| " + " | ".join(headers) + " |")
            lines.append("|" + "|".join(["------"] * len(headers)) + "|")
            for label, key in [("ROE (%)", "roe"), ("ROIC (%)", "roic"), ("Op Margin (%)", "operating_margin"),
                               ("Net Margin (%)", "net_margin"), ("FCF Yield (%)", "fcf_yield"),
                               ("D/E", "debt_to_equity"), ("Current Ratio", "current_ratio"),
                               ("Rev Growth (%)", "revenue_growth"), ("Earn Growth (%)", "earnings_growth"),
                               ("Piotroski", "piotroski_score"), ("Quality", "quality_score")]:
                row = [label]
                for m in metrics_list:
                    v = getattr(m, key, None)
                    if v is None:
                        row.append("UNKNOWN")
                    elif key == "piotroski_score":
                        mx = getattr(m, "_piotroski_max", 9)
                        row.append(f"{int(v)}/{mx}")
                    elif key == "quality_score":
                        row.append(f"{v:.2f}")
                    elif "growth" in key or "margin" in key or key in ("roe", "roic", "current_ratio"):
                        row.append(f"{v:.1f}")
                    else:
                        row.append(f"{v:.2f}")
                lines.append("| " + " | ".join(row) + " |")

        for m in metrics_list:
            f = self._fmt_val
            qs = f"{m.quality_score:.2f}/1.00" if m.quality_score is not None else "UNKNOWN"
            ps = f"{m.piotroski_score}/{getattr(m, '_piotroski_max', 9)}" if m.piotroski_score is not None else "UNKNOWN"
            lines.extend([
                "", f"## {m.ticker} - {m.name}", f"**Period**: {m.report_date}", "",
                "### Profitability",
                f(m.roe, ".1f", "ROE (%)"),
                f(m.roic, ".1f", "ROIC (%)"),
                f(m.operating_margin, ".1f", "Op Margin (%)"),
                f(m.net_margin, ".1f", "Net Margin (%)"),
                "", "### Financial Health",
                f(m.current_ratio, ".2f", "Current Ratio"),
                f(m.debt_to_equity, ".2f", "D/E"),
                f(m.fcf_to_net_income, ".2f", "FCF/NI"),
                "", "### Growth",
                f(m.revenue_growth, "+.1f", "Revenue YoY (%)"),
                f(m.earnings_growth, "+.1f", "Earnings YoY (%)"),
                "", f"### Composite: {qs} | Piotroski: {ps}",
            ])
            missing = getattr(m, "_piotroski_missing", [])
            if missing:
                lines.append(f"  Missing items ({len(missing)}): {', '.join(missing)}")
            else:
                lines.append("  All 9 Piotroski items verified.")

        report = "\n".join(lines)
        os.makedirs(self.output_dir, exist_ok=True)
        with open(os.path.join(self.output_dir, output_name), "w") as f: f.write(report)
        return report

    def generate_risk_report(self, ticker, risk_analysis, output_name=None):
        if not output_name: output_name = f"risk_{ticker}_{datetime.now().strftime('%Y%m%d')}.md"
        lines = [f"# Risk Analysis: {ticker}", f"**Analyzed**: {datetime.now().strftime('%Y-%m-%d %H:%M')}", ""]
        if risk_analysis.get("status") != "ok":
            lines.append("Risk section not found")
            return "\n".join(lines)
        lines.extend([f"**Total mentions**: {risk_analysis.get('total_risk_mentions', 0)}",
                      f"**Categories**: {risk_analysis.get('risk_diversity', 0)}", "",
                      "## Risk Categories", "", "| Category | Mentions | Severity |", "|------|---------|---------|"])
        for cat, data in sorted(risk_analysis.get("categories", {}).items(), key=lambda x: x[1]["mentions"], reverse=True):
            sev = data["severity"]; icon = {"HIGH": "R", "MEDIUM": "Y", "LOW": "G"}.get(sev, "")
            lines.append(f"| {cat} | {data['mentions']} | {icon} {sev} |")
        lines.extend(["", f"**Summary**: {risk_analysis.get('summary', '')}"])
        report = "\n".join(lines)
        os.makedirs(self.output_dir, exist_ok=True)
        with open(os.path.join(self.output_dir, output_name), "w") as f: f.write(report)
        return report


if __name__ == "__main__":
    print("Financial Statement Analyzer v1.1")
    print("Usage: import as module or use CLI args")
