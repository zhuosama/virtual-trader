#!/usr/bin/env python3
"""Tests for H35 shadow account executor.

Uses the existing CN_PIT_FileSource test data to verify:
1. Config validation (mode=paper, auto_live_orders=false required)
2. Dry-run mode produces no files
3. Stop condition detection (max drawdown, consecutive losers, turnover)
4. Full integration run with --dry-run
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# Add project to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(PROJECT_ROOT / "backtest" / "experiments"))
sys.path.insert(0, str(SCRIPTS_DIR))

# Import the module under test
# We can't import it directly because fundamental_backtest depends on yfinance
# So we test via subprocess with --dry-run

H35_SCRIPT = SCRIPTS_DIR / "h35_shadow_account_executor.py"
CONFIG_PATH = PROJECT_ROOT / "value_account" / "h34_shadow_account_config.json"
DEFAULT_PRICES = PROJECT_ROOT / "data" / "cn_pit" / "prices_h30_candidate.csv"
DEFAULT_UNIVERSE = PROJECT_ROOT / "data" / "cn_pit" / "universe_h30_candidate.jsonl"
DEFAULT_SNAPSHOTS = PROJECT_ROOT / "data" / "cn_pit" / "universe_snapshots_h30_candidate.jsonl"


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def temp_config():
    """A valid paper-mode config in a temp dir."""
    with open(CONFIG_PATH) as f:
        cfg = json.load(f)
    return cfg


@pytest.fixture
def temp_config_path(temp_config):
    """Write temp_config to a temp file and return the path."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(temp_config, f)
        tmp = f.name
    yield Path(tmp)
    if os.path.exists(tmp):
        os.unlink(tmp)


@pytest.fixture
def valid_script_args():
    """Default args for running the H35 script."""
    return [
        sys.executable, str(H35_SCRIPT),
        "--start", "2025-01-01",
        "--end", "2025-03-01",
        "--dry-run",
    ]


# ── Helper ────────────────────────────────────────────────────────────

def run_script(args: list) -> subprocess.CompletedProcess:
    """Run the H35 script as a subprocess and return the result."""
    import subprocess
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT / "backtest" / "experiments")
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
        env=env,
        timeout=120,
    )
    return result


# ── Tests ─────────────────────────────────────────────────────────────

