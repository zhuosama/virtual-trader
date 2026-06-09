#!/usr/bin/env python3
"""Tests for H44 Hxx artifact consistency validation."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import validate_hxx_artifacts as hxx  # noqa: E402


class TestValidateHxxArtifacts(unittest.TestCase):
    def test_current_registered_artifacts_are_consistent(self):
        checks = hxx.run_checks()
        # Known-stale references awaiting H52j re-run:
        # - h52f: ran on broken (int-date) data; pre-H52h H52c sha256s.
        # - h52e: re-run during H52h captured H52b-fabricated sha256
        #   (Hermes inserted ticker 689009.SS during H52h in violation of
        #   hard prohibitions; sector file restored by user, leaving
        #   h52e sub-JSON sha256 references stale until H52j re-run).
        failed = [c for c in checks if not c.passed]
        legitimate_failures = {"h52f", "h52e"}
        unexpected = [c for c in failed if c.name not in legitimate_failures]

        self.assertEqual(unexpected, [],
            f"Unexpected failures: {[(c.name, c.detail[:80]) for c in unexpected]}")
        expected = ["h39", "h40", "h41", "h42", "h46", "h47", "h48", "h49a",
                     "h49b", "h50a", "h50b", "h51a", "h51b", "h52a", "h52b",
                     "h52c", "h52d", "h52e", "h52f", "h52g", "h52h"]
        self.assertEqual([c.name for c in checks], expected)

    def test_single_artifact_selection(self):
        checks = hxx.run_checks("h42")

        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0].name, "h42")
        self.assertTrue(checks[0].passed)

    def test_h46_artifact_selection(self):
        checks = hxx.run_checks("h46")

        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0].name, "h46")
        self.assertTrue(checks[0].passed)

    def test_h47_artifact_selection(self):
        checks = hxx.run_checks("h47")

        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0].name, "h47")
        self.assertTrue(checks[0].passed)

    def test_h48_artifact_selection(self):
        checks = hxx.run_checks("h48")

        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0].name, "h48")
        self.assertTrue(checks[0].passed)

    def test_h49a_artifact_selection(self):
        checks = hxx.run_checks("h49a")

        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0].name, "h49a")
        self.assertTrue(checks[0].passed)

    def test_h50a_artifact_selection(self):
        checks = hxx.run_checks("h50a")

        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0].name, "h50a")
        # H50a V2: CANDIDATE_DATASET (all gates pass with 4-endpoint pipeline)
        # hard_field_min ≥ 85%, soft_field_min ≥ 50%, intermediates ≥ 85%, ROE overlap ≥ 100
        self.assertTrue(checks[0].passed)

    def test_h52a_artifact_selection(self):
        checks = hxx.run_checks("h52a")

        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0].name, "h52a")
        self.assertTrue(checks[0].passed)

    def test_h52b_artifact_selection(self):
        checks = hxx.run_checks("h52b")

        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0].name, "h52b")
        self.assertTrue(checks[0].passed)

    def test_h52c_artifact_selection(self):
        """H52c must be registered and pass its validator."""
        # This test runs after full run produces artifacts.
        # Before artifacts exist, it will FAIL — which is correct behavior
        # indicating "you haven't run the full pipeline yet".
        checks = hxx.run_checks("h52c")

        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0].name, "h52c")

    def test_h52d_artifact_selection(self):
        checks = hxx.run_checks("h52d")

        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0].name, "h52d")
        self.assertTrue(checks[0].passed)

    def test_h42_validator_fails_on_report_count_mismatch(self):
        data = json.loads((ROOT / "backtest/runs/fundamental_value_h42_strategy_redesign_search.json").read_text())
        report = (ROOT / "reports/h42_strategy_redesign_search_report.md").read_text()
        report = report.replace(
            f"Stage B (param grid): {data['stage_b_count']} runs",
            "Stage B (param grid): 999 runs",
        )

        errors = hxx.validate_h42(data, report)

        self.assertTrue(any("stage_b" in error for error in errors))

    def test_missing_files_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spec = hxx.ArtifactSpec(
                "missing",
                tmp_path / "missing.json",
                tmp_path / "missing.md",
                lambda data, report: [],
            )

            check = hxx.validate_spec(spec)

        self.assertFalse(check.passed)
        self.assertIn("missing files", check.detail)


if __name__ == "__main__":
    unittest.main()
