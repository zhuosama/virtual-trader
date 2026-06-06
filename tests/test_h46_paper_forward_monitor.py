#!/usr/bin/env python3
"""Tests for H46 paper-only forward monitor."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/h46_paper_forward_monitor.py"
sys.path.insert(0, str(ROOT / "scripts"))

import h46_paper_forward_monitor as h46  # noqa: E402


class TestH46PaperForwardMonitor(unittest.TestCase):
    def test_payload_is_research_only_and_paper_only(self):
        payload = h46.build_payload(top_n=2)

        self.assertEqual(payload["task"], "H46")
        self.assertEqual(payload["status"], "RESEARCH_ONLY")
        self.assertTrue(payload["paper_only"])
        self.assertEqual(payload["summary"]["candidate_count"], 5)
        self.assertEqual(payload["summary"]["paper_only_count"], 5)
        self.assertEqual(payload["summary"]["total_gate_pass"], 0)

    def test_worldquant_reference_is_design_reference_only(self):
        payload = h46.build_payload(top_n=1)
        reference = payload["worldquant_reference"]

        self.assertEqual(reference["scope"], "design_reference_only")
        self.assertIn("dataset categories", reference["borrowed_concepts"])
        self.assertIn("analyst estimates", reference["missing_for_production_alpha"])
        self.assertIn("neutralization", reference["monitor_mapping"])

    def test_report_contains_required_monitor_metrics(self):
        payload = h46.build_payload(top_n=1)
        report = h46.build_report(payload)

        self.assertIn("**Status:** RESEARCH_ONLY", report)
        self.assertIn("Daily metrics: hs300_excess_return", report)
        self.assertIn("Gate passed: 0 candidates", report)
        self.assertIn("**RESEARCH_ONLY**", report)

    def test_cli_smoke_writes_disposable_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_path = Path(tmp) / "h46.json"
            report_path = Path(tmp) / "h46.md"
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--top-n",
                    "2",
                    "--output-run",
                    str(run_path),
                    "--output-report",
                    str(report_path),
                ],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                timeout=60,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(run_path.exists())
            self.assertTrue(report_path.exists())
            data = json.loads(run_path.read_text(encoding="utf-8"))
            self.assertEqual(data["status"], "RESEARCH_ONLY")
            self.assertTrue(data["paper_only"])

    def test_candidate_from_h49b_has_registered_at(self):
        """Smoke: candidate_from_h49b returns PaperCandidate with registered_at=2026-05-23."""
        h49b_data = h46.load_json(h46.H49B_RUN)
        top = h49b_data.get("top_candidates_multi_window", [])
        self.assertTrue(top, "H49b run must have at least one candidate")
        cand = h46.candidate_from_h49b(top[0], 1)
        self.assertEqual(cand.source, "H49b")
        self.assertEqual(cand.registered_at, "2026-05-23")
        self.assertEqual(cand.gate_status, "PAPER_ONLY")
        self.assertIsNotNone(cand.metrics.get("total_return"))

    def test_candidate_from_h50b_has_registered_at(self):
        """Smoke: candidate_from_h50b returns PaperCandidate with registered_at=2026-05-23."""
        h50b_data = h46.load_json(h46.H50B_RUN)
        top = h50b_data.get("top_candidates_multi_window", [])
        self.assertTrue(top, "H50b run must have at least one candidate")
        cand = h46.candidate_from_h50b(top[0], 1)
        self.assertEqual(cand.source, "H50b")
        self.assertEqual(cand.registered_at, "2026-05-23")
        self.assertEqual(cand.gate_status, "PAPER_ONLY")
        self.assertIsNotNone(cand.metrics.get("total_return"))


if __name__ == "__main__":
    unittest.main()