class TestConfigValidation:
    """Test that config mode and auto_live_orders are validated."""

    def test_paper_mode_ok(self):
        """Config with mode=paper, auto_live_orders=false should pass."""
        import subprocess
        result = run_script([
            sys.executable, str(H35_SCRIPT),
            "--start", "2025-01-01",
            "--end", "2025-03-01",
            "--dry-run",
        ])
        # Should run, though may have blockers from data quality
        assert result.returncode in (0, 1), (
            f"Script failed with returncode {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_rejects_non_paper(self):
        """Script should bail immediately if mode is not paper."""
        import subprocess, shutil
        with tempfile.TemporaryDirectory() as td:
            cfg = json.loads(CONFIG_PATH.read_text())
            cfg["account"]["mode"] = "live"
            bad_cfg = Path(td) / "bad_config.json"
            bad_cfg.write_text(json.dumps(cfg))
            orig_config = CONFIG_PATH.read_bytes()
            try:
                # Temporarily replace the config
                shutil.copy(str(CONFIG_PATH), str(CONFIG_PATH) + ".bak")
                bad_cfg.write_text(json.dumps(cfg))
                # Actually, we need to point to a different config...
                # The script reads CONFIG_PATH which is fixed, so we mock it
                pass
            finally:
                pass
        # Test via mock approach: the script will refuse non-paper config
        # We test the validate_config function directly by importing
        sys.path.insert(0, str(SCRIPTS_DIR))
        try:
            from h35_shadow_account_executor import validate_config
        except ImportError:
            pytest.skip("Could not import module for unit test")
        with pytest.raises(RuntimeError, match="mode must be 'paper'"):
            validate_config({"account": {"mode": "live"}})

    def test_rejects_auto_live_orders(self):
        """Script should bail if auto_live_orders is true."""
        sys.path.insert(0, str(SCRIPTS_DIR))
        try:
            from h35_shadow_account_executor import validate_config
        except ImportError:
            pytest.skip("Could not import module for unit test")
        with pytest.raises(RuntimeError, match="auto_live_orders must be false"):
            validate_config({"account": {"mode": "paper", "auto_live_orders": True}})


class TestDryRun:
    """Dry-run should produce no output files."""

    def test_dry_run_creates_no_files(self):
        """With --dry-run, none of the output files should be created."""
        import subprocess
        output_dir = tempfile.mkdtemp()
        trade_log = Path(output_dir) / "trades.jsonl"
        state_file = Path(output_dir) / "state.json"
        report_file = Path(output_dir) / "report.md"
        run_file = Path(output_dir) / "run.json"

        result = run_script([
            sys.executable, str(H35_SCRIPT),
            "--start", "2025-01-01",
            "--end", "2025-03-01",
            "--dry-run",
            "--trade-log", str(trade_log),
            "--state-file", str(state_file),
            "--report-file", str(report_file),
            "--run-file", str(run_file),
        ])
        assert not trade_log.exists(), f"Dry-run should not create {trade_log}"
        assert not state_file.exists(), f"Dry-run should not create {state_file}"
        assert not report_file.exists(), f"Dry-run should not create {report_file}"
        assert not run_file.exists(), f"Dry-run should not create {run_file}"
        assert "DRY-RUN" in result.stdout, "Output should mention DRY-RUN"


class TestGateFunctions:
    """Unit tests for gate/stop condition logic."""

    def test_max_drawdown_blocker(self):
        """When max_drawdown <= threshold, a blocker is emitted."""
        sys.path.insert(0, str(SCRIPTS_DIR))
        try:
            from h35_shadow_account_executor import (
                compute_monthly_one_way_turnover,
                compute_annualized_turnover,
                compute_consecutive_losing_sells,
                check_stop_conditions,
            )
        except ImportError:
            pytest.skip("Could not import module for unit test")

        # Trades that produce >12% drawdown (sell at massive loss)
        trades = [
            {"date": "2025-01-01", "action": "buy", "ticker": "000001.SZ",
             "price": 100, "shares": 1000, "amount": 100000, "total_cost": 100050},
            {"date": "2025-01-10", "action": "sell", "ticker": "000001.SZ",
             "price": 50, "shares": 1000, "amount": 50000,
             "pnl": -50000, "commission": 5, "stamp_tax": 50},
        ]
        # Provide equity_curve that shows a drawdown below -12%
        import pandas as pd
        eq = pd.Series([100000, 100000, 100000, 50000, 50000],
                       index=pd.date_range("2025-01-01", periods=5, freq="3D"))
        cfg_dd = {
            "stop_conditions": [{"id": "max_drawdown", "threshold": -0.12}],
            "caps": {"turnover": {"monthly_one_way_max_pct": 0.75, "monthly_one_way_warn_pct": 0.40,
                                  "annualized_max_x": 3.0, "annualized_warn_x": 1.5}},
        }
        blockers, warnings = check_stop_conditions(trades, 100000, cfg_dd, {"total_return": -0.5},
                                                    equity_curve=eq)
        dd_blockers = [b for b in blockers if b.startswith("max_drawdown")]
        assert len(dd_blockers) > 0, f"Expected drawdown blocker, got {blockers}"

    def test_consecutive_losing_sells(self):
        """Detect 5+ consecutive losing sells at end of trade list."""
        sys.path.insert(0, str(SCRIPTS_DIR))
        try:
            from h35_shadow_account_executor import compute_consecutive_losing_sells
        except ImportError:
            pytest.skip("Could not import module for unit test")

        trades = [
            {"action": "sell", "pnl": 100},   # win
            {"action": "sell", "pnl": -50},   # loss
            {"action": "sell", "pnl": -30},   # loss
            {"action": "sell", "pnl": -10},   # loss
        ]
        assert compute_consecutive_losing_sells(trades) == 3

        # Only contiguous streak at end
        trades = [
            {"action": "sell", "pnl": -50},
            {"action": "sell", "pnl": 100},   # breaks streak
            {"action": "sell", "pnl": -20},
            {"action": "buy", "pnl": 0},
        ]
        assert compute_consecutive_losing_sells(trades) == 1

    def test_monthly_turnover_block(self):
        """>75% monthly turnover triggers blocker."""
        sys.path.insert(0, str(SCRIPTS_DIR))
        try:
            from h35_shadow_account_executor import compute_monthly_one_way_turnover, check_stop_conditions
        except ImportError:
            pytest.skip("Could not import module for unit test")

        # With the /2 convention: a buy of 80% capital on its own
        # gives monthly turnover = (800000) / 2 / 1000000 = 0.40 (not > 0.75)
        # Buy + sell together gives the full effect.
        today = __import__("datetime").date.today().strftime("%Y-%m-%d")
        trades = [
            {"date": today, "action": "buy", "ticker": "000001.SZ",
             "price": 100, "shares": 8000, "amount": 800000, "total_cost": 800400},
            {"date": today, "action": "sell", "ticker": "000001.SZ",
             "price": 100, "shares": 8000, "amount": 800000},
        ]
        cfg_high = {
            "stop_conditions": [],
            "caps": {"turnover": {"monthly_one_way_max_pct": 0.75, "monthly_one_way_warn_pct": 0.40,
                                  "annualized_max_x": 3.0, "annualized_warn_x": 1.5}},
        }
        # total amount = 1,600,000 / 2 / 1,000,000 = 0.80 > 0.75
        blockers, warnings = check_stop_conditions(trades, 1000000, cfg_high, {})
        turn_blockers = [b for b in blockers if b.startswith("monthly_turnover")]
        assert len(turn_blockers) > 0, f"Expected turnover blocker, got {blockers}"


class TestIntegration:
    """Integration tests — runs the full backtest via --dry-run."""

    def test_full_integration_dry_run(self):
        """Run the full script with --dry-run and verify output contains metrics."""
        import subprocess
        result = run_script([
            sys.executable, str(H35_SCRIPT),
            "--start", "2025-01-01",
            "--end", "2025-06-01",
            "--dry-run",
        ])
        stdout = result.stdout
        assert "H35 Shadow Account" in stdout, f"Missing header. stdout:\n{stdout}\nstderr:\n{result.stderr}"
        assert "Total return" in stdout, f"Missing metrics. stdout:\n{stdout}"
        assert result.returncode in (0, 1), (
            f"Unexpected exit code {result.returncode}. stdout:\n{stdout}\nstderr:\n{result.stderr}"
        )

    def test_full_integration_non_dry_run(self):
        """Run the script without --dry-run and verify output files exist."""
        import subprocess, shutil
        output_dir = Path(tempfile.mkdtemp())
        trade_log = output_dir / "trades.jsonl"
        state_file = output_dir / "state.json"
        report_file = output_dir / "report.md"
        run_file = output_dir / "run.json"

        result = run_script([
            sys.executable, str(H35_SCRIPT),
            "--start", "2025-01-01",
            "--end", "2025-06-01",
            "--trade-log", str(trade_log),
            "--state-file", str(state_file),
            "--report-file", str(report_file),
            "--run-file", str(run_file),
        ])
        # Files should be created even if blockers exist
        assert trade_log.exists(), f"Missing {trade_log}"
        assert state_file.exists(), f"Missing {state_file}"
        assert report_file.exists(), f"Missing {report_file}"
        assert run_file.exists(), f"Missing {run_file}"

        # Validate content
        trades = [json.loads(line) for line in trade_log.read_text().strip().split("\n") if line.strip()]
        assert len(trades) > 0, "Trade log should contain trades"

        state = json.loads(state_file.read_text())
        assert "performance" in state
        assert "gates" in state

        report = report_file.read_text()
        assert "H35" in report
        assert "Shadow Account" in report
