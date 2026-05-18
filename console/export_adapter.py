"""Export adapter — public snapshot safety foundation.

Provides:
  - scan_sensitive_fields(): recursive sensitive-field scanner
  - build_public_snapshot_preview(): sanitized aggregated snapshot
  - write_public_snapshot(): write site-importer-compatible JSON
  - ALLOWED_FIELDS / BLOCKED_FIELDS: whitelist/blacklist schema

This module is READ-ONLY on source data. It only writes to
~/.hermes/virtual-trader/public-export/ (never to obsidian-wiki/site).
"""

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

VT_DIR = Path(os.environ.get("VTRADER_HOME", Path.home() / ".hermes" / "virtual-trader"))

# ---------------------------------------------------------------------------
# Field policy
# ---------------------------------------------------------------------------

# Top-level fields allowed in a public snapshot
ALLOWED_FIELDS = {
    "returnPct", "dailyReturnSeries", "maxDrawdownPct", "exposurePct",
    "winRatePct", "tradeCount", "strategyVersion", "agentNames",
    "workflowStatus", "backtestChartSeries", "sanitizedCaseStudies",
    "generatedAt", "auditLayer",
}

# Fields / patterns that must NEVER appear in a public export
BLOCKED_FIELDS = {
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

# Key names that are sensitive (used by scanner)
SENSITIVE_KEY_PATTERNS = {
    "api_key", "api_secret", "secret", "token", "password",
    "credential", "credentials", "private_key",
    "broker", "broker_account", "account_id", "account_number",
    "order_id", "order_ids", "account_config",
    "final_output", "raw_output", "full_log",
    "cash", "amount",
}

# Value patterns that look like leaked secrets
SECRET_VALUE_PATTERNS = [
    re.compile(r"^sk-[A-Za-z0-9]{10,}"),          # OpenAI-style keys
    re.compile(r"^tp-[A-Za-z0-9]{10,}"),          # Tencent-style keys
    re.compile(r"^Bearer\s+\S{10,}", re.I),       # Bearer tokens
    re.compile(r"^api[_-]?key[:\s=]", re.I),      # api-key header
    re.compile(r"^Authorization[:\s=]", re.I),     # Authorization header
    re.compile(r"^[A-Za-z0-9+/]{40,}={0,2}$"),    # Base64 blob (40+ chars)
    re.compile(r"^[0-9a-f]{32,}$", re.I),         # Hex string (32+ chars)
]


# ---------------------------------------------------------------------------
# Sensitive field scanner
# ---------------------------------------------------------------------------

def scan_sensitive_fields(obj, path="$"):
    """Recursively scan a dict/list for sensitive fields.

    Returns a list of findings, each with:
      path:   dotted JSON-path where the issue was found
      reason: human-readable explanation
      severity: "high" (blocked field / secret value) or "warn" (suspicious)
    """
    findings = []

    if isinstance(obj, dict):
        for key, value in obj.items():
            full_path = f"{path}.{key}"
            key_lower = key.lower()

            # Check key name against sensitive patterns
            if key_lower in SENSITIVE_KEY_PATTERNS or any(
                p in key_lower for p in ("secret", "key", "token", "password", "credential")
            ):
                if key_lower not in {f.lower() for f in BLOCKED_FIELDS} and key_lower not in {"runId", "strategyId", "workflowId"}:
                    findings.append({
                        "path": full_path,
                        "reason": f"sensitive key name '{key}'",
                        "severity": "warn",
                    })

            # Check blocked fields
            if key_lower in {f.lower() for f in BLOCKED_FIELDS}:
                findings.append({
                    "path": full_path,
                    "reason": f"blocked field '{key}' — must not appear in public snapshot",
                    "severity": "high",
                })

            # Recurse
            findings.extend(scan_sensitive_fields(value, full_path))

    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            findings.extend(scan_sensitive_fields(item, f"{path}[{i}]"))

    elif isinstance(obj, str):
        # Check value patterns
        for pattern in SECRET_VALUE_PATTERNS:
            if pattern.search(obj):
                findings.append({
                    "path": path,
                    "reason": f"value matches secret pattern ({pattern.pattern[:40]}...)",
                    "severity": "high",
                })
                break

    return findings


# ---------------------------------------------------------------------------
# Data loaders (read-only)
# ---------------------------------------------------------------------------

def _load_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _load_accounts():
    """Load accounts without sensitive absolute values."""
    accounts_dir = VT_DIR / "accounts"
    if not accounts_dir.exists():
        return []
    result = []
    for f in sorted(accounts_dir.glob("*.json")):
        if ".demo." in f.name:
            continue
        data = _load_json(f)
        if not data:
            continue
        # Extract only percentage/exposure fields
        acc = {
            "id": data.get("id"),
            "name": data.get("name"),
            "positionPct": data.get("position_pct"),
            "maxDrawdownPct": data.get("max_drawdown"),
            "winRatePct": data.get("win_rate"),
            "tradeCount": data.get("trade_count"),
            "sharpeRatio": data.get("sharpe_ratio"),
            "totalReturnPct": data.get("total_pnl_pct"),
            "dailyReturnPct": data.get("daily_pnl_pct"),
            "exposurePct": data.get("position_pct"),
        }
        # Position summary (tickers only, no amounts)
        positions = data.get("positions", [])
        acc["positionTickers"] = [
            {"code": p.get("code"), "name": p.get("name")}
            for p in positions
        ]
        acc["positionCount"] = len(positions)
        result.append(acc)
    return result


def _load_strategies():
    """Load strategy metadata (name, version, description, rules, lessons)."""
    raw = _load_json(VT_DIR / "strategies" / "active.json")
    if not raw:
        return {}
    strategies = {}
    for key in ("main_strategy", "lab_strategy", "main", "lab"):
        if key not in raw:
            continue
        s = raw[key]
        account = "main" if "main" in key else "lab"
        strategies[account] = {
            "name": s.get("name"),
            "version": s.get("version"),
            "description": s.get("description"),
            "rules": s.get("rules"),
            "parameters": s.get("parameters"),
            "learnedLessons": s.get("learned_lessons", []),
            "iterationHistory": s.get("iteration_history", []),
        }
    return strategies


def _load_backtest_summary():
    """Load backtest runs — aggregated metrics only, no dailySeries/tradeSummary/reportText."""
    runs_dir = VT_DIR / "backtest" / "runs"
    if not runs_dir.exists():
        return []
    result = []
    for f in sorted(runs_dir.glob("*.json"), reverse=True):
        data = _load_json(f)
        if not data:
            continue
        result.append({
            "runId": data.get("runId"),
            "strategyId": data.get("strategyId"),
            "account": data.get("account"),
            "startDate": data.get("startDate"),
            "endDate": data.get("endDate"),
            "status": data.get("status"),
            "cumulativeReturnPct": data.get("cumulativeReturnPct"),
            "benchmarkReturnPct": data.get("benchmarkReturnPct"),
            "excessReturnPct": data.get("excessReturnPct"),
            "maxDrawdownPct": data.get("maxDrawdownPct"),
            "winRatePct": data.get("winRatePct"),
            "tradeCount": data.get("tradeCount"),
            "sharpeRatio": data.get("sharpeRatio"),
            # dailyReturnSeries: only return % and date, no absolute values
            "dailyReturnSeries": _sanitize_daily_series(data.get("dailySeries", [])),
        })
    return result


def _sanitize_daily_series(series):
    """Strip absolute values from daily series — keep only date + return pct."""
    if not series:
        return []
    return [
        {"date": d.get("date"), "dailyReturnPct": d.get("dailyReturnPct")}
        for d in series
    ]


def _load_workflows():
    """Load workflow metadata (no final_output/raw_output/full_log)."""
    wf_dir = VT_DIR / "agents" / "workflows"
    if not wf_dir.exists():
        return []
    result = []
    workflow_files = sorted(
        wf_dir.glob("*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for f in workflow_files[:20]:
        data = _load_json(f)
        if not data:
            continue
        created_at = (
            data.get("created_at")
            or data.get("timestamp")
            or data.get("updated_at")
            or _workflow_time_from_filename(f.name)
            or datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc).isoformat()
        )
        result.append({
            "filename": f.name,
            "type": data.get("workflow_type") or data.get("type"),
            "status": data.get("status"),
            "agent": data.get("agent"),
            "account": data.get("account"),
            "trigger": data.get("trigger"),
            "createdAt": created_at,
        })
    return result


def _workflow_time_from_filename(filename):
    match = re.match(r"^workflow_[a-z_]+_(\d{8})_(\d{6})\.json$", filename)
    if not match:
        return None
    date_text, time_text = match.groups()
    try:
        dt = datetime(
            int(date_text[:4]),
            int(date_text[4:6]),
            int(date_text[6:8]),
            int(time_text[:2]),
            int(time_text[2:4]),
            int(time_text[4:6]),
            tzinfo=timezone.utc,
        )
    except ValueError:
        return None
    return dt.isoformat()


def _load_audit_layer_summary():
    """Load public-safe audit layer status only."""
    log = _load_json(VT_DIR / "strategies" / "audit_log.json")
    entries = log if isinstance(log, list) else []
    latest = None
    if entries:
        latest = sorted(
            [e for e in entries if isinstance(e, dict)],
            key=lambda e: str(e.get("audited_at") or ""),
        )[-1]

    return {
        "enabled": True,
        "policy": "strategy proposals require audit_layer.review() before commit_approved() can write active.json/changelog",
        "totalReviews": len(entries),
        "latestDecision": latest.get("decision") if latest else None,
        "latestReviewedAt": latest.get("audited_at") if latest else None,
        "publicDetailPolicy": "aggregate status only; proposal ids, reviewer verdict traces, and workflow outputs are private",
    }


# ---------------------------------------------------------------------------
# Public snapshot builder
# ---------------------------------------------------------------------------

def build_public_snapshot_preview():
    """Build a sanitized public snapshot preview.

    Returns:
      {
        "ok": bool,
        "generatedAt": str (ISO),
        "snapshot": { ... },
        "scan": { "passed": bool, "findings": [...] }
      }
    """
    now = datetime.now(timezone.utc).isoformat()

    accounts = _load_accounts()
    strategies = _load_strategies()
    backtests = _load_backtest_summary()
    workflows = _load_workflows()
    audit_layer = _load_audit_layer_summary()

    # Build snapshot using allowed fields only
    snapshot = {
        "generatedAt": now,
        "returnPct": {},
        "maxDrawdownPct": {},
        "winRatePct": {},
        "tradeCount": {},
        "exposurePct": {},
        "strategyVersion": {},
        "agentNames": [],
        "workflowStatus": [],
        "backtestChartSeries": [],
        "sanitizedCaseStudies": [],
        "dailyReturnSeries": {},
        "auditLayer": audit_layer,
    }

    # Aggregate account metrics
    for acc in accounts:
        aid = acc["id"]
        if aid:
            snapshot["returnPct"][aid] = acc.get("totalReturnPct")
            snapshot["maxDrawdownPct"][aid] = acc.get("maxDrawdownPct")
            snapshot["winRatePct"][aid] = acc.get("winRatePct")
            snapshot["tradeCount"][aid] = acc.get("tradeCount")
            snapshot["exposurePct"][aid] = acc.get("exposurePct")

    # Strategy versions
    for account, strat in strategies.items():
        snapshot["strategyVersion"][account] = strat.get("version")

    # Agent names (from workflows)
    agent_set = set()
    for wf in workflows:
        agent = wf.get("agent")
        if agent:
            agent_set.add(agent)
    snapshot["agentNames"] = sorted(agent_set)

    # Workflow status summary
    snapshot["workflowStatus"] = [
        {"type": wf.get("type"), "status": wf.get("status"), "account": wf.get("account")}
        for wf in workflows[:10]
    ]

    # Backtest chart series (aggregated metrics only)
    snapshot["backtestChartSeries"] = backtests

    # Case studies (strategy lessons, sanitized)
    for account, strat in strategies.items():
        lessons = strat.get("learnedLessons", [])
        for lesson in lessons[:5]:
            snapshot["sanitizedCaseStudies"].append({
                "account": account,
                "lesson": lesson,
            })

    # Run sensitive scanner on the snapshot
    findings = scan_sensitive_fields(snapshot)

    # Also scan raw source data for extra safety
    raw_findings = []
    for acc in accounts:
        raw_findings.extend(scan_sensitive_fields(acc, "$.account"))
    raw_findings = [f for f in raw_findings if f["severity"] == "high"]

    # Merge: only include snapshot findings (raw findings are for audit)
    passed = all(f["severity"] != "high" for f in findings)
    ledger = _ledger_validation_status()

    return {
        "ok": passed and ledger.get("passed", False),
        "generatedAt": now,
        "snapshot": snapshot,
        "gate": {
            "ledger": ledger,
        },
        "scan": {
            "passed": passed,
            "findingCount": len(findings),
            "highSeverityCount": sum(1 for f in findings if f["severity"] == "high"),
            "findings": findings,
            "rawAuditFindingCount": len(raw_findings),
        },
    }


# ---------------------------------------------------------------------------
# Performance history loader
# ---------------------------------------------------------------------------

def _load_performance_history():
    """Load daily performance from strategies/performance_history.json."""
    path = VT_DIR / "strategies" / "performance_history.json"
    raw = _load_json(path)
    if not raw or not isinstance(raw, list):
        return []
    result = []
    for row in raw:
        result.append({
            "date": row.get("date"),
            "mainPct": row.get("main_pct"),
            "labPct": row.get("lab_pct"),
            "benchmarkPct": row.get("hs300_pct"),
            "mainBeat": row.get("main_beat"),
            "labBeat": row.get("lab_beat"),
        })
    return result


# ---------------------------------------------------------------------------
# Site-importer-compatible snapshot builder
# ---------------------------------------------------------------------------

def _build_site_compatible_snapshot():
    """Build a snapshot matching the site importer's expected JSON schema.

    Top-level fields: generatedAt, source, disclaimer, accounts, strategies,
    performance, workflows, agents, charts, lessons.
    """
    now = datetime.now(timezone.utc).isoformat()
    accounts_raw = _load_accounts()
    strategies_raw = _load_strategies()
    perf_raw = _load_performance_history()
    workflows_raw = _load_workflows()

    # --- accounts ---
    accounts = {}
    for acc in accounts_raw:
        aid = acc.get("id")
        if not aid:
            continue
        accounts[aid] = {
            "label": acc.get("name") or f"{'主' if aid == 'main' else '实验'}策略账户",
            "returnPct": acc.get("totalReturnPct"),
            "dailyReturnPct": acc.get("dailyReturnPct"),
            "maxDrawdownPct": acc.get("maxDrawdownPct"),
            "positionPct": acc.get("exposurePct"),
            "tradeCount": acc.get("tradeCount"),
            "winRatePct": acc.get("winRatePct"),
            "updatedAt": now,
        }

    # --- strategies ---
    strategies = {}
    default_names = {"main": "价值趋势混合策略", "lab": "板块轮动+动量突破策略"}
    default_descs = {
        "main": "高ROE/股息蓝筹底仓 + MA20/60趋势择时 + 商品价格维度（三维一体）+ 建仓节奏优化",
        "lab": "捕捉板块轮动和动量突破机会，增加板块确认和换手率过滤，增加高弹性板块分批止盈规则",
    }
    for account in ("main", "lab"):
        s = strategies_raw.get(account, {})
        v = s.get("version")
        if v and not str(v).startswith("v"):
            v = f"v{v}"
        strategies[account] = {
            "name": s.get("name") or default_names.get(account, ""),
            "version": v or "v1.x",
            "description": s.get("description") or default_descs.get(account, ""),
        }

    # --- performance (last 30 entries) ---
    performance = perf_raw[-30:] if perf_raw else []

    # --- workflows ---
    # Map: normalized input type → camelCase output key
    _wf_type_to_key = {
        "pre_market": "preMarket",
        "post_market": "postMarket",
        "weekly_review": "weeklyReview",
    }
    _wf_meta = {
        "preMarket": ("盘前分析", "交易日 08:10 BJT"),
        "postMarket": ("盘后复盘", "交易日 15:30 BJT"),
        "weeklyReview": ("周末复盘", "周六 08:00 BJT"),
    }
    workflows = {}
    for wf in workflows_raw:
        wtype = (wf.get("type") or "").replace("-", "_").lower()
        out_key = _wf_type_to_key.get(wtype)
        if not out_key:
            continue
        if out_key in workflows:
            # Keep latest only
            existing = workflows[out_key]
            new_created = wf.get("createdAt") or ""
            old_created = existing.get("_createdAt") or ""
            if new_created <= old_created:
                continue
        label, cadence = _wf_meta[out_key]
        created = wf.get("createdAt") or ""
        # Compute Beijing time (+8h) from UTC
        latest_run = created
        if created and created.endswith("+00:00"):
            try:
                from datetime import timedelta
                dt = datetime.fromisoformat(created)
                dt_bjt = dt + timedelta(hours=8)
                latest_run = dt_bjt.strftime("%Y-%m-%dT%H:%M:%S+08:00")
            except Exception:
                pass
        artifact = wf.get("filename")
        workflows[out_key] = {
            "label": label,
            "cadence": cadence,
            "latestRun": latest_run,
            "latestArtifact": artifact,
            "outputPolicy": "filename-only; workflow JSON contents are not published",
            "_createdAt": created,
        }
    # Remove internal key
    for v in workflows.values():
        v.pop("_createdAt", None)
    # Ensure all three keys exist
    for key, (label, cadence) in _wf_meta.items():
        if key not in workflows:
            workflows[key] = {
                "label": label,
                "cadence": cadence,
                "latestRun": None,
                "latestArtifact": None,
                "outputPolicy": "filename-only; workflow JSON contents are not published",
            }

    # --- agents (static list — matches site importer) ---
    AGENT_META = [
        ("coordinator", "Coordinator", "工作流编排、账户快照汇总、Agent 调度"),
        ("market_analyst", "Market Analyst", "市场环境、板块轮动和个股输入分析"),
        ("execution_planner", "Execution Planner", "交易候选、仓位节奏和执行计划"),
        ("risk_controller", "Risk Controller", "仓位上限、回撤阈值和风险闸门"),
        ("review_agent", "Review Agent", "盘后归因、错误模式和盲点识别"),
        ("strategy_maintainer", "Strategy Maintainer", "策略版本、规则变更和经验沉淀"),
        ("audit_layer", "Audit Layer", "策略变更提案审核、OOS 验证和 quorum 决策"),
    ]
    agents = [{"id": aid, "name": name, "role": role} for aid, name, role in AGENT_META]

    # --- charts (static paths — site importer copies PNGs separately) ---
    charts = {
        "cumulativeReturns": "/virtual-trader/cumulative_returns.png",
        "dailyPnl": "/virtual-trader/daily_pnl.png",
        "drawdown": "/virtual-trader/drawdown.png",
        "positionDistribution": "/virtual-trader/position_distribution.png",
    }

    # --- lessons (from strategy learned_lessons) ---
    lessons = []
    LESSON_TITLES = {
        "main": ["周期股不能只看量价", "现金拖累是系统问题"],
        "lab": ["高弹性资产要分批退出"],
    }
    for account, strat in strategies_raw.items():
        lesson_list = strat.get("learnedLessons") or strat.get("learned_lessons") or []
        titles = LESSON_TITLES.get(account, [])
        for i, lesson in enumerate(lesson_list[:3]):
            title = titles[i] if i < len(titles) else f"{account} 经验 #{i+1}"
            lessons.append({"title": title, "detail": lesson})

    return {
        "generatedAt": now,
        "source": {
            "system": "Hermes Virtual Trader",
            "mode": "public-aggregated-snapshot",
            "updatedAt": now,
        },
        "disclaimer": "Paper trading / demo data. Aggregated and anonymized for public portfolio display. Not investment advice.",
        "accounts": accounts,
        "strategies": strategies,
        "performance": performance,
        "workflows": workflows,
        "agents": agents,
        "auditLayer": _load_audit_layer_summary(),
        "charts": charts,
        "lessons": lessons,
    }


# ---------------------------------------------------------------------------
# Ledger gate
# ---------------------------------------------------------------------------

def _ledger_validation_status():
    """Run the strict ledger validator before publishing public snapshots."""
    try:
        scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))
        from validate_ledger_consistency import LedgerValidator

        results = LedgerValidator(root=str(VT_DIR), strict=True).validate()
        failures = [r for r in results if r.get("status") == "FAIL"]
        return {
            "passed": len(failures) == 0,
            "failures": len(failures),
            "resultCount": len(results),
        }
    except Exception:
        return {
            "passed": False,
            "failures": None,
            "error": "ledger validation unavailable",
        }


