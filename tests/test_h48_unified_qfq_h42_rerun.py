#!/usr/bin/env python3
"""Tests for H48 Unified-QFQ H42 Strategy Rerun."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


class TestH48WrapperSmoke(unittest.TestCase):
    """Smoke tests: wrapper resolves to H47 prices and H48 output paths."""

    def setUp(self):
        self.script = ROOT / "scripts" / "h48_unified_qfq_h42_rerun.py"

    def test_smoke_run_completes_without_error(self):
        """Smoke: --stage-b-limit 1 --top-k 1 writes to /tmp and exits 0."""
        with tempfile.TemporaryDirectory() as tmp:
            run_json = Path(tmp) / "smoke.json"
            run_md = Path(tmp) / "smoke.md"
            result = subprocess.run(
                [
                    sys.executable, str(self.script),
                    "--stage-a-limit", "1",
                    "--stage-b-limit", "1",
                    "--top-k", "1",
                    "--output-run", str(run_json),
                    "--output-report", str(run_md),
                ],
                capture_output=True, text=True, timeout=180,
                cwd=str(ROOT),
            )
            self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")

    def test_price_source_resolves_to_h47(self):
        """Wrapper JSON's price_source.file must end with H47 prices filename."""
        with tempfile.TemporaryDirectory() as tmp:
            run_json = Path(tmp) / "smoke2.json"
            run_md = Path(tmp) / "smoke2.md"
            subprocess.run(
                [
                    sys.executable, str(self.script),
                    "--stage-a-limit", "1",
                    "--stage-b-limit", "1",
                    "--top-k", "1",
                    "--output-run", str(run_json),
                    "--output-report", str(run_md),
                ],
                capture_output=True, timeout=180,
                cwd=str(ROOT),
            )
            data = json.loads(run_json.read_text())
            ps = data["price_source"]
            self.assertTrue(
                ps["file"].endswith("prices_h47_tushare_qfq_candidate.csv"),
                f"price_source.file={ps['file']}",
            )
            self.assertEqual(ps["task"], "h47")
            self.assertEqual(ps["provider"], "tushare:pro_bar:qfq")
            self.assertEqual(ps["benchmark_provider"], "tushare:index_daily")

    def test_wrapper_outputs_land_at_h48_paths(self):
        """Wrapper writes to H48 output paths (not H42)."""
        with tempfile.TemporaryDirectory() as tmp:
            run_json = Path(tmp) / "run.json"
            run_md = Path(tmp) / "run.md"
            subprocess.run(
                [
                    sys.executable, str(self.script),
                    "--stage-a-limit", "1",
                    "--stage-b-limit", "1",
                    "--top-k", "1",
                    "--output-run", str(run_json),
                    "--output-report", str(run_md),
                ],
                capture_output=True, timeout=180,
                cwd=str(ROOT),
            )
            data = json.loads(run_json.read_text())
            self.assertEqual(data["task"], "H48", f"task={data['task']}")
            self.assertIn("price_source", data)
            self.assertIn("verdict", data)
            self.assertIn("gate_pass_count", data)


class TestH48Guards(unittest.TestCase):
    """Ensure hard prohibitions are enforced."""

    def setUp(self):
        self.script = ROOT / "scripts" / "h48_unified_qfq_h42_rerun.py"

    def test_refuses_overwrite_h42_run_json(self):
        """Wrapper must refuse to overwrite the H42 run JSON."""
        result = subprocess.run(
            [
                sys.executable, str(self.script),
                "--output-run", str(ROOT / "backtest/runs/fundamental_value_h42_strategy_redesign_search.json"),
            ],
            capture_output=True, text=True, timeout=60,
            cwd=str(ROOT),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Refusing to overwrite", result.stdout + result.stderr)

    def test_refuses_overwrite_h42_report(self):
        """Wrapper must refuse to overwrite the H42 report."""
        result = subprocess.run(
            [
                sys.executable, str(self.script),
                "--output-report", str(ROOT / "reports/h42_strategy_redesign_search_report.md"),
            ],
            capture_output=True, text=True, timeout=60,
            cwd=str(ROOT),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Refusing to overwrite", result.stdout + result.stderr)

    def test_refuses_missing_prices_file(self):
        """Wrapper exits 1 if prices file doesn't exist."""
        result = subprocess.run(
            [
                sys.executable, str(self.script),
                "--prices-file", "/tmp/nonexistent_h48.csv",
            ],
            capture_output=True, text=True, timeout=60,
            cwd=str(ROOT),
        )
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
