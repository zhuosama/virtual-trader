#!/usr/bin/env python3
"""Tests for H49a SW L1 industry classification ingestion."""

import json
import sys
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import h49a_build_tushare_sw_industry as h49a  # noqa: E402


class TestH49aTickerConversion(unittest.TestCase):
    def test_yahoo_to_tushare_ss(self):
        self.assertEqual(h49a.yahoo_to_tushare_code("600519.SS"), "600519.SH")

    def test_yahoo_to_tushare_sz(self):
        self.assertEqual(h49a.yahoo_to_tushare_code("000858.SZ"), "000858.SZ")

    def test_yahoo_to_tushare_invalid(self):
        with self.assertRaises(ValueError):
            h49a.yahoo_to_tushare_code("000001.XSHE")


class TestH49aUniverseLoading(unittest.TestCase):
    def test_load_tickers(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "universe.jsonl"
            rows = [
                {"ticker": "600519.SS", "weight": 5.0, "effective_date": "2020-01-01"},
                {"ticker": "000858.SZ", "weight": 3.0, "effective_date": "2020-01-01"},
                {"ticker": "600519.SS", "weight": 5.5, "effective_date": "2021-06-01"},
            ]
            path.write_text(
                "\n".join(json.dumps(row) for row in rows), encoding="utf-8"
            )

            tickers = h49a.load_universe_tickers(path)
            self.assertEqual(tickers, ["000858.SZ", "600519.SS"])

    def test_load_tickers_skips_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "universe.jsonl"
            path.write_text(
                '{"ticker": "600519.SS", "weight": 5.0}\n\n{"ticker": "", "weight": 0}\n',
                encoding="utf-8",
            )
            tickers = h49a.load_universe_tickers(path)
            self.assertEqual(tickers, ["600519.SS"])

    def test_load_weights_latest_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "universe.jsonl"
            rows = [
                {"ticker": "600519.SS", "weight": 5.0, "effective_date": "2020-01-01"},
                {"ticker": "600519.SS", "weight": 5.5, "effective_date": "2021-06-01"},
                {"ticker": "000858.SZ", "weight": 3.0, "effective_date": "2020-01-01"},
            ]
            path.write_text(
                "\n".join(json.dumps(row) for row in rows), encoding="utf-8"
            )

            weights = h49a.load_universe_with_weights(path)
            self.assertEqual(weights["600519.SS"], 5.5)
            self.assertEqual(weights["000858.SZ"], 3.0)


class TestH49aBuildIndustryMapping(unittest.TestCase):
    def setUp(self):
        self.l1_codes = [
            {"index_code": "801010.SI", "industry_name": "农林牧渔"},
            {"index_code": "801020.SI", "industry_name": "采掘"},
            {"index_code": "801030.SI", "industry_name": "化工"},
        ]

    def _make_pro_api(self, members_by_code):
        """Build a mock pro_api that returns members for given index_code."""

        def index_member(index_code="", **kwargs):
            records = members_by_code.get(index_code, [])
            import pandas as pd
            if not records:
                return pd.DataFrame()
            return pd.DataFrame(records)

        mock = MagicMock()
        mock.index_member.side_effect = index_member
        return mock

    def test_all_mapped_single(self):
        members = {
            "801010.SI": [
                {"con_code": "600519.SH", "in_date": "2015-01-01", "out_date": ""},
            ],
            "801020.SI": [
                {"con_code": "000858.SZ", "in_date": "2015-01-01", "out_date": ""},
            ],
            "801030.SI": [
                {"con_code": "000001.SZ", "in_date": "2015-01-01", "out_date": ""},
            ],
        }
        pro = self._make_pro_api(members)
        results, coverage = h49a.build_industry_mapping(
            ["600519.SS", "000858.SZ", "000001.SZ"],
            self.l1_codes,
            pro,
            "2026-05-22",
        )

        self.assertEqual(len(results), 3)
        self.assertEqual(
            [r.status for r in results], ["mapped", "mapped", "mapped"]
        )
        self.assertEqual(results[0].industry_code, "801010.SI")
        self.assertEqual(results[1].industry_code, "801020.SI")
        self.assertEqual(coverage["mapped_count"], 3)
        self.assertEqual(coverage["unmapped_count"], 0)
        self.assertEqual(coverage["coverage_pct"], 100.0)

    def test_unmapped_ticker(self):
        members = {
            "801010.SI": [
                {"con_code": "600519.SH", "in_date": "2015-01-01", "out_date": ""},
            ],
        }
        pro = self._make_pro_api(members)
        results, coverage = h49a.build_industry_mapping(
            ["600519.SS", "999999.SZ"],
            self.l1_codes,
            pro,
            "2026-05-22",
        )

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].status, "mapped")
        self.assertEqual(results[1].status, "unmapped")
        self.assertEqual(results[1].reason, "not found in any SW L1 index_member set")
        self.assertEqual(coverage["mapped_count"], 1)
        self.assertEqual(coverage["unmapped_count"], 1)
        self.assertEqual(len(coverage["unmapped_tickers"]), 1)
        self.assertIn("reason", coverage["unmapped_tickers"][0])

    def test_multi_mapped_prefers_active_at_snapshot(self):
        """Multi-mapped ticker: prefer the one active at snapshot_date."""
        members = {
            "801010.SI": [
                {"con_code": "000001.SZ", "in_date": "2015-01-01", "out_date": "2019-12-31"},
            ],
            "801020.SI": [
                {"con_code": "000001.SZ", "in_date": "2020-01-01", "out_date": ""},
            ],
        }
        pro = self._make_pro_api(members)
        results, coverage = h49a.build_industry_mapping(
            ["000001.SZ"],
            self.l1_codes,
            pro,
            "2026-05-22",
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, "multi_mapped")
        # Should pick 801020.SI (active at snapshot)
        self.assertEqual(results[0].industry_code, "801020.SI")
        self.assertEqual(results[0].industry_name, "采掘")
        self.assertEqual(len(results[0].alternates), 1)
        self.assertEqual(results[0].alternates[0]["industry_code"], "801010.SI")
        self.assertEqual(coverage["multi_mapped_count"], 1)

    def test_multi_mapped_prefers_latest_in_date_when_multiple_active(self):
        """When multiple are active, prefer the one with latest in_date."""
        members = {
            "801010.SI": [
                {"con_code": "000001.SZ", "in_date": "2015-01-01", "out_date": ""},
            ],
            "801020.SI": [
                {"con_code": "000001.SZ", "in_date": "2023-06-01", "out_date": ""},
            ],
        }
        pro = self._make_pro_api(members)
        results, coverage = h49a.build_industry_mapping(
            ["000001.SZ"],
            self.l1_codes,
            pro,
            "2026-05-22",
        )

        self.assertEqual(results[0].industry_code, "801020.SI")

    def test_industry_histogram(self):
        members = {
            "801010.SI": [
                {"con_code": "600519.SH", "in_date": "2015-01-01", "out_date": ""},
            ],
            "801020.SI": [
                {"con_code": "000858.SZ", "in_date": "2015-01-01", "out_date": ""},
            ],
        }
        pro = self._make_pro_api(members)
        _, coverage = h49a.build_industry_mapping(
            ["600519.SS", "000858.SZ"],
            self.l1_codes,
            pro,
            "2026-05-22",
        )

        hist = coverage["industry_histogram"]
        self.assertEqual(len(hist), 2)
        for code, info in hist.items():
            self.assertIn("name", info)
            self.assertIn("count", info)
            self.assertEqual(info["count"], 1)

    def test_coverage_pct_calculation(self):
        members = {
            "801010.SI": [
                {"con_code": "600519.SH", "in_date": "2015-01-01", "out_date": ""},
            ],
        }
        pro = self._make_pro_api(members)
        _, coverage = h49a.build_industry_mapping(
            ["600519.SS", "999999.SZ", "888888.SS"],
            self.l1_codes,
            pro,
            "2026-05-22",
        )
        # 1 mapped out of 3 = 33.33%
        self.assertAlmostEqual(coverage["coverage_pct"], 33.33, places=1)