# ---------------------------------------------------------------------------
# Write public snapshot
# ---------------------------------------------------------------------------

EXPORT_DIR = VT_DIR / "public-export"
SNAPSHOT_FILE = "public-snapshot.json"
MANIFEST_FILE = "manifest.json"
SCHEMA_VERSION = "1.0.0"


def write_public_snapshot(dry_run=False):
    """Write a site-importer-compatible public snapshot.

    Args:
        dry_run: If True, only return preview without writing files.

    Returns:
        {
            ok: bool,
            dryRun: bool,
            written: bool,
            outputPath: str or None,
            manifestPath: str or None,
            scan: { passed, findingCount, ... },
            snapshotSummary: { accounts, strategies, ... }
        }
    """
    # Step 1: Build snapshot
    snapshot = _build_site_compatible_snapshot()

    # Step 2: Scan snapshot for sensitive fields
    findings = scan_sensitive_fields(snapshot)
    passed = all(f["severity"] != "high" for f in findings)
    ledger = _ledger_validation_status()

    result = {
        "ok": passed and ledger.get("passed", False),
        "dryRun": dry_run,
        "written": False,
        "outputPath": None,
        "manifestPath": None,
        "gate": {
            "ledger": ledger,
        },
        "scan": {
            "passed": passed,
            "findingCount": len(findings),
            "highSeverityCount": sum(1 for f in findings if f["severity"] == "high"),
            "findings": findings,
        },
        "snapshotSummary": {
            "accounts": list(snapshot.get("accounts", {}).keys()),
            "strategies": list(snapshot.get("strategies", {}).keys()),
            "performanceRows": len(snapshot.get("performance", [])),
            "agents": len(snapshot.get("agents", [])),
            "workflows": list(snapshot.get("workflows", {}).keys()),
            "lessons": len(snapshot.get("lessons", [])),
        },
    }

    # Step 3: Block if scan failed
    if not passed:
        result["ok"] = False
        return result

    if not ledger.get("passed", False):
        result["ok"] = False
        return result

    # Step 4: Dry run — don't write
    if dry_run:
        return result

    # Step 5: Write files
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_path = EXPORT_DIR / SNAPSHOT_FILE
    manifest_path = EXPORT_DIR / MANIFEST_FILE

    snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2))

    manifest = {
        "generatedAt": snapshot["generatedAt"],
        "snapshotPath": SNAPSHOT_FILE,
        "schemaVersion": SCHEMA_VERSION,
        "source": "console-export-adapter",
        "ledgerValidation": ledger,
        "scanPassed": passed,
        "findingCount": len(findings),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))

    # Step 6: Post-write verification — re-read and re-scan
    try:
        written_data = json.loads(snapshot_path.read_text())
        post_findings = scan_sensitive_fields(written_data)
        post_passed = all(f["severity"] != "high" for f in post_findings)
    except Exception as e:
        # Read-back failed — clean up
        _cleanup_export_files(snapshot_path, manifest_path)
        result["ok"] = False
        result["scan"]["postWriteError"] = str(e)
        return result

    if not post_passed:
        # Post-write scan failed — clean up
        _cleanup_export_files(snapshot_path, manifest_path)
        result["ok"] = False
        result["scan"]["passed"] = False
        result["scan"]["postWriteFindings"] = post_findings
        result["scan"]["postWriteError"] = "Post-write scan detected sensitive fields — files deleted"
        return result

    result["written"] = True
    result["outputPath"] = str(snapshot_path)
    result["manifestPath"] = str(manifest_path)
    return result


def _cleanup_export_files(snapshot_path, manifest_path):
    """Remove export files if post-write verification fails."""
    for p in (snapshot_path, manifest_path):
        try:
            if p.exists():
                p.unlink()
        except Exception:
            pass
