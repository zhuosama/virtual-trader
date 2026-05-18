import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "console"))


def _compatible_snapshot():
    account = {
        "label": "Demo",
        "returnPct": 1.2,
        "dailyReturnPct": 0.1,
        "maxDrawdownPct": 2.3,
        "positionPct": 44.5,
        "tradeCount": 3,
        "winRatePct": 50.0,
        "updatedAt": "2026-05-15T15:00:00",
    }
    return {
        "generatedAt": "2026-05-15T15:30:00Z",
        "source": "hermes-virtual-trader",
        "disclaimer": "public snapshot",
        "accounts": {"main": account, "lab": account},
        "strategies": {
            "main": {"name": "Main", "version": "v1.0"},
            "lab": {"name": "Lab", "version": "v1.0"},
        },
        "performance": [{"date": "2026-05-15", "returnPct": 1.0}],
        "workflows": [],
        "agents": [],
        "trustState": {
            "status": "validated",
            "schemaVersion": "1.0.0",
            "generatedAt": "2026-05-15T15:30:00Z",
            "staleAfterHours": 36,
            "ledgerValidation": {"passed": True, "failures": 0, "resultCount": 7},
            "audit": {"latestDecision": "AUTO_MERGE", "latestReviewedAt": "2026-05-15T14:00:00Z"},
            "workflows": {"latestStatus": "success", "degradedOrFailedCount": 0},
        },
        "charts": {},
        "lessons": [],
    }


class TestSiteBridgeAdapter(unittest.TestCase):
    def setUp(self):
        import site_bridge_adapter

        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)

        self.export_dir = Path(self.tmpdir) / "public-export"
        self.export_dir.mkdir()
        self.snapshot_path = self.export_dir / "public-snapshot.json"
        self.snapshot_path.write_text(json.dumps(_compatible_snapshot()), encoding="utf-8")

        self.site_root = Path(self.tmpdir) / "site"
        self.site_snapshot_path = self.site_root / "src" / "data" / "virtual-trader" / "public-snapshot.json"
        self.site_charts_dir = self.site_root / "public" / "virtual-trader"

        self.chart_source_dir = Path(self.tmpdir) / "charts"
        self.chart_source_dir.mkdir()

        site_bridge_adapter.EXPORT_DIR = self.export_dir
        site_bridge_adapter.SITE_ROOT = self.site_root
        site_bridge_adapter.SITE_SNAPSHOT_PATH = self.site_snapshot_path
        site_bridge_adapter.SITE_CHARTS_DIR = self.site_charts_dir
        site_bridge_adapter.CHART_SOURCE_DIR = self.chart_source_dir
        site_bridge_adapter.CHART_FILES = []

        self.adapter = site_bridge_adapter

    def test_failed_post_scan_restores_existing_site_snapshot(self):
        original = {"generatedAt": "old", "source": "previous-good-snapshot"}
        self.site_snapshot_path.parent.mkdir(parents=True)
        self.site_snapshot_path.write_text(json.dumps(original), encoding="utf-8")

        with patch.object(self.adapter, "post_import_scan", return_value={
            "passed": False,
            "findingCount": 1,
            "highSeverityCount": 1,
            "findings": [{"path": "$.token", "severity": "high"}],
        }):
            result = self.adapter.run_site_import(dry_run=False)

        self.assertFalse(result["ok"])
        self.assertTrue(result["rolledBack"])
        self.assertEqual(json.loads(self.site_snapshot_path.read_text()), original)
        self.assertIn("rolled back", result["details"])

    def test_compatibility_scan_schema_is_stable_when_snapshot_missing(self):
        self.snapshot_path.unlink()

        result = self.adapter.validate_site_import_compatibility()

        self.assertFalse(result["compatible"])
        self.assertEqual(result["scan"]["highSeverityCount"], 0)

    def test_compatibility_requires_public_trust_state(self):
        snapshot = _compatible_snapshot()
        snapshot.pop("trustState")
        self.snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")

        result = self.adapter.validate_site_import_compatibility()

        self.assertFalse(result["compatible"])
        self.assertIn("trustState", result["fieldCheck"]["missing"])

    def test_failed_post_scan_removes_new_site_snapshot_when_no_previous_file(self):
        with patch.object(self.adapter, "post_import_scan", return_value={
            "passed": False,
            "findingCount": 1,
            "highSeverityCount": 1,
            "findings": [{"path": "$.token", "severity": "high"}],
        }):
            result = self.adapter.run_site_import(dry_run=False)

        self.assertFalse(result["ok"])
        self.assertTrue(result["rolledBack"])
        self.assertFalse(self.site_snapshot_path.exists())

    def test_copy_failure_after_partial_write_rolls_back_snapshot(self):
        self.adapter.CHART_FILES = ["cumulative_returns.png"]
        chart_source = self.chart_source_dir / "cumulative_returns.png"
        chart_source.write_bytes(b"png")

        real_copy2 = shutil.copy2

        def copy_or_fail(src, dst, *args, **kwargs):
            if Path(dst).name == "cumulative_returns.png":
                raise OSError("simulated chart copy failure")
            return real_copy2(src, dst, *args, **kwargs)

        with patch.object(self.adapter.shutil, "copy2", side_effect=copy_or_fail), \
                patch.object(self.adapter, "post_import_scan") as post_scan:
            result = self.adapter.run_site_import(dry_run=False)

        post_scan.assert_not_called()
        self.assertFalse(result["ok"])
        self.assertTrue(result["rolledBack"])
        self.assertFalse(self.site_snapshot_path.exists())
        self.assertIn("Copy failed", result["details"])

    def test_copy_failure_before_any_write_is_not_reported_as_rolled_back(self):
        with patch.object(self.adapter.shutil, "copy2", side_effect=OSError("first copy failed")):
            result = self.adapter.run_site_import(dry_run=False)

        self.assertFalse(result["ok"])
        self.assertFalse(result["rolledBack"])
        self.assertFalse(self.site_snapshot_path.exists())

    def test_rollback_reports_error_without_deleting_existing_file_when_backup_missing(self):
        self.site_snapshot_path.parent.mkdir(parents=True)
        self.site_snapshot_path.write_text("new bad snapshot", encoding="utf-8")

        errors = self.adapter._rollback_changed_files([{
            "path": self.site_snapshot_path,
            "backup": Path(self.tmpdir) / "missing-backup.json",
            "existed": True,
        }])

        self.assertEqual(len(errors), 1)
        self.assertTrue(self.site_snapshot_path.exists())
        self.assertEqual(self.site_snapshot_path.read_text(encoding="utf-8"), "new bad snapshot")


if __name__ == "__main__":
    unittest.main()