class TestH49aCSVOutput(unittest.TestCase):
    def test_csv_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            results = [
                h49a.MappingResult(
                    ticker="600519.SS",
                    ts_code="600519.SH",
                    industry_code="801010.SI",
                    industry_name="农林牧渔",
                    status="mapped",
                ),
                h49a.MappingResult(
                    ticker="999999.SZ",
                    ts_code="999999.SZ",
                    status="unmapped",
                    reason="not found in any SW L1 index_member set",
                ),
            ]

            csv_path = tmp_path / "test.csv"
            h49a.write_metadata_csv(results, "2026-05-22", "2026-05-22T12:00:00Z", csv_path)

            lines = csv_path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 3)  # header + 2 rows
            header = lines[0].split(",")
            self.assertIn("ticker", header)
            self.assertIn("industry_code", header)
            self.assertIn("industry_name", header)
            self.assertIn("source_provider", header)
            self.assertIn("snapshot_date", header)
            self.assertIn("ingested_at", header)

            # Unmapped ticker should have empty industry fields
            unmapped_row = [l for l in lines if "999999" in l][0]
            parts = unmapped_row.split(",")
            self.assertEqual(parts[0], "999999.SZ")
            self.assertEqual(parts[1], "")
            self.assertEqual(parts[2], "")


class TestH49aCoverageJSON(unittest.TestCase):
    def test_provenance_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            coverage = {
                "provenance": {
                    "provider": h49a.PROVIDER_LABEL,
                    "level": h49a.LEVEL,
                    "src": h49a.SRC,
                    "snapshot_date": "2026-05-22",
                },
                "universe_ticker_count": 100,
                "mapped_count": 99,
                "unmapped_count": 1,
                "multi_mapped_count": 3,
                "coverage_pct": 99.0,
                "industry_histogram": {},
                "unmapped_tickers": [],
                "multi_mapped": [],
            }

            json_path = tmp_path / "coverage.json"
            h49a.write_coverage_json(coverage, json_path)

            loaded = json.loads(json_path.read_text(encoding="utf-8"))
            prov = loaded["provenance"]
            self.assertEqual(prov["provider"], h49a.PROVIDER_LABEL)
            self.assertEqual(prov["level"], "L1")
            self.assertEqual(prov["src"], "SW2021")
            self.assertEqual(prov["snapshot_date"], "2026-05-22")
            self.assertEqual(loaded["mapped_count"], 99)
            self.assertEqual(loaded["unmapped_count"], 1)
            self.assertEqual(loaded["multi_mapped_count"], 3)
            self.assertEqual(loaded["coverage_pct"], 99.0)


