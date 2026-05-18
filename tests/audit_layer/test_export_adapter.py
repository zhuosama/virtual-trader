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


class TestExportAdapter(unittest.TestCase):
    def setUp(self):
        import export_adapter

        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.root = Path(self.tmpdir)
        self.export_dir = self.root / "public-export"

        self._write_json("accounts/main.json", {
            "id": "main",
            "name": "Main",
            "total_pnl_pct": 1.2,
            "daily_pnl_pct": 0.1,
            "position_pct": 33.3,
            "max_drawdown": 0.01,
            "win_rate": 0.5,
            "trade_count": 3,
        })
        self._write_json("accounts/lab.json", {
            "id": "lab",
            "name": "Lab",
            "total_pnl_pct": 2.3,
            "daily_pnl_pct": 0.2,
            "position_pct": 44.4,
            "max_drawdown": 0.02,
            "win_rate": 0.6,
            "trade_count": 4,
        })
        self._write_json("strategies/active.json", {
            "main_strategy": {"name": "Main Strategy", "version": "1.0"},
            "lab_strategy": {"name": "Lab Strategy", "version": "1.1"},
        })
        self._write_json("strategies/performance_history.json", [
            {"date": "2026-05-15", "main_pct": 0.1, "lab_pct": 0.2, "hs300_pct": -0.1},
        ])
        self._write_json("strategies/audit_log.json", [
            {"decision": "AUTO_REJECT", "audited_at": "2026-05-16T20:50:15+00:00"},
        ])

        original_vt_dir = export_adapter.VT_DIR
        original_export_dir = export_adapter.EXPORT_DIR
        self.addCleanup(setattr, export_adapter, "VT_DIR", original_vt_dir)
        self.addCleanup(setattr, export_adapter, "EXPORT_DIR", original_export_dir)
        export_adapter.VT_DIR = self.root
        export_adapter.EXPORT_DIR = self.export_dir
        self.adapter = export_adapter
        ledger_patcher = patch.object(
            export_adapter,
            "_ledger_validation_status",
            return_value={"passed": True, "failures": 0, "resultCount": 7},
            create=True,
        )
        ledger_patcher.start()
        self.addCleanup(ledger_patcher.stop)

    def _write_json(self, relative_path, data):
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_site_snapshot_workflow_latest_run_uses_timestamp_field(self):
        self._write_json("agents/workflows/workflow_post_market_20260515_161813.json", {
            "workflow_type": "post_market",
            "status": "success",
            "timestamp": "2026-05-15T16:18:13+00:00",
        })

        snapshot = self.adapter._build_site_compatible_snapshot()

        self.assertEqual(
            snapshot["workflows"]["postMarket"]["latestRun"],
            "2026-05-16T00:18:13+08:00",
        )
        self.assertEqual(
            snapshot["workflows"]["postMarket"]["latestArtifact"],
            "workflow_post_market_20260515_161813.json",
        )

    def test_public_snapshot_preview_includes_ledger_gate(self):
        preview = self.adapter.build_public_snapshot_preview()

        self.assertTrue(preview["ok"])
        self.assertEqual(preview["gate"]["ledger"]["passed"], True)
        self.assertEqual(preview["gate"]["ledger"]["resultCount"], 7)

    def test_sensitive_scan_deduplicates_blocked_key_findings(self):
        findings = self.adapter.scan_sensitive_fields({"api_key": "redacted"})

        high_paths = [
            f["path"] for f in findings
            if f["severity"] == "high" and f["path"] == "$.api_key"
        ]
        self.assertEqual(high_paths, ["$.api_key"])

    def test_manifest_uses_relative_snapshot_path(self):
        result = self.adapter.write_public_snapshot(dry_run=False)

        self.assertTrue(result["ok"])
        manifest = json.loads((self.export_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["snapshotPath"], "public-snapshot.json")
        self.assertNotIn("/Users/", json.dumps(manifest))
        self.assertNotIn(str(self.root), json.dumps(manifest))

    def test_write_public_snapshot_is_blocked_when_ledger_gate_fails(self):
        with patch.object(
            self.adapter,
            "_ledger_validation_status",
            return_value={"passed": False, "failures": 1, "error": "ledger validation failed"},
            create=True,
        ):
            result = self.adapter.write_public_snapshot(dry_run=False)

        self.assertFalse(result["ok"])
        self.assertFalse(result["written"])
        self.assertEqual(result["gate"]["ledger"]["passed"], False)
        self.assertFalse((self.export_dir / "public-snapshot.json").exists())
        self.assertFalse((self.export_dir / "manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
