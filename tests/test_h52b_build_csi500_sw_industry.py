#!/usr/bin/env python3
"""Tests for H52b — CSI500 SW L1 Sector Metadata Ingestion.

Coverage per brief: load+parse, multi-mapped latest-wins deterministic tie-break,
coverage gate violation behavior, universe_source assertion.
All tests are deterministic and free of network calls.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import h52b_build_csi500_sw_industry as h52b  # noqa: E402


class TestH52bTickerConversion(unittest.TestCase):
    """Verify ticker conversion (mirrors H49a pattern)."""

    def test_yahoo_to_tushare_ss(self):
        self.assertEqual(h52b.yahoo_to_tushare_code("600872.SS"), "600872.SH")

    def test_yahoo_to_tushare_sz(self):
        self.assertEqual(h52b.yahoo_to_tushare_code("002410.SZ"), "002410.SZ")

    def test_yahoo_to_tushare_invalid(self):
        with self.assertRaises(ValueError):
            h52b.yahoo_to_tushare_code("000001.XSHE")


class TestH52bUniverseLoading(unittest.TestCase):
    """Verify universe loading from H52a JSONL."""

    def test_load_tickers_unique_sorted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "universe.jsonl"
            rows = [
                {"ticker": "600872.SS", "weight": 0.5, "effective_date": "2020-01-01"},
                {"ticker": "002410.SZ", "weight": 0.3, "effective_date": "2020-01-01"},
                {"ticker": "600872.SS", "weight": 0.6, "effective_date": "2021-06-01"},
            ]
            path.write_text(
                "\n".join(json.dumps(row) for row in rows), encoding="utf-8"
            )

            tickers = h52b.load_universe_tickers(path)
            self.assertEqual(tickers, ["002410.SZ", "600872.SS"])

    def test_load_tickers_skips_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "universe.jsonl"
            path.write_text(
                '{"ticker": "600872.SS", "weight": 0.5}\n\n{"ticker": "", "weight": 0}\n',
                encoding="utf-8",
            )
            tickers = h52b.load_universe_tickers(path)
            self.assertEqual(tickers, ["600872.SS"])

    def test_load_weights_latest_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "universe.jsonl"
            rows = [
                {"ticker": "600872.SS", "weight": 0.5, "effective_date": "2020-01-01"},
                {"ticker": "600872.SS", "weight": 0.8, "effective_date": "2022-06-01"},
                {"ticker": "002410.SZ", "weight": 0.3, "effective_date": "2020-01-01"},
            ]
            path.write_text(
                "\n".join(json.dumps(row) for row in rows), encoding="utf-8"
            )

            weights = h52b.load_universe_with_weights(path)
            self.assertEqual(weights["600872.SS"], 0.8)
            self.assertEqual(weights["002410.SZ"], 0.3)


class TestH52bMapping(unittest.TestCase):
    """Test core mapping logic with deterministic tie-break."""

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
                {"con_code": "600872.SH", "in_date": "2015-01-01", "out_date": ""},
            ],
            "801020.SI": [
                {"con_code": "002410.SZ", "in_date": "2015-01-01", "out_date": ""},
            ],
            "801030.SI": [
                {"con_code": "000001.SZ", "in_date": "2015-01-01", "out_date": ""},
            ],
        }
        pro = self._make_pro_api(members)
        results, coverage, failures = h52b.build_industry_mapping(
            ["600872.SS", "002410.SZ", "000001.SZ"],
            self.l1_codes,
            pro,
            "2026-05-24",
        )

        self.assertEqual(len(results), 3)
        self.assertEqual([r.status for r in results], ["mapped", "mapped", "mapped"])
        self.assertEqual(results[0].industry_code, "801010.SI")
        self.assertEqual(results[1].industry_code, "801020.SI")
        self.assertEqual(coverage["mapped_count"], 3)
        self.assertEqual(coverage["unmapped_count"], 0)
        self.assertEqual(coverage["coverage_pct"], 100.0)
        self.assertEqual(len(failures), 0)

    def test_unmapped_ticker(self):
        members = {
            "801010.SI": [
                {"con_code": "600872.SH", "in_date": "2015-01-01", "out_date": ""},
            ],
        }
        pro = self._make_pro_api(members)
        results, coverage, failures = h52b.build_industry_mapping(
            ["600872.SS", "999999.SZ"],
            self.l1_codes,
            pro,
            "2026-05-24",
        )

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].status, "mapped")
        self.assertEqual(results[1].status, "unmapped")
        self.assertEqual(results[1].reason, "not found in any SW L1 index_member set")
        self.assertEqual(coverage["mapped_count"], 1)
        self.assertEqual(coverage["unmapped_count"], 1)
        self.assertEqual(len(coverage["unmapped_tickers"]), 1)
        self.assertIn("reason", coverage["unmapped_tickers"][0])
        # unmapped entities must have a non-empty reason
        self.assertTrue(bool(coverage["unmapped_tickers"][0]["reason"]))

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
        results, coverage, _ = h52b.build_industry_mapping(
            ["000001.SZ"],
            self.l1_codes,
            pro,
            "2026-05-24",
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, "multi_mapped")
        self.assertEqual(results[0].industry_code, "801020.SI")
        self.assertEqual(results[0].industry_name, "采掘")
        self.assertEqual(len(results[0].alternates), 1)
        self.assertEqual(results[0].alternates[0]["industry_code"], "801010.SI")
        self.assertEqual(coverage["multi_mapped_count"], 1)

    def test_multi_mapped_latest_in_date_wins(self):
        """When multiple are active, prefer the latest in_date."""
        members = {
            "801010.SI": [
                {"con_code": "000001.SZ", "in_date": "2015-01-01", "out_date": ""},
            ],
            "801020.SI": [
                {"con_code": "000001.SZ", "in_date": "2023-06-01", "out_date": ""},
            ],
        }
        pro = self._make_pro_api(members)
        results, _, _ = h52b.build_industry_mapping(
            ["000001.SZ"],
            self.l1_codes,
            pro,
            "2026-05-24",
        )

        self.assertEqual(results[0].industry_code, "801020.SI")

    def test_multi_mapped_equal_in_date_deterministic_tie_break(self):
        """When ALL in_date values are equal, pick by industry_code ASC (deterministic).

        This is the edge case identified in the H52b review prompt.
        """
        members = {
            "801020.SI": [
                {"con_code": "000001.SZ", "in_date": "2015-01-01", "out_date": "2019-12-31"},
            ],
            "801010.SI": [
                {"con_code": "000001.SZ", "in_date": "2015-01-01", "out_date": "2019-12-31"},
            ],
        }
        pro = self._make_pro_api(members)
        results, _, _ = h52b.build_industry_mapping(
            ["000001.SZ"],
            self.l1_codes,
            pro,
            "2024-01-01",  # snapshot before out_date
        )

        # Both have same in_date, both active at snapshot.
        # Deterministic tie-break: pick industry_code ASC → 801010.SI
        self.assertEqual(results[0].industry_code, "801010.SI")

    def test_multi_mapped_none_active_fallback(self):
        """When none active at snapshot, pick latest in_date."""
        members = {
            "801010.SI": [
                {"con_code": "000001.SZ", "in_date": "2010-01-01", "out_date": "2015-12-31"},
            ],
            "801020.SI": [
                {"con_code": "000001.SZ", "in_date": "2018-06-01", "out_date": "2020-12-31"},
            ],
        }
        pro = self._make_pro_api(members)
        results, _, _ = h52b.build_industry_mapping(
            ["000001.SZ"],
            self.l1_codes,
            pro,
            "2026-05-24",  # snapshot after both out_dates → none active
        )

        # 801020.SI has later in_date
        self.assertEqual(results[0].industry_code, "801020.SI")

    def test_multi_mapped_all_same_in_date_none_active_fallback(self):
        """When none active AND all in_dates equal → industry_code ASC for determinism."""
        members = {
            "801020.SI": [
                {"con_code": "000001.SZ", "in_date": "2010-01-01", "out_date": "2015-12-31"},
            ],
            "801010.SI": [
                {"con_code": "000001.SZ", "in_date": "2010-01-01", "out_date": "2015-12-31"},
            ],
        }
        pro = self._make_pro_api(members)
        results, _, _ = h52b.build_industry_mapping(
            ["000001.SZ"],
            self.l1_codes,
            pro,
            "2026-05-24",  # snapshot after both out_dates
        )

        self.assertEqual(results[0].industry_code, "801010.SI")  # ASC tie-break

    def test_industry_histogram(self):
        members = {
            "801010.SI": [
                {"con_code": "600872.SH", "in_date": "2015-01-01", "out_date": ""},
            ],
            "801020.SI": [
                {"con_code": "002410.SZ", "in_date": "2015-01-01", "out_date": ""},
            ],
        }
        pro = self._make_pro_api(members)
        _, coverage, _ = h52b.build_industry_mapping(
            ["600872.SS", "002410.SZ"],
            self.l1_codes,
            pro,
            "2026-05-24",
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
                {"con_code": "600872.SH", "in_date": "2015-01-01", "out_date": ""},
            ],
        }
        pro = self._make_pro_api(members)
        _, coverage, _ = h52b.build_industry_mapping(
            ["600872.SS", "999999.SZ", "888888.SS"],
            self.l1_codes,
            pro,
            "2026-05-24",
        )
        # 1 mapped out of 3 = 33.33%
        self.assertAlmostEqual(coverage["coverage_pct"], 33.33, places=1)

    def test_coverage_includes_universe_source(self):
        """Verify universe_source is populated and points at H52a."""
        members = {
            "801010.SI": [
                {"con_code": "600872.SH", "in_date": "2015-01-01", "out_date": ""},
            ],
        }
        pro = self._make_pro_api(members)
        _, coverage, _ = h52b.build_industry_mapping(
            ["600872.SS"],
            self.l1_codes,
            pro,
            "2026-05-24",
        )
        self.assertEqual(
            coverage["universe_source"],
            "data/cn_pit/universe_h52a_csi500.jsonl"
        )

    def test_coverage_includes_fetch_failures(self):
        """Verify fetch_failures key exists in coverage."""
        members = {
            "801010.SI": [
                {"con_code": "600872.SH", "in_date": "2015-01-01", "out_date": ""},
            ],
        }
        pro = self._make_pro_api(members)
        _, coverage, _ = h52b.build_industry_mapping(
            ["600872.SS"],
            self.l1_codes,
            pro,
            "2026-05-24",
        )
        self.assertIn("fetch_failures", coverage)
        self.assertEqual(coverage["fetch_failures"], [])

    def test_coverage_includes_verdict(self):
        """Verify verdict key exists in coverage."""
        members = {
            "801010.SI": [
                {"con_code": "600872.SH", "in_date": "2015-01-01", "out_date": ""},
            ],
        }
        pro = self._make_pro_api(members)
        _, coverage, _ = h52b.build_industry_mapping(
            ["600872.SS"],
            self.l1_codes,
            pro,
            "2026-05-24",
        )
        self.assertIn("verdict", coverage)

    def test_coverage_includes_snapshot_timestamp(self):
        """Verify snapshot_timestamp is in provenance (required for H52b)."""
        members = {
            "801010.SI": [
                {"con_code": "600872.SH", "in_date": "2015-01-01", "out_date": ""},
            ],
        }
        pro = self._make_pro_api(members)
        _, coverage, _ = h52b.build_industry_mapping(
            ["600872.SS"],
            self.l1_codes,
            pro,
            "2026-05-24",
        )
        self.assertIn("snapshot_timestamp", coverage["provenance"])


class TestH52bCoverageGateBehavior(unittest.TestCase):
    """Test coverage acceptance gate behavior — when do we get BLOCKED?"""

    def test_above_95_gate_is_candidate(self):
        """Coverage above 95% should pass."""
        coverage = {
            "universe_ticker_count": 100,
            "mapped_count": 96,
            "unmapped_count": 4,
            "multi_mapped_count": 5,
            "coverage_pct": 96.0,
            "industry_histogram": {
                str(801000 + i): {"name": f"Industry{i}", "count": 96 // 25}
                for i in range(25)
            },
            "fetch_failures": [],
        }
        self.assertTrue(h52b._check_coverage_gates(coverage))

    def test_below_95_gate_is_blocked(self):
        """Coverage below 95% should fail."""
        coverage = {
            "universe_ticker_count": 100,
            "mapped_count": 94,
            "unmapped_count": 6,
            "multi_mapped_count": 3,
            "coverage_pct": 94.0,
            "industry_histogram": {"801010.SI": {"name": "A", "count": 94}},
            "fetch_failures": [],
        }
        self.assertFalse(h52b._check_coverage_gates(coverage))

    def test_multi_mapped_exceeds_50pct_is_blocked(self):
        """multi_mapped > 50% of universe should fail (sanity cap)."""
        coverage = {
            "universe_ticker_count": 100,
            "mapped_count": 100,
            "unmapped_count": 0,
            "multi_mapped_count": 55,  # 55/100 = 55% > 50%
            "coverage_pct": 100.0,
            "industry_histogram": {
                str(801000 + i): {"name": f"Industry{i}", "count": 100 // 25}
                for i in range(25)
            },
            "fetch_failures": [],
        }
        self.assertFalse(h52b._check_coverage_gates(coverage))

    def test_multi_mapped_at_exactly_50pct_is_ok(self):
        """multi_mapped == 50% should pass (≤ 0.50)."""
        coverage = {
            "universe_ticker_count": 100,
            "mapped_count": 100,
            "unmapped_count": 0,
            "multi_mapped_count": 50,
            "coverage_pct": 100.0,
            "industry_histogram": {
                str(801000 + i): {"name": f"Industry{i}", "count": 100 // 25}
                for i in range(25)
            },
            "fetch_failures": [],
        }
        self.assertTrue(h52b._check_coverage_gates(coverage))

    def test_fewer_than_25_industries_is_blocked(self):
        """Fewer than 25 distinct SW L1 industries should fail."""
        coverage = {
            "universe_ticker_count": 100,
            "mapped_count": 100,
            "unmapped_count": 0,
            "multi_mapped_count": 5,
            "coverage_pct": 100.0,
            "industry_histogram": {
                "801010.SI": {"name": "A", "count": 100},
            },
            "fetch_failures": [],
        }
        self.assertFalse(h52b._check_coverage_gates(coverage))

    def test_single_industry_above_40pct_is_blocked(self):
        """Any single industry > 40% of universe should fail."""
        coverage = {
            "universe_ticker_count": 100,
            "mapped_count": 100,
            "unmapped_count": 0,
            "multi_mapped_count": 5,
            "coverage_pct": 100.0,
            "industry_histogram": {
                "801010.SI": {"name": "A", "count": 41},  # 41% > 40%
            },
            "fetch_failures": [],
        }
        # Need 25 distinct in histogram to pass C3, but we only have 1
        # Actually check only C4 by adding 24 filler industries
        for i in range(24):
            coverage["industry_histogram"][str(801100 + i)] = {"name": f"X{i}", "count": 1}
        self.assertFalse(h52b._check_coverage_gates(coverage))

    def test_too_many_fetch_failures_is_blocked(self):
        """More than 3 fetch_failures should fail."""
        coverage = {
            "universe_ticker_count": 100,
            "mapped_count": 100,
            "unmapped_count": 0,
            "multi_mapped_count": 5,
            "coverage_pct": 100.0,
            "industry_histogram": {
                str(801000 + i): {"name": f"X{i}", "count": 100 // 25}
                for i in range(25)
            },
            "fetch_failures": [
                {"industry_code": f"8010{10+i:02d}.SI", "reason": f"error {i}"}
                for i in range(4)
            ],
        }
        self.assertFalse(h52b._check_coverage_gates(coverage))


class TestH52bCSVOutput(unittest.TestCase):
    """Test CSV output schema matches H49a reference."""

    def test_csv_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            results = [
                h52b.MappingResult(
                    ticker="600872.SS",
                    ts_code="600872.SH",
                    industry_code="801010.SI",
                    industry_name="农林牧渔",
                    status="mapped",
                ),
                h52b.MappingResult(
                    ticker="999999.SZ",
                    ts_code="999999.SZ",
                    status="unmapped",
                    reason="not found in any SW L1 index_member set",
                ),
            ]

            csv_path = tmp_path / "test.csv"
            h52b.write_metadata_csv(results, "2026-05-24", "2026-05-24T12:00:00Z", csv_path)

            lines = csv_path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 2)  # header + 1 mapped row (unmapped excluded)
            header = lines[0].split(",")
            self.assertIn("ticker", header)
            self.assertIn("industry_code", header)
            self.assertIn("industry_name", header)
            self.assertIn("source_provider", header)
            self.assertIn("snapshot_date", header)
            self.assertIn("ingested_at", header)

            # Only mapped ticker present
            self.assertIn("600872.SS", lines[1])
            self.assertIn("801010.SI", lines[1])

    def test_csv_rows_sorted_by_ticker(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            results = [
                h52b.MappingResult(
                    ticker="600872.SS", ts_code="600872.SH",
                    industry_code="801010.SI", industry_name="农林牧渔", status="mapped",
                ),
                h52b.MappingResult(
                    ticker="000001.SZ", ts_code="000001.SZ",
                    industry_code="801780.SI", industry_name="银行", status="mapped",
                ),
            ]

            csv_path = tmp_path / "test.csv"
            h52b.write_metadata_csv(results, "2026-05-24", "2026-05-24T12:00:00Z", csv_path)

            lines = csv_path.read_text(encoding="utf-8").strip().splitlines()
            # Sorted by ticker: 000001.SZ comes before 600872.SS
            self.assertIn("000001.SZ", lines[1])
            self.assertIn("600872.SS", lines[2])


class TestH52bCoverageJSON(unittest.TestCase):
    """Test coverage JSON output schema."""

    def test_provenance_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            coverage = {
                "provenance": {
                    "provider": h52b.PROVIDER_LABEL,
                    "level": h52b.LEVEL,
                    "src": h52b.SRC,
                    "snapshot_date": "2026-05-24",
                    "snapshot_timestamp": "2026-05-24T12:00:00Z",
                },
                "universe_source": h52b.UNIVERSE_SOURCE,
                "universe_ticker_count": 100,
                "mapped_count": 99,
                "unmapped_count": 1,
                "multi_mapped_count": 3,
                "coverage_pct": 99.0,
                "industry_histogram": {},
                "unmapped_tickers": [],
                "multi_mapped": [],
                "fetch_failures": [],
                "verdict": "CANDIDATE_DATASET",
            }

            json_path = tmp_path / "coverage.json"
            h52b.write_coverage_json(coverage, json_path)

            loaded = json.loads(json_path.read_text(encoding="utf-8"))
            prov = loaded["provenance"]
            self.assertEqual(prov["provider"], h52b.PROVIDER_LABEL)
            self.assertEqual(prov["level"], "L1")
            self.assertEqual(prov["src"], "SW2021")
            self.assertEqual(prov["snapshot_date"], "2026-05-24")
            self.assertEqual(prov["snapshot_timestamp"], "2026-05-24T12:00:00Z")
            self.assertEqual(loaded["universe_source"], h52b.UNIVERSE_SOURCE)
            self.assertEqual(loaded["mapped_count"], 99)
            self.assertEqual(loaded["unmapped_count"], 1)
            self.assertEqual(loaded["multi_mapped_count"], 3)
            self.assertEqual(loaded["coverage_pct"], 99.0)
            self.assertEqual(loaded["verdict"], "CANDIDATE_DATASET")
            self.assertIn("fetch_failures", loaded)


class TestH52bReport(unittest.TestCase):
    """Test Markdown report output."""

    def test_report_contains_all_sections(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            coverage = {
                "provenance": {
                    "provider": h52b.PROVIDER_LABEL,
                    "level": "L1",
                    "src": "SW2021",
                    "snapshot_date": "2026-05-24",
                    "snapshot_timestamp": "2026-05-24T12:00:00Z",
                },
                "universe_source": h52b.UNIVERSE_SOURCE,
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
                "fetch_failures": [],
                "verdict": "BLOCKED",
            }
            results = [
                h52b.MappingResult(
                    ticker="600872.SS", ts_code="600872.SH",
                    industry_code="801010.SI", industry_name="农林牧渔",
                    status="mapped",
                ),
                h52b.MappingResult(
                    ticker="002410.SZ", ts_code="002410.SZ",
                    industry_code="801020.SI", industry_name="采掘",
                    status="mapped",
                ),
                h52b.MappingResult(
                    ticker="999999.SZ", ts_code="999999.SZ",
                    status="unmapped",
                    reason="not found in any SW L1 index_member set",
                ),
            ]
            weights = {"600872.SS": 0.5, "002410.SZ": 0.3}

            report_path = tmp_path / "report.md"
            h52b.write_report(coverage, results, weights, report_path)

            report = report_path.read_text(encoding="utf-8")
            self.assertIn("Provenance", report)
            self.assertIn("Coverage Summary", report)
            self.assertIn("Industry Histogram", report)
            self.assertIn("Unmapped Tickers", report)
            self.assertIn("Verdict", report)
            self.assertIn("Fetch Failures", report)
            self.assertIn("BLOCKED", report)

    def test_report_verdict_candidate_dataset(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            coverage = {
                "provenance": {
                    "provider": h52b.PROVIDER_LABEL,
                    "level": "L1",
                    "src": "SW2021",
                    "snapshot_date": "2026-05-24",
                    "snapshot_timestamp": "2026-05-24T12:00:00Z",
                },
                "universe_source": h52b.UNIVERSE_SOURCE,
                "universe_ticker_count": 100,
                "mapped_count": 98,
                "unmapped_count": 2,
                "multi_mapped_count": 10,
                "coverage_pct": 98.0,
                "industry_histogram": {},
                "unmapped_tickers": [],
                "multi_mapped": [],
                "fetch_failures": [],
                "verdict": "CANDIDATE_DATASET",
            }
            results = []
            weights = {}

            report_path = tmp_path / "report.md"
            h52b.write_report(coverage, results, weights, report_path)

            report = report_path.read_text(encoding="utf-8")
            self.assertIn("CANDIDATE_DATASET", report)


class TestH52bSmokeValidation(unittest.TestCase):
    """Test the smoke validation function."""

    def test_smoke_validation_missing_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            rc = h52b.run_smoke_validation(
                tmp_path / "missing.csv",
                tmp_path / "coverage.json",
                tmp_path / "report.md",
            )
            self.assertEqual(rc, 1)

    def test_smoke_validation_all_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            csv_path = tmp_path / "test.csv"
            csv_path.write_text(
                "ticker,industry_code,industry_name,source_provider,snapshot_date,ingested_at\n"
                "600872.SS,801010.SI,农林牧渔,tushare:index_classify+index_member,2026-05-24,2026-05-24T12:00:00Z\n",
                encoding="utf-8",
            )

            json_path = tmp_path / "coverage.json"
            json_path.write_text(json.dumps({
                "provenance": {
                    "provider": h52b.PROVIDER_LABEL,
                    "level": "L1",
                    "src": "SW2021",
                    "snapshot_date": "2026-05-24",
                    "snapshot_timestamp": "2026-05-24T12:00:00Z",
                },
                "universe_source": h52b.UNIVERSE_SOURCE,
                "mapped_count": 1,
                "universe_ticker_count": 1,
                "unmapped_count": 0,
                "multi_mapped_count": 0,
                "coverage_pct": 100.0,
                "industry_histogram": {},
                "unmapped_tickers": [],
                "multi_mapped": [],
                "fetch_failures": [],
                "verdict": "CANDIDATE_DATASET",
            }, ensure_ascii=False, indent=2), encoding="utf-8")

            report_path = tmp_path / "report.md"
            report_path.write_text(
                "# H52b\n\n## Provenance\n\n## Coverage Summary\n\n"
                "## Industry Histogram\n\n## Fetch Failures\n\n## Unmapped Tickers\n\n## Verdict\n",
                encoding="utf-8",
            )

            rc = h52b.run_smoke_validation(csv_path, json_path, report_path)
            self.assertEqual(rc, 0)


class TestH52bProvenanceAssertions(unittest.TestCase):
    """Verify that provenance.universe_source asserts against H52a specifically."""

    def test_universe_source_constant(self):
        """H52b's UNIVERSE_SOURCE must point to H52a, NOT H30."""
        self.assertEqual(
            h52b.UNIVERSE_SOURCE,
            "data/cn_pit/universe_h52a_csi500.jsonl"
        )
        self.assertNotEqual(
            h52b.UNIVERSE_SOURCE,
            "data/cn_pit/universe_h30_candidate.jsonl"
        )

    def test_provider_label_constant(self):
        self.assertEqual(
            h52b.PROVIDER_LABEL,
            "tushare:index_classify+index_member"
        )

    def test_level_and_src_constants(self):
        self.assertEqual(h52b.LEVEL, "L1")
        self.assertEqual(h52b.SRC, "SW2021")

    def test_coverage_gate_constant(self):
        """H52b gate is 95% (lower than H49a's 98%)."""
        self.assertEqual(h52b.COVERAGE_GATE, 95.0)

    def test_multi_mapped_cap_constant(self):
        """multi_mapped cap is 0.50 (50%)."""
        self.assertEqual(h52b.MULTI_MAPPED_CAP, 0.50)


if __name__ == "__main__":
    unittest.main()