class TestH49aReport(unittest.TestCase):
    def test_report_contains_all_sections(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            coverage = {
                "provenance": {
                    "provider": h49a.PROVIDER_LABEL,
                    "level": "L1",
                    "src": "SW2021",
                    "snapshot_date": "2026-05-22",
                },
                "universe_ticker_count": 3,
                "mapped_count": 2,
                "unmapped_count": 1,
                "multi_mapped_count": 0,
                "coverage_pct": 66.67,
                "industry_histogram": {
                    "801010.SI": {"name": "农林牧渔", "count": 1},
                    "801020.SI": {"name": "采掘", "count": 1},
                },
                "unmapped_tickers": [
                    {"ticker": "999999.SZ", "ts_code": "999999.SZ",
                     "reason": "not found in any SW L1 index_member set"}
                ],
                "multi_mapped": [],
            }
            results = [
                h49a.MappingResult(
                    ticker="600519.SS", ts_code="600519.SH",
                    industry_code="801010.SI", industry_name="农林牧渔",
                    status="mapped",
                ),
                h49a.MappingResult(
                    ticker="000858.SZ", ts_code="000858.SZ",
                    industry_code="801020.SI", industry_name="采掘",
                    status="mapped",
                ),
                h49a.MappingResult(
                    ticker="999999.SZ", ts_code="999999.SZ",
                    status="unmapped",
                    reason="not found in any SW L1 index_member set",
                ),
            ]
            weights = {"600519.SS": 5.0, "000858.SZ": 3.0}

            report_path = tmp_path / "report.md"
            h49a.write_report(coverage, results, weights, report_path)

            report = report_path.read_text(encoding="utf-8")
            self.assertIn("Provenance", report)
            self.assertIn("Coverage Summary", report)
            self.assertIn("Industry Histogram", report)
            self.assertIn("Unmapped Tickers", report)
            self.assertIn("Verdict", report)
            self.assertIn("RESEARCH_ONLY", report)  # coverage 66.67% < 98%

    def test_report_verdict_candidate_dataset(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            coverage = {
                "provenance": {
                    "provider": h49a.PROVIDER_LABEL,
                    "level": "L1",
                    "src": "SW2021",
                    "snapshot_date": "2026-05-22",
                },
                "universe_ticker_count": 100,
                "mapped_count": 99,
                "unmapped_count": 1,
                "multi_mapped_count": 0,
                "coverage_pct": 99.0,
                "industry_histogram": {},
                "unmapped_tickers": [],
                "multi_mapped": [],
            }
            results = []
            weights = {}

            report_path = tmp_path / "report.md"
            h49a.write_report(coverage, results, weights, report_path)

            report = report_path.read_text(encoding="utf-8")
            self.assertIn("CANDIDATE_DATASET", report)


class TestH49aSmokeValidation(unittest.TestCase):
    def test_smoke_validation_missing_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            rc = h49a.run_smoke_validation(
                tmp_path / "missing.csv",
                tmp_path / "coverage.json",
                tmp_path / "report.md",
            )
            self.assertEqual(rc, 1)

    def test_smoke_validation_all_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            # Write valid CSV
            csv_path = tmp_path / "test.csv"
            csv_path.write_text(
                "ticker,industry_code,industry_name,source_provider,snapshot_date,ingested_at\n"
                "600519.SS,801010.SI,农林牧渔,tushare:index_classify+index_member,2026-05-22,2026-05-22T12:00:00Z\n",
                encoding="utf-8",
            )

            # Write valid JSON
            json_path = tmp_path / "coverage.json"
            json_path.write_text(json.dumps({
                "provenance": {
                    "provider": h49a.PROVIDER_LABEL,
                    "level": "L1",
                    "src": "SW2021",
                    "snapshot_date": "2026-05-22",
                },
                "mapped_count": 1,
                "universe_ticker_count": 1,
                "unmapped_count": 0,
                "multi_mapped_count": 0,
                "coverage_pct": 100.0,
                "industry_histogram": {},
                "unmapped_tickers": [],
                "multi_mapped": [],
            }, ensure_ascii=False, indent=2), encoding="utf-8")

            # Write valid report
            report_path = tmp_path / "report.md"
            report_path.write_text(
                "# H49a\n\n## Provenance\n\n## Coverage Summary\n\n"
                "## Industry Histogram\n\n## Verdict\n",
                encoding="utf-8",
            )

            rc = h49a.run_smoke_validation(csv_path, json_path, report_path)
            self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
