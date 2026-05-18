#!/usr/bin/env python3
"""Hermes Virtual Trader — Local Console Gateway (Phase 1+2+3A+3B).

HTTP server bound to 127.0.0.1:8765.
Phase 1: /health, /console/virtual-trader/
Phase 2: read-only Data Manager, Strategy Manager, workflows
Phase 3A: Backtest Center (create, run, view results)
Phase 3B: Backtest Compare (multi-run / multi-strategy comparison)
No external dependencies — uses Python standard library.

Usage:
    python3 server.py              # start on 127.0.0.1:8765
    python3 server.py --port 9999  # custom port (still 127.0.0.1 only)
"""

import json
import os
import re
import secrets
import sys
import signal
import argparse
import hmac
import threading
import time
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# Add console dir to path for backtest_adapter import
CONSOLE_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = CONSOLE_DIR.parent / "scripts"
sys.path.insert(0, str(CONSOLE_DIR))
sys.path.insert(0, str(SCRIPTS_DIR))
import backtest_adapter
import export_adapter
import site_bridge_adapter
from validate_ledger_consistency import LedgerValidator

VTRADER_HOME = Path(os.environ.get("VTRADER_HOME", Path.home() / ".hermes" / "virtual-trader"))
CONSOLE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = CONSOLE_DIR / "templates"

# Sensitive field patterns — never expose in API responses
SENSITIVE_KEYS = frozenset({
    "api_key", "api_secret", "secret", "token", "password",
    "broker", "broker_account", "account_id", "account_number",
    "credential", "credentials", "private_key",
})

# Workflow fields to exclude from API responses
WORKFLOW_EXCLUDE = frozenset({"final_output", "raw_output", "full_log"})
MAX_BODY_BYTES = 1 << 20
LEDGER_HEALTH_TTL_SECONDS = 30
WORKFLOW_LIST_LIMIT = 20
_LEDGER_HEALTH_CACHE = {"root": None, "expires_at": 0, "value": None}
_LEDGER_HEALTH_LOCK = threading.Lock()
CONSOLE_NONCE = secrets.token_urlsafe(24)


def is_sensitive(key: str) -> bool:
    k = key.lower()
    return k in SENSITIVE_KEYS or any(s in k for s in ("secret", "key", "token", "password", "credential"))


def mask_sensitive(d: dict) -> dict:
    """Return a copy with sensitive fields masked."""
    out = {}
    for k, v in d.items():
        if is_sensitive(k):
            out[k] = "***"
        elif isinstance(v, dict):
            out[k] = mask_sensitive(v)
        elif isinstance(v, list):
            out[k] = [mask_sensitive(i) if isinstance(i, dict) else i for i in v]
        else:
            out[k] = v
    return out


def strip_workflow_fields(d: dict) -> dict:
    """Remove heavy/sensitive fields from workflow artifacts."""
    return {k: v for k, v in d.items() if k not in WORKFLOW_EXCLUDE}


def redact_local_paths(text) -> str:
    """Return a compact diagnostic string without local filesystem paths."""
    value = str(text)
    value = value.replace(str(VTRADER_HOME), "<VTRADER_HOME>")
    value = re.sub(r"/Users/[^\s,;:)]+", "<LOCAL_PATH>", value)
    return value


