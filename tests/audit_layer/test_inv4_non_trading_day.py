"""INV-4 non-trading-day rule against the REAL vendored XSHG calendar.

Synthetic cases (always run) use a minimal fabricated ledger with no real
amounts. Real-ledger end-to-end cases run only when the local private ledger
contains the annotated 2026-08-15 / 2026-10-05 entries; otherwise skipped.

Each case copies the real strategies/performance_history.json into a temp
root, mutates at most one entry, and runs LedgerValidator.check_inv4().
The XSHG calendar is the vendored data/calendar/xshg_trade_cal.json
(tushare trade_cal SSE). No mocks. Never writes to the repo.
"""
import copy
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from validate_ledger_consistency import (  # noqa: E402
    LedgerValidator, NON_TRADING_NOTE, _xshg_day_status)

PERF = os.path.join(ROOT, "strategies", "performance_history.json")


def _real_perf():
    with open(PERF) as f:
        return json.load(f)


def _real_ledger_available():
    try:
        dates = {e.get("date") for e in _real_perf()}
    except (OSError, ValueError):
        return False
    return {"2026-08-15", "2026-10-05", "2026-08-17", "2026-10-16"} <= dates


REAL = unittest.skipUnless(_real_ledger_available(),
                           "local private ledger not present; real-ledger E2E skipped")


def _row(date, hs, note=None):
    # Synthetic row: zero P&L, no real amounts.
    r = {"date": date, "main_pct": 0.0, "lab_pct": 0.0, "hs300_pct": hs}
    if note is not None:
        r["benchmark_note"] = note
    return r


class TestINV4Synthetic(unittest.TestCase):
    """Minimal fabricated ledger + real calendar. Always runs."""

    def test_calendar_real_days(self):
        self.assertEqual(_xshg_day_status("2026-08-15"), "closed")  # Saturday
        self.assertEqual(_xshg_day_status("2026-10-05"), "closed")  # National Day
        self.assertEqual(_xshg_day_status("2026-08-17"), "open")
        self.assertEqual(_xshg_day_status("2099-01-05"), "unknown")

    def test_non_trading_with_note_passes(self):
        r = _inv4([_row("2026-08-14", 0.5), _row("2026-08-15", None, NON_TRADING_NOTE)])
        self.assertEqual(r["status"], "PASS", r["msg"])

    def test_trading_day_null_fails(self):
        r = _inv4([_row("2026-08-17", None, NON_TRADING_NOTE)])
        self.assertEqual(r["status"], "FAIL")
        self.assertIn("2026-08-17", r["msg"])

    def test_non_trading_without_note_fails(self):
        r = _inv4([_row("2026-10-05", None)])
        self.assertEqual(r["status"], "FAIL")
        self.assertIn("2026-10-05", r["msg"])

    def test_out_of_coverage_fails(self):
        r = _inv4([_row("2099-01-03", None, NON_TRADING_NOTE)])
        self.assertEqual(r["status"], "FAIL")
        self.assertIn("unknown", r["msg"])


def _inv4(perf):
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "strategies"))
        with open(os.path.join(tmp, "strategies", "performance_history.json"), "w") as f:
            json.dump(perf, f)
        v = LedgerValidator(root=tmp)
        v.check_inv4()
        return next(r for r in v.results if r["inv"] == "INV-4")


@REAL
class TestINV4RealLedgerE2E(unittest.TestCase):

    def test_calendar_real_days_e2e(self):
        self.assertEqual(_xshg_day_status("2026-08-15"), "closed")  # Saturday
        self.assertEqual(_xshg_day_status("2026-10-05"), "closed")  # National Day
        self.assertEqual(_xshg_day_status("2026-08-17"), "open")
        self.assertEqual(_xshg_day_status("2099-01-05"), "unknown")

    def test_real_ledger_passes(self):
        r = _inv4(_real_perf())
        self.assertEqual(r["status"], "PASS", r["msg"])

    def test_real_ledger_annotated_non_trading_entries_present(self):
        perf = {e["date"]: e for e in _real_perf()}
        for d in ("2026-08-15", "2026-10-05"):
            self.assertIsNone(perf[d]["hs300_pct"])
            self.assertEqual(perf[d]["benchmark_note"], NON_TRADING_NOTE)

    def test_non_trading_day_without_note_fails(self):
        perf = _real_perf()
        e = next(x for x in perf if x["date"] == "2026-08-15")
        e.pop("benchmark_note")
        r = _inv4(perf)
        self.assertEqual(r["status"], "FAIL")
        self.assertIn("2026-08-15", r["msg"])

    def test_non_trading_day_with_other_note_fails(self):
        perf = _real_perf()
        e = next(x for x in perf if x["date"] == "2026-10-05")
        e["benchmark_note"] = "api_unavailable"
        r = _inv4(perf)
        self.assertEqual(r["status"], "FAIL")
        self.assertIn("2026-10-05", r["msg"])

    def test_trading_day_null_with_note_fails(self):
        perf = _real_perf()
        e = next(x for x in perf if x["date"] == "2026-08-17")
        e["hs300_pct"] = None
        e["benchmark_note"] = NON_TRADING_NOTE
        r = _inv4(perf)
        self.assertEqual(r["status"], "FAIL")
        self.assertIn("2026-08-17", r["msg"])

    def test_trading_day_null_without_note_fails(self):
        perf = _real_perf()
        e = next(x for x in perf if x["date"] == "2026-10-16")
        e["hs300_pct"] = None
        e.pop("benchmark_note", None)
        r = _inv4(perf)
        self.assertEqual(r["status"], "FAIL")
        self.assertIn("2026-10-16", r["msg"])

    def test_out_of_calendar_coverage_fails(self):
        perf = _real_perf()
        extra = copy.deepcopy(perf[-1])
        extra.update(date="2099-01-03", hs300_pct=None, benchmark_note=NON_TRADING_NOTE)
        perf.append(extra)
        r = _inv4(perf)
        self.assertEqual(r["status"], "FAIL")
        self.assertIn("unknown", r["msg"])


if __name__ == "__main__":
    unittest.main()
