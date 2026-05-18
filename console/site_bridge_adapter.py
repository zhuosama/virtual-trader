"""Site bridge adapter — import sanitized public snapshot into obsidian-wiki/site.

Provides:
  - validate_site_import_compatibility(): check snapshot matches site schema
  - run_site_import(): copy snapshot + charts to site repo (with scan guard)
  - post_import_scan(): verify written site files are clean

This module writes ONLY to ~/obsidian-wiki/site/src/data/virtual-trader/
and ~/obsidian-wiki/site/public/virtual-trader/. It never runs npm build,
git commit, or git push.
"""

import json
import os
import shutil
import tempfile
from pathlib import Path

# Import the scanner from export_adapter
import sys
CONSOLE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(CONSOLE_DIR))
from export_adapter import scan_sensitive_fields

VT_DIR = Path(os.environ.get("VTRADER_HOME", Path.home() / ".hermes" / "virtual-trader"))
EXPORT_DIR = VT_DIR / "public-export"
SNAPSHOT_FILE = "public-snapshot.json"

# Site repo paths
SITE_ROOT = Path.home() / "obsidian-wiki" / "site"
SITE_SNAPSHOT_PATH = SITE_ROOT / "src" / "data" / "virtual-trader" / "public-snapshot.json"
SITE_CHARTS_DIR = SITE_ROOT / "public" / "virtual-trader"

# Source chart paths
CHART_SOURCE_DIR = VT_DIR / "reports" / "charts"
CHART_FILES = ["cumulative_returns.png", "daily_pnl.png", "drawdown.png", "position_distribution.png"]

# Required top-level fields for site importer compatibility
REQUIRED_TOP_FIELDS = [
    "generatedAt", "source", "disclaimer",
    "accounts", "strategies", "performance",
    "workflows", "agents", "charts", "lessons",
]

# Required account fields
REQUIRED_ACCOUNT_FIELDS = [
    "label", "returnPct", "dailyReturnPct", "maxDrawdownPct",
    "positionPct", "tradeCount", "winRatePct", "updatedAt",
]

# Forbidden fields that must NEVER appear in the site snapshot
FORBIDDEN_FIELDS = {
    "cash", "amount", "initial_capital", "initialCapital",
    "total_value", "totalValue", "portfolio_market_value", "market_value",
    "avg_cost", "buy_price", "sell_price", "cost", "pnl", "realized_pnl",
    "unrealized_pnl", "unrealized", "shares",
    "api_key", "api_secret", "secret", "token", "password",
    "credential", "credentials", "private_key",
    "broker", "broker_account", "account_id", "account_number",
    "order_id", "order_ids", "account_config",
    "final_output", "raw_output", "full_log",
    "reportText", "report_text", "tradeSummary", "trade_summary",
    "raw_trades", "rawTrades",
}


def _find_forbidden_keys(obj, path="$"):
    """Recursively find forbidden key names in a JSON structure."""
    found = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key.lower() in {f.lower() for f in FORBIDDEN_FIELDS}:
                found.append(f"{path}.{key}")
            found.extend(_find_forbidden_keys(value, f"{path}.{key}"))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            found.extend(_find_forbidden_keys(item, f"{path}[{i}]"))
    return found


