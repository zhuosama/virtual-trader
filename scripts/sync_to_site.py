#!/usr/bin/env python3
"""站点自动同步脚本

被 coordinator.py 在盘后/周末工作流完成后调用。
负责：导入虚拟盘快照 → 构建站点 → 提交推送。
所有错误被隔离，不影响调用方。
"""

import json, os, subprocess, sys, traceback
from datetime import datetime


SITE_DIR = os.path.expanduser("~/obsidian-wiki/site")
REPO_ROOT = os.path.expanduser("~/obsidian-wiki")
VTRADER_HOME = os.path.expanduser("~/.hermes/virtual-trader")
LOG_PATH = os.path.expanduser("~/.hermes/virtual-trader/logs/site_sync.log")
HEALTH_PATH = os.path.expanduser("~/.hermes/virtual-trader/logs/site_sync_health.json")
MAX_LOG_LINES = 500
HERMES_BIN = os.path.expanduser("~/.local/bin/hermes")

os.environ["PATH"] = (
    "/Users/zhuosama/.nvm/versions/node/v22.22.2/bin:"
    "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:"
    + os.environ.get("PATH", "")
)
os.environ.setdefault("VTRADER_HOME", VTRADER_HOME)


def log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, file=sys.stderr)
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def trim_log() -> None:
    try:
        if not os.path.exists(LOG_PATH):
            return
        lines = open(LOG_PATH).readlines()
        if len(lines) > MAX_LOG_LINES:
            with open(LOG_PATH, "w") as f:
                f.writelines(lines[-MAX_LOG_LINES:])
    except Exception:
        pass


def _last_error(n_lines: int = 3) -> str:
    """Return last N lines of the most recent log entry (the failed step)."""
    try:
        if not os.path.exists(LOG_PATH):
            return "(no log)"
        lines = open(LOG_PATH).read().strip().splitlines()
        tail = [l for l in lines if l.startswith("  FAIL") or l.startswith("  WARN") or l.startswith("  SKIP")]
        return "\n".join(tail[-n_lines:]) if tail else lines[-1] if lines else "(no log)"
    except Exception:
        return "(error reading log)"


def run(cmd: list, cwd: str = None, timeout: int = 120) -> tuple:
    """Run a command. Returns (ok, output)."""
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
        )
        ok = proc.returncode == 0
        out = (proc.stdout + proc.stderr).strip()
        return ok, out
    except subprocess.TimeoutExpired:
        return False, f"timeout after {timeout}s"
    except FileNotFoundError as e:
        return False, f"command not found: {e}"
    except Exception as e:
        return False, str(e)


def import_snapshot() -> bool:
    """Step 1: Import virtual trader data into site snapshot."""
    log("Step 1: import-virtual-trader-public")
    if not os.path.isdir(SITE_DIR):
        log(f"  SKIP: site dir not found: {SITE_DIR}")
        return False

    node_script = os.path.join(SITE_DIR, "scripts", "import-virtual-trader-public.mjs")
    if not os.path.isfile(node_script):
        log(f"  SKIP: import script not found: {node_script}")
        return False

    ok, out = run(["node", node_script], cwd=SITE_DIR, timeout=30)
    if ok:
        log(f"  OK: {out}")
        return True
    else:
        log(f"  FAIL: {out}")
        return False


def generate_charts() -> bool:
    """Step 0: Refresh public chart PNGs from current performance/account data."""
    log("Step 0: generate virtual-trader charts")
    script = os.path.join(VTRADER_HOME, "scripts", "generate_charts.py")
    if not os.path.isfile(script):
        log(f"  SKIP: chart script not found: {script}")
        return False

    output_dir = os.path.join(VTRADER_HOME, "reports", "charts")
    ok, out = run(["/usr/bin/python3", script, output_dir], cwd=VTRADER_HOME, timeout=60)
    if ok:
        log(f"  OK: charts refreshed")
        return True

    short = "\n".join(out.splitlines()[-3:]) if out else "(no output)"
    log(f"  FAIL: {short}")
    return False


def build_site() -> bool:
    """Step 2: Build the Astro site."""
    log("Step 2: npm run build")
    if not os.path.isdir(os.path.join(SITE_DIR, "node_modules")):
        log("  SKIP: node_modules not found (run npm install first)")
        return False

    ok, out = run(["npm", "run", "build"], cwd=SITE_DIR, timeout=120)
    if ok:
        log(f"  OK: build succeeded")
        return True
    else:
        # Only log last 3 lines of output
        short = "\n".join(out.splitlines()[-3:]) if out else "(no output)"
        log(f"  FAIL: {short}")
        return False