def normalize_list_field(value):
    """Normalize strategy history/list fields for browser templates."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("["):
            try:
                decoded = json.loads(stripped)
            except json.JSONDecodeError:
                decoded = None
            if isinstance(decoded, list):
                return decoded
        delimiter = "→" if "→" in value else "\n"
        return [part.strip() for part in value.split(delimiter) if part.strip()]
    return [value]


def normalize_version(version):
    text = str(version)
    if text == "n/a" or text.startswith("v"):
        return text
    return f"v{text}"


def iter_strategy_keys(raw: dict):
    """Prefer canonical strategy keys; legacy keys are fallback only."""
    canonical = ("main_strategy", "lab_strategy")
    if any(key in raw for key in canonical):
        return canonical
    return ("main", "lab")


def json_for_script(data) -> str:
    """Serialize JSON so it is safe inside an inline script block."""
    return (
        json.dumps(data, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------

def load_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def get_accounts():
    """List all accounts (non-demo only for summary)."""
    accounts_dir = VTRADER_HOME / "accounts"
    if not accounts_dir.exists():
        return []
    result = []
    for f in sorted(accounts_dir.glob("*.json")):
        if ".demo." in f.name:
            continue
        data = load_json(f)
        if data:
            result.append(normalize_account_for_console(mask_sensitive(data)))
    return result


def get_account(account_id: str):
    """Get single account by id."""
    accounts_dir = VTRADER_HOME / "accounts"
    if not accounts_dir.exists():
        return None
    for f in accounts_dir.glob("*.json"):
        if ".demo." in f.name:
            continue
        data = load_json(f)
        if data and data.get("id") == account_id:
            return normalize_account_for_console(mask_sensitive(data))
    return None


def normalize_account_for_console(account: dict) -> dict:
    """Add stable aliases consumed by local console templates."""
    normalized = dict(account)
    positions = []
    for pos in normalized.get("positions", []) or []:
        item = dict(pos)
        if item.get("pnl_pct") is None and item.get("unrealized_pnl_pct") is not None:
            item["pnl_pct"] = item.get("unrealized_pnl_pct")
        if item.get("pnl") is None and item.get("unrealized_pnl") is not None:
            item["pnl"] = item.get("unrealized_pnl")
        positions.append(item)
    normalized["positions"] = positions
    return normalized


def get_strategies():
    """List all strategies (public-safe fields only)."""
    path = VTRADER_HOME / "strategies" / "active.json"
    raw = load_json(path)
    if not raw:
        return []
    result = []
    for key in iter_strategy_keys(raw):
        if key in raw:
            s = raw[key]
            account = "main" if "main" in key else "lab"
            result.append({
                "id": f"{account}-{normalize_version(s.get('version', 'n/a'))}",
                "account": account,
                "name": s.get("name", "n/a"),
                "version": normalize_version(s.get("version", "n/a")),
                "description": s.get("description", ""),
                "status": "active",
                "rules": s.get("rules", {}),
                "parameters": s.get("parameters", {}),
                "iteration_history": normalize_list_field(s.get("iteration_history", [])),
                "learned_lessons": normalize_list_field(s.get("learned_lessons", [])),
            })
    return result


def get_strategy(strategy_id: str):
    """Get single strategy by id (e.g. 'main-v1.0.5')."""
    strategies = get_strategies()
    for s in strategies:
        if s["id"] == strategy_id:
            return s
    # Also match by account name
    for s in strategies:
        if s["account"] == strategy_id:
            return s
    return None


def get_workflows():
    """List workflow artifacts (metadata only, no final_output)."""
    wf_dir = VTRADER_HOME / "agents" / "workflows"
    if not wf_dir.exists():
        return []
    result = []
    workflow_files = sorted(
        wf_dir.glob("*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for f in workflow_files[:WORKFLOW_LIST_LIMIT]:
        data = load_json(f)
        if not data:
            continue
        # Extract metadata only
        meta = {
            "filename": f.name,
            "size_bytes": f.stat().st_size,
            "modified": datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc).isoformat(),
        }
        # Pull safe top-level fields
        for key in ("workflow_type", "type", "status", "created_at", "timestamp", "updated_at",
                     "agent", "task", "account", "trigger"):
            if key in data:
                meta[key] = data[key]
        warnings = normalize_list_field(data.get("warnings", []))
        events = normalize_list_field(data.get("events", []))
        meta["warningCount"] = len(warnings)
        meta["eventCount"] = len(events)
        meta["warningSummary"] = [redact_local_paths(w) for w in warnings[:3]]
        meta["eventSummary"] = [redact_local_paths(e) for e in events[:3]]
        result.append(strip_workflow_fields(meta))
    return result


def _latest_workflow():
    workflows = get_workflows()
    return workflows[0] if workflows else None


def _count_open_risk_actions():
    return len(get_pending_risk_actions())


def get_pending_risk_actions():
    """Return public-safe pending reduce-only risk actions for the local UI."""
    pending = load_json(VTRADER_HOME / "actions" / "pending.json") or []
    if not isinstance(pending, list):
        return []

    open_statuses = {"proposed", "acknowledged"}
    result = []
    for action in pending:
        if not isinstance(action, dict) or action.get("status") not in open_statuses:
            continue
        result.append({
            "actionId": action.get("action_id"),
            "account": action.get("account"),
            "action": action.get("action"),
            "code": action.get("code"),
            "name": action.get("name"),
            "status": action.get("status"),
            "autoExecute": bool(action.get("auto_execute")),
            "currentPct": action.get("current_pct"),
            "targetPct": action.get("target_pct"),
            "projectedPct": action.get("projected_pct"),
            "sellShares": action.get("sell_shares"),
            "expiresAt": action.get("expires_at"),
            "reason": action.get("reason"),
        })
    return result


def _count_pending_audit_retries():
    pending_dir = VTRADER_HOME / "strategies" / "proposals" / "pending_retry"
    if not pending_dir.exists():
        return 0
    return sum(1 for path in pending_dir.glob("*.json") if path.is_file())


def _ledger_health(use_cache=True):
    now = time.time()
    root = str(VTRADER_HOME)

    with _LEDGER_HEALTH_LOCK:
        if (
            use_cache
            and _LEDGER_HEALTH_CACHE.get("root") == root
            and _LEDGER_HEALTH_CACHE.get("value") is not None
            and _LEDGER_HEALTH_CACHE.get("expires_at", 0) > now
        ):
            return _LEDGER_HEALTH_CACHE["value"]

        try:
            validator = LedgerValidator(root=root, strict=True)
            results = validator.validate()
        except Exception as e:
            sys.stderr.write(f"[health] ledger validator unavailable: {e}\n")
            value = {
                "passed": False,
                "failures": 0,
                "error": "validator unavailable",
            }
            _LEDGER_HEALTH_CACHE.update({
                "root": root,
                "expires_at": now + LEDGER_HEALTH_TTL_SECONDS,
                "value": value,
            })
            return value

        failures = [r for r in results if r.get("status") == "FAIL"]
        value = {
            "passed": len(failures) == 0,
            "failures": len(failures),
            "resultCount": len(results),
        }
        _LEDGER_HEALTH_CACHE.update({
            "root": root,
            "expires_at": now + LEDGER_HEALTH_TTL_SECONDS,
            "value": value,
        })
        return value


def get_business_health():
    """Business-level health for the local console gateway."""
    issues = []
    latest = _latest_workflow()
    latest_status = (latest.get("status") if latest else None) or ""
    latest_status_normalized = str(latest_status).lower()
    if latest and latest_status_normalized in {"failed", "degraded", "error"}:
        issues.append(f"latest workflow {latest_status_normalized}")

    ledger = _ledger_health()
    if not ledger.get("passed"):
        issues.append("ledger validation failed")

    pending_risk_actions = get_pending_risk_actions()
    pending_risk = len(pending_risk_actions)
    if pending_risk:
        issues.append(f"{pending_risk} pending risk action(s)")

    pending_audit = _count_pending_audit_retries()
    if pending_audit:
        issues.append(f"{pending_audit} pending audit retry proposal(s)")

    return {
        "status": "healthy" if not issues else "degraded",
        "issues": issues,
        "latestWorkflow": latest,
        "ledger": ledger,
        "pendingRiskActions": pending_risk,
        "pendingRiskActionDetails": pending_risk_actions,
        "pendingAuditRetries": pending_audit,
    }


# ---------------------------------------------------------------------------
# Health & Summary
# ---------------------------------------------------------------------------

def get_health():
    business = get_business_health()
    return {
        "ok": business["status"] == "healthy",
        "status": business["status"],
        "service": "hermes-gateway",
        "modules": {"virtualTrader": "online"},
        "business": business,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def get_summary():
    """Public-safe summary for console home."""
    strategies = []
    raw = load_json(VTRADER_HOME / "strategies" / "active.json")
    if raw:
        for key in iter_strategy_keys(raw):
            if key in raw:
                s = raw[key]
                account = "main" if "main" in key else "lab"
                strategies.append({
                    "account": account,
                    "name": s.get("name", "n/a"),
                    "version": normalize_version(s.get("version", "n/a")),
                    "status": "active",
                })

    return {
        "system": "Hermes Virtual Trader",
        "mode": "local-console",
        "strategies": strategies,
        "health": get_business_health(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def get_backtest_strategy_options():
    options = []
    for s in get_strategies():
        account_label = "Main" if s.get("account") == "main" else "Lab"
        options.append({
            "id": s.get("id"),
            "account": s.get("account"),
            "label": f"{account_label} · {s.get('version', 'n/a')} · {s.get('name', 'n/a')}",
        })
    return options


def get_backtests_payload():
    return {
        "runs": backtest_adapter.list_runs(),
        "strategies": get_backtest_strategy_options(),
    }


# ---------------------------------------------------------------------------
# Request handler
# ---------------------------------------------------------------------------

class ConsoleHandler(BaseHTTPRequestHandler):
    """Request router for Phase 1+2+3A. Handles GET and POST."""

    def log_message(self, fmt, *args):
        sys.stderr.write(f"[{datetime.now().strftime('%H:%M:%S')}] {fmt % args}\n")

    def _json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _html(self, html, status=200):
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _no_content(self):
        self.send_response(204)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _not_found(self):
        self._json({"error": "not found", "path": self.path.split("?")[0]}, 404)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        if length < 0:
            self.close_connection = True
            self._json({"error": "invalid content length"}, 400)
            return None
        if length > MAX_BODY_BYTES:
            self.close_connection = True
            self._json({"error": "request body too large"}, 413)
            return None
        try:
            return json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            self._json({"error": "invalid json"}, 400)
            return None
        except Exception:
            self._json({"error": "invalid request body"}, 400)
            return None

    def _require_console_nonce(self) -> bool:
        nonce = self.headers.get("X-Hermes-Console-Nonce", "")
        if hmac.compare_digest(nonce, CONSOLE_NONCE):
            return True
        self._json({"error": "console nonce required"}, 403)
        return False

    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/") or "/"

        # ---- Health ----
        if path == "/favicon.ico":
            self._no_content()

        elif path in {"/health", "/api/virtual-trader/health"}:
            self._json(get_health())

        # ---- Console pages ----
        elif path == "/console/virtual-trader":
            self._serve_template("index.html", get_summary(), "__SUMMARY_JSON__")

        elif path == "/console/virtual-trader/data":
            self._serve_template("data.html", {
                "accounts": get_accounts(),
                "workflows": get_workflows(),
                "health": get_business_health(),
            }, "__DATA_JSON__")

        elif path == "/console/virtual-trader/strategies":
            self._serve_template("strategies.html", {
                "strategies": get_strategies(),
                "health": get_business_health(),
            }, "__STRATEGIES_JSON__")

        elif path == "/console/virtual-trader/backtests":
            payload = get_backtests_payload()
            payload["health"] = get_business_health()
            self._serve_template("backtests.html", payload, "__BACKTESTS_JSON__")

        elif path == "/console/virtual-trader/backtests/compare":
            qs = parse_qs(urlparse(self.path).query)
            run_ids = qs.get("runs", [""])[0].split(",")
            run_ids = [r.strip() for r in run_ids if r.strip()]
            compare_data = backtest_adapter.compare_runs(run_ids) if run_ids else {"runs": [], "rankings": {}, "privateOnly": True, "privacy": "local-private"}
            compare_data["health"] = get_business_health()
            self._serve_template("compare.html", compare_data, "__COMPARE_JSON__")

        elif path == "/console/virtual-trader/export":
            preview = export_adapter.build_public_snapshot_preview()
            preview["health"] = get_business_health()
            self._serve_template("export.html", preview, "__EXPORT_JSON__")

        elif re.match(r"^/console/virtual-trader/backtests/[\w-]+$", path):
            run_id = path.split("/")[-1]
            result = backtest_adapter.get_run(run_id)
            if result:
                result["health"] = get_business_health()
                self._serve_template("backtest-detail.html", result, "__RUN_JSON__")
            else:
                self._html("<h1>Run not found</h1><p><a href='/console/virtual-trader/backtests'>Back to list</a></p>", 404)

        # ---- API ----
        elif path == "/api/virtual-trader/summary":
            self._json(get_summary())

        elif path == "/api/virtual-trader/accounts":
            self._json({"accounts": get_accounts()})

        elif path == "/api/virtual-trader/strategies":
            self._json({"strategies": get_strategies()})

        elif path == "/api/virtual-trader/workflows":
            self._json({"workflows": get_workflows()})

        elif path == "/api/virtual-trader/risk-actions":
            self._json({"actions": get_pending_risk_actions()})

        elif path == "/api/virtual-trader/backtests":
            self._json(get_backtests_payload())

        elif path == "/api/virtual-trader/backtests/compare":
            qs = parse_qs(urlparse(self.path).query)
            run_ids = qs.get("runs", [""])[0].split(",")
            run_ids = [r.strip() for r in run_ids if r.strip()]
            if len(run_ids) < 2:
                self._json({"error": "at least 2 run IDs required", "provided": len(run_ids)}, 400)
                return
            result = backtest_adapter.compare_runs(run_ids)
            self._json(result)

        elif path == "/api/virtual-trader/export/public-preview":
            preview = export_adapter.build_public_snapshot_preview()
            status = 200 if preview.get("ok") else 422
            self._json(preview, status)

        elif path == "/api/virtual-trader/export/site-compatibility":
            result = site_bridge_adapter.validate_site_import_compatibility()
            self._json(result)

        # ---- API with path params ----
        elif re.match(r"^/api/virtual-trader/accounts/[\w-]+$", path):
            account_id = path.split("/")[-1]
            result = get_account(account_id)
            if result:
                self._json(result)
            else:
                self._json({"error": "account not found", "id": account_id}, 404)

        elif re.match(r"^/api/virtual-trader/strategies/[\w.-]+$", path):
            strategy_id = path.split("/")[-1]
            result = get_strategy(strategy_id)
            if result:
                self._json(result)
            else:
                self._json({"error": "strategy not found", "id": strategy_id}, 404)

        elif re.match(r"^/api/virtual-trader/backtests/[\w-]+$", path):
            run_id = path.split("/")[-1]
            result = backtest_adapter.get_run(run_id)
            if result:
                self._json(result)
            else:
                self._json({"error": "run not found", "runId": run_id}, 404)

        else:
            self._not_found()

    def do_POST(self):
        path = self.path.split("?")[0].rstrip("/") or "/"
        if not self._require_console_nonce():
            return

        if path == "/api/virtual-trader/backtests":
            body = self._read_body()
            if body is None:
                return
            if not body.get("strategyId"):
                self._json({"error": "strategyId is required"}, 400)
                return
            valid_strategy_ids = {s.get("id") for s in get_backtest_strategy_options()}
            if valid_strategy_ids and body.get("strategyId") not in valid_strategy_ids:
                self._json({"error": "unknown strategyId"}, 400)
                return
            # Run backtest (synchronous for Phase 3A)
            result = backtest_adapter.run_backtest_task(body)
            status = 201 if result["status"] == "completed" else 500
            self._json(result, status)

        elif path == "/api/virtual-trader/export/public-snapshot":
            body = self._read_body()
            if body is None:
                return
            dry_run = body.get("dryRun", True)
            result = export_adapter.write_public_snapshot(dry_run=dry_run)
            self._json(result, 200 if result.get("ok") else 422)

        elif path == "/api/virtual-trader/export/import-to-site":
            body = self._read_body()
            if body is None:
                return
            dry_run = body.get("dryRun", True)
            result = site_bridge_adapter.run_site_import(dry_run=dry_run)
            self._json(result, 200 if result.get("ok") else 422)

        else:
            self._not_found()

    def _serve_template(self, template_name, inject_data, placeholder):
        tpl = TEMPLATES_DIR / template_name
        if not tpl.exists():
            self._html(f"<h1>Template not found: {template_name}</h1>", 500)
            return
        html = tpl.read_text("utf-8")
        html = html.replace(placeholder, json_for_script(inject_data))
        html = html.replace("__CONSOLE_NONCE__", CONSOLE_NONCE)
        self._html(html)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Hermes Local Console Gateway")
    parser.add_argument("--port", type=int, default=8765, help="Port (default 8765)")
    args = parser.parse_args()

    host = "127.0.0.1"
    port = args.port

    server = ThreadingHTTPServer((host, port), ConsoleHandler)

    def shutdown(sig, frame):
        print(f"\n[Gateway] Shutting down...")
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    print(f"[Gateway] Hermes Local Console listening on {host}:{port}")
    print(f"[Gateway] Health:      http://{host}:{port}/health")
    print(f"[Gateway] Console:     http://{host}:{port}/console/virtual-trader")
    print(f"[Gateway] Data:        http://{host}:{port}/console/virtual-trader/data")
    print(f"[Gateway] Strategies:  http://{host}:{port}/console/virtual-trader/strategies")
    print(f"[Gateway] Backtests:   http://{host}:{port}/console/virtual-trader/backtests")
    print(f"[Gateway] Compare:     http://{host}:{port}/console/virtual-trader/backtests/compare")
    print(f"[Gateway] Export:      http://{host}:{port}/console/virtual-trader/export")
    print(f"[Gateway] VTRADER_HOME: {VTRADER_HOME}")
    print(f"[Gateway] Press Ctrl+C to stop\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