def validate_site_import_compatibility():
    """Check if the public-export snapshot is compatible with the site importer.

    Returns:
        {
            compatible: bool,
            snapshotExists: bool,
            siteRootExists: bool,
            fieldCheck: { passed, missing, forbidden },
            structureCheck: { passed, issues },
            scan: { passed, findingCount, findings },
            details: str
        }
    """
    result = {
        "compatible": False,
        "snapshotExists": False,
        "siteRootExists": SITE_ROOT.exists(),
        "fieldCheck": {"passed": False, "missing": [], "forbidden": []},
        "structureCheck": {"passed": False, "issues": []},
        "scan": {"passed": False, "findingCount": 0, "highSeverityCount": 0, "findings": []},
        "details": "",
    }

    # Step 1: Check snapshot exists
    snapshot_path = EXPORT_DIR / SNAPSHOT_FILE
    if not snapshot_path.exists():
        result["details"] = f"Snapshot not found at {snapshot_path}. Run Write Local Snapshot first."
        return result
    result["snapshotExists"] = True

    # Step 2: Load and check top-level fields
    try:
        data = json.loads(snapshot_path.read_text())
    except Exception as e:
        result["details"] = f"Failed to parse snapshot: {e}"
        return result

    missing_fields = [f for f in REQUIRED_TOP_FIELDS if f not in data]
    result["fieldCheck"]["missing"] = missing_fields

    # Step 3: Check forbidden fields — scan actual JSON keys, not substring
    found_forbidden = _find_forbidden_keys(data)
    result["fieldCheck"]["forbidden"] = found_forbidden

    if not missing_fields and not found_forbidden:
        result["fieldCheck"]["passed"] = True

    # Step 4: Structure check — accounts
    issues = []
    accounts = data.get("accounts", {})
    if not accounts:
        issues.append("accounts is empty")
    else:
        for acc_key in ("main", "lab"):
            acc = accounts.get(acc_key)
            if not acc:
                issues.append(f"accounts.{acc_key} missing")
                continue
            for field in REQUIRED_ACCOUNT_FIELDS:
                if field not in acc:
                    issues.append(f"accounts.{acc_key}.{field} missing")

    # Step 5: Structure check — strategies
    strategies = data.get("strategies", {})
    if not strategies:
        issues.append("strategies is empty")
    else:
        for strat_key in ("main", "lab"):
            s = strategies.get(strat_key)
            if not s:
                issues.append(f"strategies.{strat_key} missing")
            elif not s.get("name") or not s.get("version"):
                issues.append(f"strategies.{strat_key} missing name/version")

    # Step 6: Structure check — performance
    perf = data.get("performance", [])
    if not perf:
        issues.append("performance is empty (acceptable but noted)")

    result["structureCheck"]["issues"] = issues
    if not issues or all("acceptable" in i for i in issues):
        result["structureCheck"]["passed"] = True

    # Step 7: Sensitive scan
    findings = scan_sensitive_fields(data)
    high_findings = [f for f in findings if f["severity"] == "high"]
    result["scan"] = {
        "passed": len(high_findings) == 0,
        "findingCount": len(findings),
        "highSeverityCount": len(high_findings),
        "findings": findings,
    }

    # Final verdict
    result["compatible"] = (
        result["snapshotExists"]
        and result["fieldCheck"]["passed"]
        and result["structureCheck"]["passed"]
        and result["scan"]["passed"]
    )

    if result["compatible"]:
        result["details"] = "Snapshot is compatible with site importer."
    else:
        blockers = []
        if not result["fieldCheck"]["passed"]:
            if missing_fields:
                blockers.append(f"missing fields: {missing_fields}")
            if found_forbidden:
                blockers.append(f"forbidden fields: {found_forbidden}")
        if not result["structureCheck"]["passed"]:
            blockers.append(f"structure issues: {issues}")
        if not result["scan"]["passed"]:
            blockers.append(f"scan found {len(high_findings)} high-severity findings")
        result["details"] = "Blocked: " + "; ".join(blockers)

    return result


def _backup_path_for(target: Path, backup_root: Path) -> Path:
    try:
        rel = target.relative_to(SITE_ROOT)
    except ValueError:
        rel = Path(target.name)
    return backup_root / rel


def _copy_with_rollback(src: Path, dst: Path, rollback_plan: list, backup_root: Path):
    """Copy a file and record enough state to undo it if post-scan fails."""
    backup_path = None
    existed = dst.exists()
    if dst.exists():
        backup_path = _backup_path_for(dst, backup_root)
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(dst, backup_path)

    rollback_item = {
        "path": dst,
        "backup": backup_path,
        "existed": existed,
    }
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(src, dst)
    except Exception:
        if existed or dst.exists():
            rollback_plan.append(rollback_item)
        raise
    rollback_plan.append(rollback_item)


def _rollback_changed_files(rollback_plan: list):
    errors = []
    for item in reversed(rollback_plan):
        path = item["path"]
        backup = item["backup"]
        try:
            if item["existed"]:
                if backup and backup.exists():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(backup, path)
                else:
                    errors.append({
                        "path": str(path),
                        "error": "Backup missing; cannot restore original",
                    })
            elif path.exists():
                path.unlink()
        except Exception as e:
            errors.append({"path": str(path), "error": str(e)})
    return errors