def git_sync() -> bool:
    """Step 3: Commit and push changes from repo root."""
    log("Step 3: git commit + push")
    if not os.path.isdir(os.path.join(REPO_ROOT, ".git")):
        log("  SKIP: not a git repo")
        return False

    # Check if there are changes (from repo root, only site/ paths)
    ok, out = run(["git", "status", "--porcelain", "--", "site/"], cwd=REPO_ROOT, timeout=10)
    if not ok:
        log(f"  FAIL: git status error: {out}")
        return False

    relevant = [
        line for line in out.splitlines()
        if any(p in line for p in [
            "site/src/data/virtual-trader/",
            "site/public/virtual-trader/",
        ])
    ]
    if not relevant:
        log("  OK: no changes to commit")
        return True

    date_str = datetime.now().strftime("%Y-%m-%d")
    ok1, out1 = run([
        "git", "add", "-f",
        "site/src/data/virtual-trader/public-snapshot.json",
        "site/public/virtual-trader/",
    ], cwd=REPO_ROOT, timeout=10)
    if not ok1:
        log(f"  FAIL: git add error: {out1}")
        return False

    ok2, out2 = run([
        "git", "commit", "-m",
        f"[sync] auto-sync virtual trader data {date_str}",
    ], cwd=REPO_ROOT, timeout=10)

    if not ok2 and "nothing to commit" in (out2 or ""):
        log("  OK: nothing to commit")
        return True

    if not ok2:
        log(f"  WARN: commit had issues: {out2[-200:]}")
        # Don't block on commit warnings — push anyway

    ok3, out3 = run(["git", "push"], cwd=REPO_ROOT, timeout=60)
    if ok3:
        log(f"  OK: pushed changes ({len(relevant)} file(s))")
        return True
    else:
        log(f"  FAIL: push failed: {out3[-200:]}")
        return False


def sync_all() -> dict:
    """Run full sync pipeline. Returns status dict."""
    log("=" * 40)
    log("SITE SYNC START")
    results = {
        "timestamp": datetime.now().isoformat(),
        "steps": {},
        "errors": {},
        "success": False,
    }

    charts = generate_charts()
    results["steps"]["charts"] = charts
    if not charts:
        results["errors"]["charts"] = _last_error()

    # Step 1: Import
    imported = import_snapshot()
    results["steps"]["import"] = imported
    if not imported:
        results["errors"]["import"] = _last_error()

    # Step 2: Build (only if import succeeded or had new data)
    if imported:
        built = build_site()
        results["steps"]["build"] = built
        if not built:
            results["errors"]["build"] = _last_error()
    else:
        log("Step 2: SKIP (import failed or no changes)")
        results["steps"]["build"] = False
        results["errors"]["build"] = "skipped (import failed)"

    # Step 3: Git push (only if build succeeded)
    if results["steps"].get("build", False):
        pushed = git_sync()
        results["steps"]["push"] = pushed
        if not pushed:
            results["errors"]["push"] = _last_error()
    else:
        log("Step 3: SKIP (build failed)")
        results["steps"]["push"] = False
        results["errors"]["push"] = "skipped (build failed)"

    results["success"] = all(results["steps"].values())
    status = "OK" if results["success"] else "DEGRADED"
    log(f"SITE SYNC END: {status}")
    log("=" * 40)
    trim_log()
    return results


def _write_health(result: dict) -> None:
    """Write machine-readable health file for cron/last_status tracking."""
    try:
        os.makedirs(os.path.dirname(HEALTH_PATH), exist_ok=True)
        health = {
            "ts": result["timestamp"],
            "success": result["success"],
        }
        if not result["success"]:
            steps = result.get("steps", {})
            errors = result.get("errors", {})
            failed_step = "unknown"
            for step_name in ["charts", "import", "build", "push"]:
                if not steps.get(step_name, True):
                    failed_step = step_name
                    break
            health["failed_step"] = failed_step
            health["error"] = errors.get(failed_step, "(no error captured)")
        with open(HEALTH_PATH, "w") as f:
            json.dump(health, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log(f"  WARN: failed to write health file: {e}")


def _notify_wecom(result: dict) -> None:
    """Send WeCom alert on failure. Subprocess-based; never raises."""
    if result.get("success", False):
        return
    steps = result.get("steps", {})
    errors = result.get("errors", {})
    failed_step = "unknown"
    for step_name in ["charts", "import", "build", "push"]:
        if not steps.get(step_name, True):
            failed_step = step_name
            break
    error_text = (errors.get(failed_step, "") or "")[:500]
    ts = result.get("timestamp", datetime.now().isoformat())
    body = f"[{ts}] step={failed_step} failed\n---stderr tail---\n{error_text}"
    try:
        subprocess.run(
            [HERMES_BIN, "send", "--to", "wecom",
             "--subject", f"[vtrader-sync] ❌ {failed_step}"],
            input=body, capture_output=True, text=True, timeout=15,
        )
    except Exception as e:
        log(f"  WARN: failed to send WeCom alert: {e}")


if __name__ == "__main__":
    result = sync_all()
    _write_health(result)
    _notify_wecom(result)
    # Output JSON for coordinator to parse
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0)  # Never fail the caller
