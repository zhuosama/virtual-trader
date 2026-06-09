"""TDD tests for ledger consistency invariants.

These tests read real repo data (read-only). They verify that
performance_history, trade files, daily reports, and live accounts
are mutually consistent. Never writes to the repo.
"""
import os
import sys
import json
import tempfile
import unittest
from pathlib import Path

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from validate_ledger_consistency import LedgerValidator


class TestINV1_TradeDatesHavePerfEntry(unittest.TestCase):
    """INV-1: Every date with a trade file must have a performance_history entry."""

    def test_no_missing_perf_entries(self):
        v = LedgerValidator(root=ROOT)
        v.check_inv1()
        result = next(r for r in v.results if r["inv"] == "INV-1")
        self.assertEqual(result["status"], "PASS", result["msg"])


class TestINV2_TradeDatesHaveDailyReport(unittest.TestCase):
    """INV-2: Every date with a trade file must have a daily report."""

    def test_no_missing_reports(self):
        v = LedgerValidator(root=ROOT)
        v.check_inv2()
        result = next(r for r in v.results if r["inv"] == "INV-2")
        self.assertEqual(result["status"], "PASS", result["msg"])


class TestINV3_ThreeWayConsistency(unittest.TestCase):
    """INV-3: performance_history, trade file, and daily report must agree on daily returns."""

    def test_no_pnl_mismatches(self):
        v = LedgerValidator(root=ROOT)
        v.check_inv3()
        result = next(r for r in v.results if r["inv"] == "INV-3")
        self.assertEqual(result["status"], "PASS", result["msg"])


class TestINV4_NoUnverifiedBenchmark(unittest.TestCase):
    """INV-4: hs300_pct must not be null or unverified zero placeholder."""

    def test_no_null_benchmark(self):
        v = LedgerValidator(root=ROOT)
        v.check_inv4()
        result = next(r for r in v.results if r["inv"] == "INV-4")
        self.assertEqual(result["status"], "PASS", result["msg"])


class TestINV5_NoDuplicateDates(unittest.TestCase):
    """INV-5: performance_history must not have duplicate dates."""

    def test_no_duplicates(self):
        v = LedgerValidator(root=ROOT)
        v.check_inv5()
        result = next(r for r in v.results if r["inv"] == "INV-5")
        self.assertEqual(result["status"], "PASS", result["msg"])


class TestINV6_ObservedLedgerCoverage(unittest.TestCase):
    """INV-6: All observed dates (trades/reports/workflows) must have perf entries."""

    def test_full_coverage(self):
        v = LedgerValidator(root=ROOT)
        v.check_inv6()
        result = next(r for r in v.results if r["inv"] == "INV-6")
        self.assertEqual(result["status"], "PASS", result["msg"])

    def test_strategy_update_only_workflows_are_not_ledger_dates(self):
        tmpdir = tempfile.mkdtemp()
        Path(tmpdir, "agents", "workflows").mkdir(parents=True)
        Path(tmpdir, "strategies").mkdir()
        Path(tmpdir, "strategies", "performance_history.json").write_text(
            json.dumps([{"date": "2026-05-15"}])
        )
        Path(
            tmpdir,
            "agents",
            "workflows",
            "workflow_strategy_update_only_20260516_225122.json",
        ).write_text("{}")

        v = LedgerValidator(root=tmpdir)
        v.check_inv6()
        result = next(r for r in v.results if r["inv"] == "INV-6")
        self.assertEqual(result["status"], "PASS", result["msg"])

    def test_premarket_workflow_alone_is_not_a_ledger_date(self):
        tmpdir = tempfile.mkdtemp()
        Path(tmpdir, "agents", "workflows").mkdir(parents=True)
        Path(tmpdir, "strategies").mkdir()
        Path(tmpdir, "strategies", "performance_history.json").write_text(
            json.dumps([{"date": "2026-05-15"}])
        )
        Path(
            tmpdir,
            "agents",
            "workflows",
            "workflow_pre_market_20260518_021028.json",
        ).write_text("{}")

        v = LedgerValidator(root=tmpdir)
        v.check_inv6()
        result = next(r for r in v.results if r["inv"] == "INV-6")
        self.assertEqual(result["status"], "PASS", result["msg"])


class TestINV7_LiveAccountAlignment(unittest.TestCase):
    """INV-7: Live accounts daily_pnl_pct must match performance_history (strict mode)."""

    def test_accounts_aligned(self):
        v = LedgerValidator(root=ROOT, strict=True)
        v.check_inv7()
        result = next(r for r in v.results if r["inv"] == "INV-7")
        self.assertIn(result["status"], ("PASS", "SKIP"), result["msg"])


class TestAllInvariants(unittest.TestCase):
    """Run all invariants together — must all pass."""

    def test_all_pass_strict(self):
        v = LedgerValidator(root=ROOT, strict=True)
        results = v.validate()
        failures = [r for r in results if r["status"] == "FAIL"]
        self.assertEqual(failures, [],
                         f"Failed invariants:\n" +
                         "\n".join(f"  {r['inv']}: {r['msg']}" for r in failures))


if __name__ == "__main__":
    unittest.main()
