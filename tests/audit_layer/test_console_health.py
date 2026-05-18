import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "console"))


class TestConsoleHealth(unittest.TestCase):
    def setUp(self):
        import server

        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        original_home = server.VTRADER_HOME
        self.addCleanup(setattr, server, "VTRADER_HOME", original_home)
        if hasattr(server, "_LEDGER_HEALTH_CACHE"):
            server._LEDGER_HEALTH_CACHE = {"root": None, "expires_at": 0, "value": None}
        server.VTRADER_HOME = Path(self.tmpdir)
        self.server = server

    def _write_json(self, relative_path, data):
        path = Path(self.tmpdir) / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_health_reports_degraded_when_latest_workflow_failed(self):
        self._write_json("agents/workflows/workflow_post_market_20260515_153000.json", {
            "workflow_type": "post_market",
            "status": "failed",
            "error": "attempted relative import with no known parent package",
        })

        with patch.object(self.server, "_ledger_health", return_value={"passed": True, "failures": 0}):
            health = self.server.get_health()

        self.assertFalse(health["ok"])
        self.assertEqual(health["status"], "degraded")
        self.assertEqual(health["business"]["latestWorkflow"]["status"], "failed")
        self.assertTrue(any("latest workflow failed" in issue for issue in health["business"]["issues"]))

    def test_health_treats_uppercase_failed_workflow_as_degraded(self):
        self._write_json("agents/workflows/workflow_post_market_20260515_153000.json", {
            "workflow_type": "post_market",
            "status": "ERROR",
        })

        with patch.object(self.server, "_ledger_health", return_value={"passed": True, "failures": 0}):
            health = self.server.get_health()

        self.assertFalse(health["ok"])
        self.assertEqual(health["status"], "degraded")
        self.assertTrue(any("latest workflow error" in issue for issue in health["business"]["issues"]))

    def test_health_reports_pending_risk_actions_and_audit_retries(self):
        self._write_json("actions/pending.json", [
            {"action_id": "a1", "status": "proposed", "code": "000651", "name": "格力电器"},
            {"action_id": "a2", "status": "acknowledged", "code": "300124", "name": "汇川技术"},
            {"action_id": "a3", "status": "completed"},
        ])
        self._write_json("strategies/proposals/pending_retry/proposal-001.json", {
            "proposal_id": "proposal-001",
            "status": "pending_retry",
        })

        with patch.object(self.server, "_ledger_health", return_value={"passed": True, "failures": 0}):
            health = self.server.get_health()

        self.assertFalse(health["ok"])
        self.assertEqual(health["status"], "degraded")
        self.assertEqual(health["business"]["pendingRiskActions"], 2)
        self.assertEqual(
            [a["code"] for a in health["business"]["pendingRiskActionDetails"]],
            ["000651", "300124"],
        )
        self.assertEqual(health["business"]["pendingAuditRetries"], 1)

    def test_pending_risk_action_details_are_public_safe(self):
        self._write_json("actions/pending.json", [
            {
                "action_id": "risk-1",
                "account": "main",
                "action": "sell",
                "code": "000651",
                "name": "格力电器",
                "status": "proposed",
                "auto_execute": True,
                "current_pct": 0.129,
                "target_pct": 0.1,
                "sell_shares": 800,
                "price": 40.28,
                "expires_at": "2026-05-18T23:59:59",
                "reason": "仓位12.9%超过10%限制",
                "private_path": "/Users/zhuosama/.hermes/virtual-trader/accounts/main.json",
            },
            {"action_id": "risk-2", "status": "completed", "code": "000002"},
        ])

        actions = self.server.get_pending_risk_actions()

        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["account"], "main")
        self.assertEqual(actions[0]["action"], "sell")
        self.assertEqual(actions[0]["code"], "000651")
        self.assertEqual(actions[0]["sellShares"], 800)
        self.assertTrue(actions[0]["autoExecute"])
        self.assertNotIn("private_path", actions[0])
        self.assertNotIn("/Users/", str(actions))

    def test_health_reports_healthy_baseline(self):
        with patch.object(self.server, "_ledger_health", return_value={"passed": True, "failures": 0}):
            health = self.server.get_health()

        self.assertTrue(health["ok"])
        self.assertEqual(health["status"], "healthy")
        self.assertEqual(health["business"]["issues"], [])

    def test_latest_workflow_uses_modified_time_not_filename_order(self):
        old = self._write_json("agents/workflows/workflow_weekly_review_20260516_020028.json", {
            "workflow_type": "weekly_review",
            "status": "degraded",
        })
        new = self._write_json("agents/workflows/workflow_strategy_update_only_20260516_225122.json", {
            "workflow_type": "strategy_update_only",
            "status": "success",
        })
        os.utime(old, (1000, 1000))
        os.utime(new, (2000, 2000))

        with patch.object(self.server, "_ledger_health", return_value={"passed": True, "failures": 0}):
            health = self.server.get_health()

        self.assertTrue(health["ok"])
        self.assertEqual(
            health["business"]["latestWorkflow"]["filename"],
            "workflow_strategy_update_only_20260516_225122.json",
        )

    def test_workflow_list_is_bounded_to_recent_items(self):
        for i in range(25):
            path = self._write_json(f"agents/workflows/workflow_post_market_202605{i+1:02d}_153000.json", {
                "workflow_type": "post_market",
                "status": "success",
            })
            os.utime(path, (1000 + i, 1000 + i))

        workflows = self.server.get_workflows()

        self.assertEqual(len(workflows), 20)
        self.assertEqual(workflows[0]["filename"], "workflow_post_market_20260525_153000.json")
        self.assertEqual(workflows[-1]["filename"], "workflow_post_market_20260506_153000.json")

    def test_workflow_list_includes_safe_warning_and_event_summary(self):
        self._write_json("agents/workflows/workflow_post_market_20260518_153000.json", {
            "workflow_type": "post_market",
            "status": "degraded",
            "warnings": [
                "审计层 INFRA_ERROR，待重试",
                "private path /Users/zhuosama/.hermes/virtual-trader/accounts/main.json",
            ],
            "events": ["1 条风控减仓已自动执行"],
            "final_output": "private report should be stripped",
        })

        workflows = self.server.get_workflows()

        self.assertEqual(workflows[0]["warningCount"], 2)
        self.assertEqual(workflows[0]["eventCount"], 1)
        self.assertIn("INFRA_ERROR", workflows[0]["warningSummary"][0])
        self.assertIn("风控减仓", workflows[0]["eventSummary"][0])
        self.assertNotIn("final_output", workflows[0])
        self.assertNotIn("/Users/zhuosama", str(workflows[0]))

    def test_workflow_warning_redacts_non_user_absolute_paths(self):
        self._write_json("agents/workflows/workflow_post_market_20260518_153000.json", {
            "workflow_type": "post_market",
            "status": "degraded",
            "warnings": [
                "temp path /var/folders/abc/private.log",
                "tmp path /tmp/hermes-secret.log",
                "linux path /home/hermes/.env",
                "volume path /Volumes/Backup/Hermes/private.json",
            ],
        })

        workflows = self.server.get_workflows()

        self.assertNotIn("/var/folders", str(workflows[0]))
        self.assertNotIn("/tmp/hermes", str(workflows[0]))
        self.assertNotIn("/home/hermes", str(workflows[0]))
        self.assertNotIn("/Volumes/Backup", str(workflows[0]))
        self.assertIn("<LOCAL_PATH>", str(workflows[0]))

    def test_get_accounts_adds_position_pnl_pct_from_unrealized_field(self):
        self._write_json("accounts/main.json", {
            "id": "main",
            "positions": [{
                "code": "000651",
                "name": "格力电器",
                "unrealized_pnl_pct": 6.01,
            }],
        })

        accounts = self.server.get_accounts()

        self.assertEqual(accounts[0]["positions"][0]["pnl_pct"], 6.01)

    def test_get_accounts_preserves_explicit_zero_position_pnl(self):
        self._write_json("accounts/main.json", {
            "id": "main",
            "positions": [{
                "code": "000651",
                "pnl_pct": 0.0,
                "pnl": 0,
                "unrealized_pnl_pct": 6.01,
                "unrealized_pnl": 100,
            }],
        })

        accounts = self.server.get_accounts()

        self.assertEqual(accounts[0]["positions"][0]["pnl_pct"], 0.0)
        self.assertEqual(accounts[0]["positions"][0]["pnl"], 0)

    def test_get_strategies_normalizes_iteration_history_to_list(self):
        self._write_json("strategies/active.json", {
            "main_strategy": {
                "name": "Main",
                "version": "1.0.5",
                "iteration_history": "v1.0.0→v1.0.1(底仓建仓)→v1.0.2(风控)",
            },
            "lab_strategy": {
                "name": "Lab",
                "version": "1.0.6",
                "iteration_history": ["v1.0.0", "v1.0.1"],
            },
        })

        strategies = self.server.get_strategies()

        self.assertEqual(strategies[0]["iteration_history"], ["v1.0.0", "v1.0.1(底仓建仓)", "v1.0.2(风控)"])
        self.assertEqual(strategies[1]["iteration_history"], ["v1.0.0", "v1.0.1"])

    def test_get_strategies_normalizes_json_encoded_iteration_history(self):
        self._write_json("strategies/active.json", {
            "main_strategy": {
                "name": "Main",
                "version": "1.0.5",
                "iteration_history": "[\"v1.0.0\", \"v1.0.1\"]",
                "learned_lessons": "[\"lesson one\", \"lesson two\"]",
            },
        })

        strategies = self.server.get_strategies()

        self.assertEqual(strategies[0]["iteration_history"], ["v1.0.0", "v1.0.1"])
        self.assertEqual(strategies[0]["learned_lessons"], ["lesson one", "lesson two"])

    def test_get_strategies_prefers_canonical_keys_without_duplicates(self):
        self._write_json("strategies/active.json", {
            "main_strategy": {"name": "Main Canonical", "version": "1.0.5"},
            "lab_strategy": {"name": "Lab Canonical", "version": "1.0.6"},
            "main": {"name": "Main Legacy", "version": "0.9.0"},
            "lab": {"name": "Lab Legacy", "version": "0.9.1"},
        })

        strategies = self.server.get_strategies()
        summary = self.server.get_summary()

        self.assertEqual([s["id"] for s in strategies], ["main-v1.0.5", "lab-v1.0.6"])
        self.assertEqual([s["version"] for s in summary["strategies"]], ["v1.0.5", "v1.0.6"])

    def test_get_strategies_uses_legacy_keys_when_canonical_keys_absent(self):
        self._write_json("strategies/active.json", {
            "main": {"name": "Main Legacy", "version": "0.9.0"},
            "lab": {"name": "Lab Legacy", "version": "0.9.1"},
        })

        strategies = self.server.get_strategies()

        self.assertEqual([s["id"] for s in strategies], ["main-v0.9.0", "lab-v0.9.1"])

    def test_backtests_payload_uses_active_strategy_versions(self):
        self._write_json("strategies/active.json", {
            "main_strategy": {"name": "Main Next", "version": "2.0.0"},
            "lab_strategy": {"name": "Lab Next", "version": "2.1.0"},
        })

        with patch.object(self.server.backtest_adapter, "list_runs", return_value=[]):
            payload = self.server.get_backtests_payload()

        self.assertEqual(
            [s["id"] for s in payload["strategies"]],
            ["main-v2.0.0", "lab-v2.1.0"],
        )
        self.assertIn("Main Next", payload["strategies"][0]["label"])

    def test_ledger_health_is_cached_between_health_calls(self):
        validator = MagicMock()
        validator.validate.return_value = [{"inv": "INV-1", "status": "PASS", "msg": "ok"}]

        with patch.object(self.server, "LedgerValidator", return_value=validator):
            first = self.server.get_health()
            second = self.server.get_health()

        self.assertTrue(first["business"]["ledger"]["passed"])
        self.assertTrue(second["business"]["ledger"]["passed"])
        self.assertEqual(validator.validate.call_count, 1)

    def test_ledger_exception_is_redacted_in_health_response(self):
        with patch.object(self.server, "LedgerValidator", side_effect=RuntimeError("/tmp/private/path failed")):
            ledger = self.server._ledger_health(use_cache=False)

        self.assertFalse(ledger["passed"])
        self.assertEqual(ledger["error"], "validator unavailable")
        self.assertNotIn("/tmp/private", str(ledger))

    def test_ledger_failures_do_not_expose_raw_validator_messages(self):
        validator = MagicMock()
        validator.validate.return_value = [{
            "inv": "INV-1",
            "status": "FAIL",
            "msg": "absolute path leaked: /Users/zhuosama/.hermes/virtual-trader/accounts/main.json",
        }]

        with patch.object(self.server, "LedgerValidator", return_value=validator):
            ledger = self.server._ledger_health(use_cache=False)

        self.assertFalse(ledger["passed"])
        self.assertEqual(ledger["failures"], 1)
        self.assertNotIn("failedResults", ledger)
        self.assertNotIn("/Users/zhuosama", str(ledger))

    def test_ledger_health_cache_is_locked_across_concurrent_calls(self):
        validator = MagicMock()

        def slow_validate():
            time.sleep(0.05)
            return [{"inv": "INV-1", "status": "PASS", "msg": "ok"}]

        validator.validate.side_effect = slow_validate
        results = []

        with patch.object(self.server, "LedgerValidator", return_value=validator):
            threads = [
                threading.Thread(target=lambda: results.append(self.server._ledger_health()))
                for _ in range(5)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        self.assertEqual(len(results), 5)
        self.assertEqual(validator.validate.call_count, 1)

    def test_not_found_response_does_not_echo_query_string(self):
        handler = self.server.ConsoleHandler.__new__(self.server.ConsoleHandler)
        handler.path = "/missing?token=secret"
        captured = {}
        handler._json = lambda data, status=200: captured.update({"data": data, "status": status})

        handler._not_found()

        self.assertEqual(captured["status"], 404)
        self.assertEqual(captured["data"]["path"], "/missing")
        self.assertNotIn("secret", str(captured["data"]))

    def test_api_virtual_trader_health_alias_returns_health(self):
        handler = self.server.ConsoleHandler.__new__(self.server.ConsoleHandler)
        handler.path = "/api/virtual-trader/health"
        captured = {}
        handler._json = lambda data, status=200: captured.update({"data": data, "status": status})

        with patch.object(self.server, "_ledger_health", return_value={"passed": True, "failures": 0}):
            handler.do_GET()

        self.assertEqual(captured["status"], 200)
        self.assertTrue(captured["data"]["ok"])
        self.assertEqual(captured["data"]["status"], "healthy")

    def test_summary_does_not_expose_absolute_vtrader_home(self):
        summary = self.server.get_summary()

        self.assertNotIn("vtrader_home", summary)
        self.assertNotIn("/Users/", str(summary))

    def test_summary_includes_business_health_for_home_page(self):
        self._write_json("actions/pending.json", [
            {"action_id": "risk-1", "status": "proposed"},
        ])

        with patch.object(self.server, "_ledger_health", return_value={"passed": True, "failures": 0}):
            summary = self.server.get_summary()

        self.assertEqual(summary["health"]["status"], "degraded")
        self.assertEqual(summary["health"]["pendingRiskActions"], 1)
        self.assertIn("pending risk action", " ".join(summary["health"]["issues"]))
        self.assertNotIn("/Users/", str(summary))

    def test_health_includes_public_snapshot_freshness(self):
        generated_at = datetime.now(timezone.utc).isoformat()
        self._write_json("public-export/manifest.json", {
            "generatedAt": generated_at,
            "snapshotPath": "public-snapshot.json",
            "schemaVersion": "1.0.0",
            "scanPassed": True,
        })
        self._write_json("public-export/public-snapshot.json", {"generatedAt": generated_at})

        with patch.object(self.server, "_ledger_health", return_value={"passed": True, "failures": 0}):
            health = self.server.get_health()

        public_export = health["business"]["publicExport"]
        self.assertEqual(public_export["status"], "fresh")
        self.assertTrue(public_export["exists"])
        self.assertEqual(public_export["snapshotPath"], "public-export/public-snapshot.json")
        self.assertEqual(public_export["schemaVersion"], "1.0.0")
        self.assertIsInstance(public_export["ageSeconds"], int)
        self.assertNotIn("/Users/", str(public_export))

    def test_health_degrades_when_public_snapshot_is_stale(self):
        self._write_json("public-export/manifest.json", {
            "generatedAt": "2026-01-01T00:00:00+00:00",
            "snapshotPath": "public-snapshot.json",
            "schemaVersion": "1.0.0",
            "scanPassed": True,
        })
        self._write_json("public-export/public-snapshot.json", {"generatedAt": "2026-01-01T00:00:00+00:00"})

        with patch.object(self.server, "_ledger_health", return_value={"passed": True, "failures": 0}):
            health = self.server.get_health()

        self.assertFalse(health["ok"])
        self.assertEqual(health["business"]["publicExport"]["status"], "stale")
        self.assertIn("public snapshot stale", " ".join(health["business"]["issues"]))

    def test_template_json_injection_cannot_close_script_tag(self):
        template_dir = Path(self.tmpdir) / "templates"
        template_dir.mkdir()
        (template_dir / "xss.html").write_text(
            "<script>const data = __DATA_JSON__;</script>",
            encoding="utf-8",
        )

        original_templates_dir = self.server.TEMPLATES_DIR
        self.addCleanup(setattr, self.server, "TEMPLATES_DIR", original_templates_dir)
        self.server.TEMPLATES_DIR = template_dir

        handler = self.server.ConsoleHandler.__new__(self.server.ConsoleHandler)
        captured = {}
        handler._html = lambda html, status=200: captured.update({"html": html, "status": status})

        handler._serve_template("xss.html", {"name": "</script><script>alert(1)</script>"}, "__DATA_JSON__")

        self.assertEqual(captured["status"], 200)
        self.assertNotIn("</script><script>alert(1)</script>", captured["html"])
        self.assertIn("\\u003c/script\\u003e", captured["html"])

    def test_management_templates_include_operational_health_strip(self):
        for template_name in ("data.html", "strategies.html", "backtests.html", "export.html"):
            html = (self.server.TEMPLATES_DIR / template_name).read_text(encoding="utf-8")
            self.assertIn("ops-strip", html, template_name)
            self.assertIn("renderOpsStrip", html, template_name)

    def test_home_template_renders_public_snapshot_health(self):
        html = (self.server.TEMPLATES_DIR / "index.html").read_text(encoding="utf-8")

        self.assertIn("publicExport", html)
        self.assertIn("Public Snapshot", html)

    def test_management_templates_escape_local_data_insertions(self):
        strategies_html = (self.server.TEMPLATES_DIR / "strategies.html").read_text(encoding="utf-8")
        data_html = (self.server.TEMPLATES_DIR / "data.html").read_text(encoding="utf-8")

        self.assertIn("escapeHtml(entry[0])", strategies_html)
        self.assertIn("escapeHtml(s.version || 'n/a')", strategies_html)
        self.assertIn("escapeHtml(s.status || 'n/a')", strategies_html)
        self.assertIn("escapeHtml(p.code || p.stock_code || '—')", data_html)
        self.assertIn("escapeHtml(p.name || '—')", data_html)
        self.assertIn("escapeHtml(a.id || '')", data_html)
        self.assertIn("escapeHtml(a.name || '')", data_html)

    def test_management_routes_inject_business_health_payload(self):
        expected_health = {"status": "degraded", "issues": ["test issue"]}

        for path in (
            "/console/virtual-trader/data",
            "/console/virtual-trader/strategies",
            "/console/virtual-trader/backtests",
            "/console/virtual-trader/export",
        ):
            handler = self.server.ConsoleHandler.__new__(self.server.ConsoleHandler)
            handler.path = path
            captured = {}
            handler._serve_template = lambda template, data, placeholder: captured.update({
                "template": template,
                "data": data,
                "placeholder": placeholder,
            })

            with patch.object(self.server, "get_business_health", return_value=expected_health), \
                    patch.object(self.server.export_adapter, "build_public_snapshot_preview", return_value={"ok": True, "snapshot": {}}):
                handler.do_GET()

            self.assertEqual(captured["data"]["health"], expected_health, path)

    def test_mask_sensitive_masks_scalar_lists_under_sensitive_key(self):
        masked = self.server.mask_sensitive({"api_keys": ["live-key", "sandbox-key"]})

        self.assertEqual(masked["api_keys"], "***")
        self.assertNotIn("live-key", str(masked))

    def test_read_body_rejects_oversized_content_length_without_reading_stream(self):
        class RecordingBody(BytesIO):
            def __init__(self):
                super().__init__(b"{}")
                self.read_size = None

            def read(self, size=-1):
                self.read_size = size
                return super().read(size)

        handler = self.server.ConsoleHandler.__new__(self.server.ConsoleHandler)
        body = RecordingBody()
        handler.rfile = body
        handler.headers = {"Content-Length": str(self.server.MAX_BODY_BYTES * 2)}
        handler.close_connection = False
        captured = {}
        handler._json = lambda data, status=200: captured.update({"data": data, "status": status})

        self.assertIsNone(handler._read_body())
        self.assertEqual(captured["status"], 413)
        self.assertEqual(captured["data"]["error"], "request body too large")
        self.assertTrue(handler.close_connection)
        self.assertIsNone(body.read_size)

    def test_read_body_rejects_negative_content_length_without_reading_stream(self):
        class RecordingBody(BytesIO):
            def __init__(self):
                super().__init__(b"{}")
                self.read_size = None

            def read(self, size=-1):
                self.read_size = size
                return super().read(size)

        handler = self.server.ConsoleHandler.__new__(self.server.ConsoleHandler)
        body = RecordingBody()
        handler.rfile = body
        handler.headers = {"Content-Length": "-1"}
        handler.close_connection = False
        captured = {}
        handler._json = lambda data, status=200: captured.update({"data": data, "status": status})

        self.assertIsNone(handler._read_body())
        self.assertEqual(captured["status"], 400)
        self.assertEqual(captured["data"]["error"], "invalid content length")
        self.assertTrue(handler.close_connection)
        self.assertIsNone(body.read_size)

    def test_read_body_rejects_malformed_json(self):
        handler = self.server.ConsoleHandler.__new__(self.server.ConsoleHandler)
        handler.rfile = BytesIO(b"{bad json")
        handler.headers = {"Content-Length": "9"}
        captured = {}
        handler._json = lambda data, status=200: captured.update({"data": data, "status": status})

        self.assertIsNone(handler._read_body())
        self.assertEqual(captured["status"], 400)
        self.assertEqual(captured["data"]["error"], "invalid json")

    def test_write_endpoint_rejects_missing_console_nonce(self):
        handler = self.server.ConsoleHandler.__new__(self.server.ConsoleHandler)
        handler.headers = {}
        captured = {}
        handler._json = lambda data, status=200: captured.update({"data": data, "status": status})

        self.assertFalse(handler._require_console_nonce())
        self.assertEqual(captured["status"], 403)
        self.assertEqual(captured["data"]["error"], "console nonce required")

    def test_write_endpoint_accepts_matching_console_nonce(self):
        handler = self.server.ConsoleHandler.__new__(self.server.ConsoleHandler)
        handler.headers = {"X-Hermes-Console-Nonce": self.server.CONSOLE_NONCE}
        handler._json = lambda data, status=200: self.fail("valid nonce should not emit response")

        self.assertTrue(handler._require_console_nonce())

    def test_backtest_post_appends_admin_action_log(self):
        body = {"strategyId": "main-v1.0.5", "startDate": "2026-05-01", "endDate": "2026-05-10"}
        handler = self.server.ConsoleHandler.__new__(self.server.ConsoleHandler)
        handler.path = "/api/virtual-trader/backtests"
        handler.headers = {
            "X-Hermes-Console-Nonce": self.server.CONSOLE_NONCE,
            "Content-Length": str(len(json.dumps(body))),
        }
        handler.rfile = BytesIO(json.dumps(body).encode("utf-8"))
        captured = {}
        handler._json = lambda data, status=200: captured.update({"data": data, "status": status})

        result = {
            "status": "completed",
            "runId": "bt-001",
            "reportText": "private report should not be logged",
            "traceback": "private stack should not be logged",
            "dailySeries": [{"date": "2026-05-01"}],
            "tradeSummary": [{"code": "000001"}],
        }
        with patch.object(self.server, "get_backtest_strategy_options", return_value=[{"id": "main-v1.0.5"}]), \
                patch.object(self.server.backtest_adapter, "run_backtest_task", return_value=result):
            handler.do_POST()

        log_path = Path(self.tmpdir) / "logs" / "admin_actions.jsonl"
        entry = json.loads(log_path.read_text(encoding="utf-8").strip())
        self.assertEqual(captured["status"], 201)
        self.assertEqual(entry["operation"], "backtest.run")
        self.assertEqual(entry["path"], "/api/virtual-trader/backtests")
        self.assertEqual(entry["statusCode"], 201)
        self.assertTrue(entry["ok"])
        self.assertEqual(entry["request"]["strategyId"], "main-v1.0.5")
        self.assertEqual(entry["result"]["runId"], "bt-001")
        self.assertEqual(entry["changedFiles"], ["backtest/runs/bt-001.json"])
        self.assertNotIn("reportText", entry["result"])
        self.assertNotIn("traceback", entry["result"])
        self.assertNotIn("dailySeries", entry["result"])
        self.assertNotIn("tradeSummary", entry["result"])

    def test_backtest_post_rejects_unknown_strategy_when_no_active_strategies(self):
        body = {"strategyId": "main-v1.0.5"}
        handler = self.server.ConsoleHandler.__new__(self.server.ConsoleHandler)
        handler.path = "/api/virtual-trader/backtests"
        handler.headers = {
            "X-Hermes-Console-Nonce": self.server.CONSOLE_NONCE,
            "Content-Length": str(len(json.dumps(body))),
        }
        handler.rfile = BytesIO(json.dumps(body).encode("utf-8"))
        captured = {}
        handler._json = lambda data, status=200: captured.update({"data": data, "status": status})

        with patch.object(self.server, "get_backtest_strategy_options", return_value=[]), \
                patch.object(self.server.backtest_adapter, "run_backtest_task") as run_backtest:
            handler.do_POST()

        self.assertEqual(captured["status"], 400)
        self.assertEqual(captured["data"]["error"], "unknown strategyId")
        run_backtest.assert_not_called()
        self.assertFalse((Path(self.tmpdir) / "logs" / "admin_actions.jsonl").exists())

    def test_backtest_post_logs_adapter_exception(self):
        body = {"strategyId": "main-v1.0.5"}
        handler = self.server.ConsoleHandler.__new__(self.server.ConsoleHandler)
        handler.path = "/api/virtual-trader/backtests"
        handler.headers = {
            "X-Hermes-Console-Nonce": self.server.CONSOLE_NONCE,
            "Content-Length": str(len(json.dumps(body))),
        }
        handler.rfile = BytesIO(json.dumps(body).encode("utf-8"))
        captured = {}
        handler._json = lambda data, status=200: captured.update({"data": data, "status": status})

        with patch.object(self.server, "get_backtest_strategy_options", return_value=[{"id": "main-v1.0.5"}]), \
                patch.object(self.server.backtest_adapter, "run_backtest_task", side_effect=RuntimeError("/tmp/private failure")):
            handler.do_POST()

        log_path = Path(self.tmpdir) / "logs" / "admin_actions.jsonl"
        entry = json.loads(log_path.read_text(encoding="utf-8").strip())
        self.assertEqual(captured["status"], 500)
        self.assertFalse(entry["ok"])
        self.assertEqual(entry["operation"], "backtest.run")
        self.assertEqual(entry["changedFiles"], [])
        self.assertNotIn("/tmp/private", str(entry))

    def test_export_post_admin_action_log_redacts_sensitive_fields_and_paths(self):
        body = {"dryRun": False, "api_key": "live-secret"}
        handler = self.server.ConsoleHandler.__new__(self.server.ConsoleHandler)
        handler.path = "/api/virtual-trader/export/public-snapshot"
        handler.headers = {
            "X-Hermes-Console-Nonce": self.server.CONSOLE_NONCE,
            "Content-Length": str(len(json.dumps(body))),
        }
        handler.rfile = BytesIO(json.dumps(body).encode("utf-8"))
        handler._json = lambda data, status=200: None

        result = {
            "ok": True,
            "dryRun": False,
            "written": True,
            "outputPath": "/Users/zhuosama/.hermes/virtual-trader/public-export/public-snapshot.json",
            "manifestPath": "/Users/zhuosama/.hermes/virtual-trader/public-export/manifest.json",
            "scan": {"findings": []},
        }
        with patch.object(self.server.export_adapter, "write_public_snapshot", return_value=result):
            handler.do_POST()

        log_path = Path(self.tmpdir) / "logs" / "admin_actions.jsonl"
        entry = json.loads(log_path.read_text(encoding="utf-8").strip())
        self.assertEqual(entry["operation"], "export.public_snapshot")
        self.assertEqual(entry["request"]["api_key"], "***")
        self.assertEqual(entry["changedFiles"], [
            "public-export/public-snapshot.json",
            "public-export/manifest.json",
        ])
        self.assertNotIn("/Users/zhuosama", str(entry))

    def test_public_snapshot_post_logs_adapter_exception(self):
        body = {"dryRun": False}
        handler = self.server.ConsoleHandler.__new__(self.server.ConsoleHandler)
        handler.path = "/api/virtual-trader/export/public-snapshot"
        handler.headers = {
            "X-Hermes-Console-Nonce": self.server.CONSOLE_NONCE,
            "Content-Length": str(len(json.dumps(body))),
        }
        handler.rfile = BytesIO(json.dumps(body).encode("utf-8"))
        captured = {}
        handler._json = lambda data, status=200: captured.update({"data": data, "status": status})

        with patch.object(self.server.export_adapter, "write_public_snapshot", side_effect=RuntimeError("/var/folders/private failure")):
            handler.do_POST()

        log_path = Path(self.tmpdir) / "logs" / "admin_actions.jsonl"
        entry = json.loads(log_path.read_text(encoding="utf-8").strip())
        self.assertEqual(captured["status"], 500)
        self.assertFalse(entry["ok"])
        self.assertEqual(entry["operation"], "export.public_snapshot")
        self.assertNotIn("/var/folders", str(entry))

    def test_failed_backtest_admin_log_does_not_claim_changed_file(self):
        result = {"status": "failed", "runId": "bt-fail", "error": "disk full"}

        changed = self.server.changed_files_for_admin_log("backtest.run", result)

        self.assertEqual(changed, [])

    def test_import_to_site_post_appends_admin_action_log(self):
        body = {"dryRun": False}
        handler = self.server.ConsoleHandler.__new__(self.server.ConsoleHandler)
        handler.path = "/api/virtual-trader/export/import-to-site"
        handler.headers = {
            "X-Hermes-Console-Nonce": self.server.CONSOLE_NONCE,
            "Content-Length": str(len(json.dumps(body))),
        }
        handler.rfile = BytesIO(json.dumps(body).encode("utf-8"))
        handler._json = lambda data, status=200: None

        result = {
            "ok": True,
            "dryRun": False,
            "written": True,
            "changedFiles": [
                "/Users/zhuosama/obsidian-wiki/site/src/data/virtual-trader/public-snapshot.json",
            ],
        }
        with patch.object(self.server.site_bridge_adapter, "run_site_import", return_value=result):
            handler.do_POST()

        log_path = Path(self.tmpdir) / "logs" / "admin_actions.jsonl"
        entry = json.loads(log_path.read_text(encoding="utf-8").strip())
        self.assertEqual(entry["operation"], "export.import_to_site")
        self.assertEqual(entry["changedFiles"], ["<LOCAL_PATH>"])
        self.assertNotIn("/Users/zhuosama", str(entry))

    def test_import_to_site_post_logs_adapter_exception(self):
        body = {"dryRun": False}
        handler = self.server.ConsoleHandler.__new__(self.server.ConsoleHandler)
        handler.path = "/api/virtual-trader/export/import-to-site"
        handler.headers = {
            "X-Hermes-Console-Nonce": self.server.CONSOLE_NONCE,
            "Content-Length": str(len(json.dumps(body))),
        }
        handler.rfile = BytesIO(json.dumps(body).encode("utf-8"))
        captured = {}
        handler._json = lambda data, status=200: captured.update({"data": data, "status": status})

        with patch.object(self.server.site_bridge_adapter, "run_site_import", side_effect=RuntimeError("/home/hermes/private failure")):
            handler.do_POST()

        log_path = Path(self.tmpdir) / "logs" / "admin_actions.jsonl"
        entry = json.loads(log_path.read_text(encoding="utf-8").strip())
        self.assertEqual(captured["status"], 500)
        self.assertFalse(entry["ok"])
        self.assertEqual(entry["operation"], "export.import_to_site")
        self.assertNotIn("/home/hermes", str(entry))

    def test_import_to_site_dry_run_admin_log_has_no_changed_files(self):
        result = {
            "ok": True,
            "dryRun": True,
            "written": False,
            "changedFiles": ["/Users/zhuosama/obsidian-wiki/site/src/data/virtual-trader/public-snapshot.json"],
        }

        changed = self.server.changed_files_for_admin_log("export.import_to_site", result)

        self.assertEqual(changed, [])

    def test_unknown_post_with_valid_nonce_returns_404_without_admin_log(self):
        handler = self.server.ConsoleHandler.__new__(self.server.ConsoleHandler)
        handler.path = "/api/virtual-trader/unknown"
        handler.headers = {"X-Hermes-Console-Nonce": self.server.CONSOLE_NONCE}
        captured = {}
        handler._json = lambda data, status=200: captured.update({"data": data, "status": status})

        handler.do_POST()

        self.assertEqual(captured["status"], 404)
        self.assertFalse((Path(self.tmpdir) / "logs" / "admin_actions.jsonl").exists())

    def test_admin_log_sensitive_detection_does_not_mask_benign_key_names(self):
        payload = {
            "cacheKey": "cache-v1",
            "lookupKey": "lookup-v1",
            "apiKey": "secret",
            "access_key": "secret",
            "secret": "secret",
        }

        sanitized = self.server.sanitize_for_admin_log(payload)

        self.assertEqual(sanitized["cacheKey"], "cache-v1")
        self.assertEqual(sanitized["lookupKey"], "lookup-v1")
        self.assertEqual(sanitized["apiKey"], "***")
        self.assertEqual(sanitized["access_key"], "***")
        self.assertEqual(sanitized["secret"], "***")


if __name__ == "__main__":
    unittest.main()
