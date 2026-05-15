"""Regression tests for backfill operations.

These tests prevent the specific data corruption patterns observed during
the 2026-05-15 incident: losing historical entries, writing placeholder zeros,
and test code polluting real data.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path

VTRADER_ROOT = os.path.expanduser("~/.hermes/virtual-trader")


class TestBackfillPreservesExistingDates(unittest.TestCase):
    """Backfilling new dates must NOT delete existing performance_history entries."""

    def _load_perf(self):
        path = os.path.join(VTRADER_ROOT, "strategies", "performance_history.json")
        with open(path) as f:
            return json.load(f)

    def test_may_6_and_7_present(self):
        """5/6 and 5/7 must exist in performance_history (were lost in prior backfills)."""
        perf = self._load_perf()
        dates = {e["date"] for e in perf}
        self.assertIn("2026-05-06", dates, "5/6 missing — was deleted by backfill")
        self.assertIn("2026-05-07", dates, "5/7 missing — was deleted by backfill")

    def test_no_date_gap_in_may(self):
        """May entries should not have gaps where trade files exist."""
        perf = self._load_perf()
        perf_dates = {e["date"] for e in perf}

        trades_dir = os.path.join(VTRADER_ROOT, "trades")
        trade_dates = set()
        for month_dir in Path(trades_dir).glob("2026-05"):
            for f in month_dir.glob("*.json"):
                import re
                m = re.search(r"(\d{4}-\d{2}-\d{2})", f.name)
                if m:
                    trade_dates.add(m.group(1))

        missing = trade_dates - perf_dates
        self.assertEqual(missing, set(),
                         f"Trade dates missing from perf: {sorted(missing)}")


class TestHS300NeverPlaceholderZero(unittest.TestCase):
    """HS300 benchmark must not be a placeholder 0 on trading days."""

    def _load_perf(self):
        path = os.path.join(VTRADER_ROOT, "strategies", "performance_history.json")
        with open(path) as f:
            return json.load(f)

    def test_no_placeholder_zeros(self):
        """No entry should have hs300_pct=0 without a verification marker."""
        perf = self._load_perf()
        violations = []
        for e in perf:
            if e.get("hs300_pct") == 0:
                has_marker = (e.get("benchmark_source") or
                              e.get("benchmark_verified") or
                              e.get("benchmark_note"))
                if not has_marker:
                    violations.append(e["date"])
        self.assertEqual(violations, [],
                         f"hs300_pct=0 without verification: {violations}")

    def test_no_null_hs300(self):
        """No entry should have hs300_pct as null."""
        perf = self._load_perf()
        nulls = [e["date"] for e in perf if e.get("hs300_pct") is None]
        self.assertEqual(nulls, [], f"hs300_pct=null: {nulls}")


class TestReportMatchesPerformance(unittest.TestCase):
    """Daily report returns must match performance_history."""

    def test_reports_consistent(self):
        """All daily reports must have returns matching performance_history."""
        import re
        perf_path = os.path.join(VTRADER_ROOT, "strategies", "performance_history.json")
        with open(perf_path) as f:
            perf = json.load(f)
        perf_by_date = {e["date"]: e for e in perf}

        reports_dir = os.path.join(VTRADER_ROOT, "reports", "daily")
        mismatches = []
        for report_file in sorted(Path(reports_dir).glob("20??-??-??.md")):
            date = report_file.stem
            if date not in perf_by_date:
                continue
            # Skip pre-May: old reports use cumulative returns, not daily
            if date < "2026-05-01":
                continue
            with open(report_file) as f:
                text = f.read()

            main_m = re.search(r"主账户.*?总资产：[\d,.]+（([+-]?[\d.]+)%）", text, re.DOTALL)
            lab_m = re.search(r"实验账户.*?总资产：[\d,.]+（([+-]?[\d.]+)%）", text, re.DOTALL)

            perf_entry = perf_by_date[date]
            for acct, regex_m in [("main", main_m), ("lab", lab_m)]:
                if regex_m:
                    rpt_pct = float(regex_m.group(1))
                    perf_pct = perf_entry.get(f"{acct}_pct", 0)
                    if abs(rpt_pct - perf_pct) > 0.03:
                        mismatches.append(
                            f"{date} {acct}: report={rpt_pct:+.2f}% perf={perf_pct:+.2f}%"
                        )

        self.assertEqual(mismatches, [],
                         f"Report/perf mismatches:\n  " + "\n  ".join(mismatches))


class TestSettlementWritesNotToRealRepo(unittest.TestCase):
    """run_settlement tests must not write to the real VTRADER_HOME."""

    def test_test_coordinator_uses_tempdir(self):
        """test_coordinator_audit_flow must not use ROOT as data_dir."""
        test_file = os.path.join(VTRADER_ROOT, "tests", "audit_layer",
                                 "test_coordinator_audit_flow.py")
        with open(test_file) as f:
            content = f.read()

        # The test must NOT set c.data_dir = ROOT directly
        # It should use tempfile or mock
        self.assertNotIn("c.data_dir = ROOT", content,
                         "test uses ROOT as data_dir — will pollute real data")
        # Should either use tmpdir or mock run_settlement
        self.assertTrue(
            "tmpdir" in content or "MagicMock" in content or "mock" in content,
            "test must use tmpdir or mock to avoid writing real data"
        )


if __name__ == "__main__":
    unittest.main()