def run_site_import(dry_run=True):
    """Import the public snapshot into the site repo.

    Args:
        dry_run: If True, only validate — don't write to site.

    Returns:
        {
            ok: bool,
            dryRun: bool,
            compatible: bool,
            written: bool,
            changedFiles: [],
            postScan: { passed, findings },
            command: str,
            details: str
        }
    """
    result = {
        "ok": True,
        "dryRun": dry_run,
        "compatible": False,
        "written": False,
        "rolledBack": False,
        "rollbackErrors": [],
        "changedFiles": [],
        "postScan": {"passed": True, "findingCount": 0, "findings": []},
        "command": "",
        "details": "",
    }

    # Step 1: Validate compatibility
    compat = validate_site_import_compatibility()
    result["compatible"] = compat["compatible"]

    if not compat["compatible"]:
        result["ok"] = False
        result["details"] = compat["details"]
        return result

    # Step 2: Dry run — stop here
    if dry_run:
        result["details"] = "Dry run: snapshot is compatible. No files written."
        return result

    # Step 3: Write snapshot to site
    snapshot_path = EXPORT_DIR / SNAPSHOT_FILE
    backup_root = Path(tempfile.mkdtemp(prefix="hermes-site-import-rollback-"))
    rollback_plan = []

    try:
        _copy_with_rollback(snapshot_path, SITE_SNAPSHOT_PATH, rollback_plan, backup_root)
        result["changedFiles"].append(str(SITE_SNAPSHOT_PATH))

        # Step 4: Copy chart PNGs (if source exists)
        for chart_name in CHART_FILES:
            src = CHART_SOURCE_DIR / chart_name
            dst = SITE_CHARTS_DIR / chart_name
            if src.exists():
                _copy_with_rollback(src, dst, rollback_plan, backup_root)
                result["changedFiles"].append(str(dst))
    except Exception as e:
        rollback_errors = _rollback_changed_files(rollback_plan)
        result["rolledBack"] = bool(rollback_plan) and len(rollback_errors) == 0
        result["rollbackErrors"] = rollback_errors
        shutil.rmtree(backup_root, ignore_errors=True)
        result["ok"] = False
        result["details"] = (
            f"Copy failed: {e}. "
            + ("Import rolled back." if result["rolledBack"]
               else "Rollback attempted but errors occurred. Review rollbackErrors manually.")
        )
        return result

    result["written"] = True
    result["command"] = "direct-copy (bypasses importer — snapshot already in correct format)"

    # Step 5: Post-import scan — verify written files are clean
    try:
        post_scan = post_import_scan()
    except Exception as e:
        post_scan = {
            "passed": False,
            "findingCount": 1,
            "highSeverityCount": 1,
            "findings": [{
                "path": str(SITE_SNAPSHOT_PATH),
                "reason": f"Post-import scan failed: {e}",
                "severity": "high",
            }],
        }
    result["postScan"] = post_scan

    if not post_scan["passed"]:
        rollback_errors = _rollback_changed_files(rollback_plan)
        result["rolledBack"] = len(rollback_errors) == 0
        result["rollbackErrors"] = rollback_errors
        shutil.rmtree(backup_root, ignore_errors=True)
        result["ok"] = False
        result["details"] = (
            f"Post-import scan FAILED — {len(post_scan.get('findings', []))} sensitive findings detected. "
            + ("Import rolled back." if result["rolledBack"]
               else "Rollback attempted but errors occurred. Review rollbackErrors manually.")
        )
        return result

    result["details"] = (
        f"Imported successfully. {len(result['changedFiles'])} files written. "
        f"Post-scan passed ({post_scan['findingCount']} findings, 0 high)."
    )
    shutil.rmtree(backup_root, ignore_errors=True)

    return result


def post_import_scan():
    """Scan the site's public snapshot for sensitive fields after import.

    Returns:
        { passed: bool, findingCount: int, findings: [...] }
    """
    findings = []

    # Scan the site snapshot JSON
    if SITE_SNAPSHOT_PATH.exists():
        try:
            data = json.loads(SITE_SNAPSHOT_PATH.read_text())
            json_findings = scan_sensitive_fields(data)
            findings.extend(json_findings)
        except Exception as e:
            findings.append({
                "path": str(SITE_SNAPSHOT_PATH),
                "reason": f"Failed to parse: {e}",
                "severity": "high",
            })

    # Scan chart filenames (PNGs shouldn't contain text secrets, but check names)
    if SITE_CHARTS_DIR.exists():
        for f in SITE_CHARTS_DIR.iterdir():
            if f.suffix.lower() in (".png", ".jpg", ".jpeg", ".svg"):
                continue  # Skip binary images
            # If there are any non-image files, scan them
            try:
                content = f.read_text(errors="ignore")
                text_findings = scan_sensitive_fields({"content": content}, path=str(f))
                findings.extend(text_findings)
            except Exception:
                pass

    high_findings = [f for f in findings if f["severity"] == "high"]

    return {
        "passed": len(high_findings) == 0,
        "findingCount": len(findings),
        "highSeverityCount": len(high_findings),
        "findings": findings,
    }
