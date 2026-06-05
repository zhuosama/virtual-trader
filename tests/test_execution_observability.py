#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 4 · observability — fix the 假绿 (false-green) problem.

Two pure helpers in execution_model:
  build_execution_decisions(summary) -> dict   (compact persisted view)
  format_execution_summary(summary) -> str     (Chinese WeCom/post-market block)

Plus an integration check that the formatter is wired into the post-market
human-facing output and that degraded/rolled-back/halt surface a warning line
in workflow_result (without flipping a passing settlement to failed on plain
shadow rejects).

All summaries are injected; no real writes, no config changes.
"""

import os
import sys
import unittest

VTRADER_ROOT = os.path.expanduser("~/.hermes/virtual-trader")
sys.path.insert(0, os.path.join(VTRADER_ROOT, "agents"))

from execution_model import (  # noqa: E402
    build_execution_decisions,
    format_execution_summary,
)


# ── summary fixtures (shape per run_autonomous_execution) ────────────────────

def _decision(account, code, side, verdict, gate, reason):
    return {
        "order": {"account": account, "code": code, "side": side},
        "verdict": verdict,
        "gate": gate,
        "reason": reason,
    }


def _summary_pass_only():
    return {
        "mode": "live",
        "date": "2026-06-05",
        "plan_found": True,
        "counts": {"pass": 2, "clamp": 0, "reject": 0, "halt": 0},
        "ledger_validation_passed": True,
        "accounts": {
            "main": {
                "candidates": 2,
                "executed": 2,
                "counts": {"pass": 2, "clamp": 0, "reject": 0, "halt": 0},
                "decisions": [
                    _decision("main", "600900", "buy", "pass", "G3", "pretrade ok"),
                    _decision("main", "600519", "buy", "pass", "G3", "pretrade ok"),
                ],
            },
            "lab": {
                "candidates": 0,
                "executed": 0,
                "counts": {"pass": 0, "clamp": 0, "reject": 0, "halt": 0},
                "decisions": [],
            },
        },
    }


def _summary_with_rejects():
    return {
        "mode": "shadow",
        "date": "2026-06-05",
        "plan_found": True,
        "counts": {"pass": 1, "clamp": 1, "reject": 4, "halt": 0},
        "accounts": {
            "main": {
                "candidates": 6,
                "executed": 0,
                "counts": {"pass": 1, "clamp": 1, "reject": 4, "halt": 0},
                "decisions": [
                    _decision("main", "600900", "buy", "pass", "G3", "pretrade ok"),
                    _decision("main", "600519", "buy", "clamp", "G2", "single position capped"),
                    _decision("main", "000001", "buy", "reject", "G3", "below min_order_amount"),
                    _decision("main", "000002", "buy", "reject", "G3", "below min_order_amount"),
                    _decision("main", "000003", "buy", "reject", "G3", "price_deviation 0.12 > 0.1"),
                    _decision("main", "000004", "buy", "reject", "G3", "not in whitelist: 000004"),
                ],
            },
            "lab": {
                "candidates": 0,
                "executed": 0,
                "counts": {"pass": 0, "clamp": 0, "reject": 0, "halt": 0},
                "decisions": [],
            },
        },
    }


def _summary_halt():
    return {
        "mode": "halt",
        "date": "2026-06-05",
        "plan_found": True,
        "counts": {"pass": 0, "clamp": 0, "reject": 0, "halt": 3},
        "accounts": {
            "main": {
                "candidates": 3,
                "executed": 0,
                "counts": {"pass": 0, "clamp": 0, "reject": 0, "halt": 3},
                "decisions": [
                    _decision("main", "600900", "buy", "halt", "G6", "kill-switch: EXECUTION_HALT present"),
                    _decision("main", "600519", "buy", "halt", "G6", "kill-switch: EXECUTION_HALT present"),
                    _decision("main", "000001", "buy", "halt", "G6", "kill-switch: EXECUTION_HALT present"),
                ],
            },
            "lab": {
                "candidates": 0,
                "executed": 0,
                "counts": {"pass": 0, "clamp": 0, "reject": 0, "halt": 0},
                "decisions": [],
            },
        },
    }


def _summary_rolled_back():
    return {
        "mode": "canary",
        "date": "2026-06-05",
        "plan_found": True,
        "counts": {"pass": 2, "clamp": 0, "reject": 0, "halt": 0},
        "ledger_validation_passed": False,
        "degraded_reason": "ledger validation failed after autonomous execution (INV-3) — rolled back account/trade writes",
        "accounts": {
            "main": {
                "candidates": 2,
                "executed": 0,
                "rolled_back": True,
                "counts": {"pass": 2, "clamp": 0, "reject": 0, "halt": 0},
                "decisions": [
                    _decision("main", "600900", "buy", "pass", "G3", "pretrade ok"),
                    _decision("main", "600519", "buy", "pass", "G3", "pretrade ok"),
                ],
            },
            "lab": {
                "candidates": 0,
                "executed": 0,
                "counts": {"pass": 0, "clamp": 0, "reject": 0, "halt": 0},
                "decisions": [],
            },
        },
    }


def _summary_degraded_only():
    """Degraded (e.g. execute_plan internal exception path) but not rolled_back."""
    return {
        "mode": "shadow",
        "date": "2026-06-05",
        "plan_found": True,
        "counts": {"pass": 0, "clamp": 0, "reject": 0, "halt": 0},
        "degraded_reason": "execute_plan internal error — day skipped",
        "accounts": {
            "main": {
                "candidates": 0,
                "executed": 0,
                "counts": {"pass": 0, "clamp": 0, "reject": 0, "halt": 0},
                "decisions": [],
            },
            "lab": {
                "candidates": 0,
                "executed": 0,
                "counts": {"pass": 0, "clamp": 0, "reject": 0, "halt": 0},
                "decisions": [],
            },
        },
    }


def _summary_no_plan():
    return {
        "mode": "shadow",
        "date": "2026-06-05",
        "plan_found": False,
        "counts": {"pass": 0, "clamp": 0, "reject": 0, "halt": 0},
        "accounts": {
            "main": {"candidates": 0, "executed": 0,
                     "counts": {"pass": 0, "clamp": 0, "reject": 0, "halt": 0},
                     "decisions": []},
            "lab": {"candidates": 0, "executed": 0,
                    "counts": {"pass": 0, "clamp": 0, "reject": 0, "halt": 0},
                    "decisions": []},
        },
    }


# ── 1. build_execution_decisions ────────────────────────────────────────────

class TestBuildExecutionDecisions(unittest.TestCase):

    def test_pass_only(self):
        d = build_execution_decisions(_summary_pass_only())
        self.assertEqual(d["mode"], "live")
        self.assertIs(d["ledger_validation_passed"], True)
        self.assertNotIn("degraded_reason", d)
        main = d["accounts"]["main"]
        self.assertEqual(main["candidates"], 2)
        self.assertEqual(main["passed"], 2)   # pass + clamp
        self.assertEqual(main["rejected"], 0)
        self.assertEqual(main["halted"], 0)
        self.assertEqual(main["executed"], 2)
        self.assertFalse(main["rolled_back"])
        self.assertEqual(main["top_rejections"], [])

    def test_with_rejects_aggregates_top3_distinct(self):
        d = build_execution_decisions(_summary_with_rejects())
        main = d["accounts"]["main"]
        self.assertEqual(main["passed"], 2)    # 1 pass + 1 clamp
        self.assertEqual(main["rejected"], 4)
        # top rejections: ≤3 distinct (gate, reason); the two min_order rejects
        # aggregate to one entry with count 2.
        self.assertLessEqual(len(main["top_rejections"]), 3)
        reasons = [r["reason"] for r in main["top_rejections"]]
        # the most-common reason (below min_order_amount, count 2) leads
        self.assertTrue(any("min_order_amount" in r for r in reasons))
        top = main["top_rejections"][0]
        self.assertEqual(top["gate"], "G3")
        self.assertEqual(top["count"], 2)

    def test_halted(self):
        d = build_execution_decisions(_summary_halt())
        self.assertEqual(d["mode"], "halt")
        main = d["accounts"]["main"]
        self.assertEqual(main["halted"], 3)
        self.assertEqual(main["passed"], 0)
        self.assertEqual(main["executed"], 0)

    def test_rolled_back(self):
        d = build_execution_decisions(_summary_rolled_back())
        self.assertIs(d["ledger_validation_passed"], False)
        self.assertIn("degraded_reason", d)
        main = d["accounts"]["main"]
        self.assertTrue(main["rolled_back"])
        self.assertEqual(main["executed"], 0)

    def test_degraded_passthrough(self):
        d = build_execution_decisions(_summary_degraded_only())
        self.assertEqual(d["degraded_reason"], "execute_plan internal error — day skipped")

    def test_error_summary_is_safe(self):
        d = build_execution_decisions({"error": "boom"})
        self.assertEqual(d["accounts"], {})
        self.assertEqual(d.get("error"), "boom")


# ── 2. format_execution_summary ─────────────────────────────────────────────

class TestFormatExecutionSummary(unittest.TestCase):

    def test_normal_live(self):
        s = format_execution_summary(_summary_pass_only())
        self.assertIn("🤖 自主执行(live)", s)
        self.assertIn("计划2", s)
        self.assertIn("闸门通过2", s)
        self.assertIn("否决0", s)
        self.assertIn("成交2", s)
        # live is real → no shadow label
        self.assertNotIn("影子模式", s)
        # no halt/rollback banners
        self.assertNotIn("⛔", s)
        self.assertNotIn("⚠️ 自主执行已回滚", s)

    def test_with_rejects_lists_up_to_3(self):
        s = format_execution_summary(_summary_with_rejects())
        self.assertIn("否决4", s)
        # rejection lines present, capped at 3
        veto_lines = [ln for ln in s.splitlines() if ln.strip().startswith("· 否决")]
        self.assertGreaterEqual(len(veto_lines), 1)
        self.assertLessEqual(len(veto_lines), 3)
        self.assertTrue(any("G3" in ln for ln in veto_lines))

    def test_shadow_labelled(self):
        s = format_execution_summary(_summary_with_rejects())
        self.assertIn("（影子模式 · 未真实成交）", s)

    def test_halt_banner(self):
        s = format_execution_summary(_summary_halt())
        self.assertIn("⛔", s)
        self.assertIn("自主执行已停", s)
        self.assertIn("reduce-only", s)

    def test_rolled_back_banner_shows_zero_fills(self):
        s = format_execution_summary(_summary_rolled_back())
        self.assertIn("⚠️ 自主执行已回滚", s)
        self.assertIn("INV-3", s)
        self.assertIn("成交0", s)

    def test_degraded_banner(self):
        s = format_execution_summary(_summary_degraded_only())
        self.assertIn("⚠️ 自主执行已回滚", s)
        self.assertIn("day skipped", s)
        self.assertIn("成交0", s)

    def test_no_plan(self):
        s = format_execution_summary(_summary_no_plan())
        self.assertIn("无计划/无候选", s)

    def test_error_summary_safe(self):
        s = format_execution_summary({"error": "boom"})
        self.assertIn("自主执行", s)
        self.assertIn("boom", s)


# ── 3. wiring + status honesty ───────────────────────────────────────────────

class TestPostMarketWiring(unittest.TestCase):
    """Formatter is appended to the post-market human-facing output, and
    execution_decisions is persisted; degraded/rolled-back/halt surface a
    warning line; plain shadow rejects do NOT add a warning."""

    def _coord(self):
        from coordinator import MultiAgentCoordinator
        return MultiAgentCoordinator.__new__(MultiAgentCoordinator)

    def test_formatter_included_in_post_market_output(self):
        c = self._coord()
        wf = {"execution": _summary_with_rejects(), "warnings": [], "events": []}
        out = c._generate_post_market_output({}, {}, wf)
        self.assertIn("🤖 自主执行(shadow)", out)
        self.assertIn("（影子模式 · 未真实成交）", out)

    def test_halt_banner_in_post_market_output(self):
        c = self._coord()
        wf = {"execution": _summary_halt(), "warnings": [], "events": []}
        out = c._generate_post_market_output({}, {}, wf)
        self.assertIn("⛔", out)

    def test_apply_execution_decisions_persists_compact_view(self):
        c = self._coord()
        wf = {"execution": _summary_with_rejects(), "warnings": [], "events": []}
        c._apply_execution_observability(wf)
        self.assertIn("execution_decisions", wf)
        self.assertEqual(wf["execution_decisions"]["accounts"]["main"]["rejected"], 4)
        # full execution summary kept too
        self.assertIn("execution", wf)

    def test_shadow_rejects_do_not_add_warning(self):
        c = self._coord()
        wf = {"execution": _summary_with_rejects(), "warnings": [], "events": []}
        c._apply_execution_observability(wf)
        self.assertEqual(wf["warnings"], [])

    def test_rolled_back_adds_warning(self):
        c = self._coord()
        wf = {"execution": _summary_rolled_back(), "warnings": [], "events": []}
        c._apply_execution_observability(wf)
        self.assertTrue(any("回滚" in w or "rolled" in w.lower() for w in wf["warnings"]))

    def test_halt_adds_warning(self):
        c = self._coord()
        wf = {"execution": _summary_halt(), "warnings": [], "events": []}
        c._apply_execution_observability(wf)
        self.assertTrue(any("自主执行已停" in w or "halt" in w.lower() for w in wf["warnings"]))


if __name__ == "__main__":
    unittest.main()
