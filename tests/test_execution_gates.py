#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 2: ExecutionModel gate chain G0–G6 (pure, individually tested).

Each candidate order yields a decision {order, verdict, gate, reason} where
verdict in {pass, clamp, reject, halt}. Gates are pure functions injected with
data (no real I/O beyond reading the kill-switch file in data_dir). Tests cover
the pass case, each veto/clamp case, and each graceful-degradation (missing
data) case. Shadow stays shadow: even with all gates passing, accounts/*.json
and trades/ are untouched and executed == 0.

h35 threshold sources (scripts/h35_shadow_account_executor.py:82-241):
  drawdown_stop -0.12, monthly_loss_stop -0.08, consec_losing_sells 5,
  turnover_warn 0.40, turnover_block 0.75 — reused as G4 thresholds.
"""

import json
import os
import sys
import tempfile
import unittest

VTRADER_ROOT = os.path.expanduser("~/.hermes/virtual-trader")
sys.path.insert(0, os.path.join(VTRADER_ROOT, "agents"))

from execution_model import ExecutionModel  # noqa: E402


DEFAULT_GATES = {
    "max_orders": 5,
    "min_order_amount": 1000,
    "price_deviation": 0.1,
    "whitelist": None,
    "drawdown_stop": -0.12,
    "monthly_loss_stop": -0.08,
    "consec_losing_sells": 5,
    "turnover_warn": 0.40,
    "turnover_block": 0.75,
    "soft_brake_net_sell": 0.15,
}


def _model(data_dir=None, eps=0.005, mode="shadow", gates=None):
    if data_dir is None:
        data_dir = tempfile.mkdtemp()
    cfg = {"mode": mode, "eps": eps, "gates": dict(gates or DEFAULT_GATES),
           "kill_switch_path": "EXECUTION_HALT"}
    return ExecutionModel(data_dir=data_dir, exec_config=cfg, mode=mode)


def _order(code, side, est_amount, target_weight=0.1, delta_weight=0.1, account="main", price=10.0):
    return {
        "account": account,
        "code": code,
        "side": side,
        "delta_weight": delta_weight if side == "buy" else -abs(delta_weight),
        "target_weight": target_weight,
        "est_amount": est_amount,
        "price": price,
    }


# ───────────────────────────── G0 plan contract ─────────────────────────────
class TestG0PlanContract(unittest.TestCase):

    def test_approved_dict_for_today_passes(self):
        m = _model()
        plan = {"decision": "APPROVED", "target_weights": {"600900": 0.1}}
        res = m.check_plan_contract(plan, "2026-06-05", plan_date="2026-06-05")
        self.assertTrue(res["ok"])
        self.assertEqual(res["gate"], "G0")

    def test_non_dict_plan_rejected(self):
        m = _model()
        res = m.check_plan_contract(None, "2026-06-05", plan_date="2026-06-05")
        self.assertFalse(res["ok"])
        self.assertEqual(res["gate"], "G0")

    def test_not_approved_rejected(self):
        m = _model()
        plan = {"decision": "REJECTED", "target_weights": {"600900": 0.1}}
        res = m.check_plan_contract(plan, "2026-06-05", plan_date="2026-06-05")
        self.assertFalse(res["ok"])

    def test_stale_plan_date_rejected(self):
        m = _model()
        plan = {"decision": "APPROVED", "target_weights": {"600900": 0.1}}
        res = m.check_plan_contract(plan, "2026-06-05", plan_date="2026-06-04")
        self.assertFalse(res["ok"])
        self.assertIn("freshness", res["reason"].lower() + res["gate"])

    def test_target_weights_not_dict_rejected(self):
        m = _model()
        plan = {"decision": "APPROVED", "target_weights": [1, 2, 3]}
        res = m.check_plan_contract(plan, "2026-06-05", plan_date="2026-06-05")
        self.assertFalse(res["ok"])

    def test_missing_plan_date_skips_freshness_but_checks_structure(self):
        # plan_date None -> freshness not enforceable; structural checks still apply.
        m = _model()
        plan = {"decision": "APPROVED", "target_weights": {"600900": 0.1}}
        res = m.check_plan_contract(plan, "2026-06-05", plan_date=None)
        self.assertTrue(res["ok"])


# ───────────────────────────── G2 risk limits ─────────────────────────────
class TestG2RiskLimits(unittest.TestCase):

    def _rules(self, **over):
        base = {"max_single_position": 0.08, "max_sector_exposure": 0.3,
                "total_position_limit": 0.8}
        base.update(over)
        return base

    def _state(self, total_value=1000.0, positions=None):
        return {"total_value": total_value, "positions": positions or []}

    def test_within_limits_passes(self):
        m = _model()
        state = self._state(positions=[{"code": "600900", "market_value": 50.0}])
        orders = [_order("600900", "buy", 20.0, target_weight=0.07,
                         delta_weight=0.02)]
        decs = m.apply_risk_limits(orders, state, self._rules())
        self.assertEqual(decs[0]["verdict"], "pass")
        self.assertEqual(decs[0]["gate"], "G2")

    def test_single_name_over_limit_clamps_buy(self):
        m = _model()
        # holding 0.05, buying to target 0.20 but cap 0.08 -> clamp to 0.08
        state = self._state(positions=[{"code": "600900", "market_value": 50.0}])
        orders = [_order("600900", "buy", 150.0, target_weight=0.20,
                         delta_weight=0.15)]
        decs = m.apply_risk_limits(orders, state, self._rules(max_single_position=0.08))
        self.assertEqual(decs[0]["verdict"], "clamp")
        # clamped est_amount should be (0.08-0.05)*1000 = 30
        self.assertAlmostEqual(decs[0]["order"]["est_amount"], 30.0, places=4)

    def test_clamp_below_eps_rejects(self):
        m = _model(eps=0.005)
        # already at 0.079, cap 0.08 -> room 0.001 < eps -> reject
        state = self._state(positions=[{"code": "600900", "market_value": 79.0}])
        orders = [_order("600900", "buy", 100.0, target_weight=0.18,
                         delta_weight=0.10)]
        decs = m.apply_risk_limits(orders, state, self._rules(max_single_position=0.08))
        self.assertEqual(decs[0]["verdict"], "reject")

    def test_sell_never_blocked_by_upper_limit(self):
        m = _model()
        state = self._state(positions=[{"code": "600900", "market_value": 300.0}])
        orders = [_order("600900", "sell", 200.0, target_weight=0.10,
                         delta_weight=-0.20)]
        decs = m.apply_risk_limits(orders, state, self._rules(max_single_position=0.08))
        self.assertEqual(decs[0]["verdict"], "pass")

    def test_total_book_over_limit_clamps_buy(self):
        m = _model()
        # current book 0.78, total cap 0.80; buy of 0.10 would push to 0.88 -> clamp to +0.02
        state = self._state(positions=[
            {"code": "600900", "market_value": 400.0},
            {"code": "000333", "market_value": 380.0},
        ])
        orders = [_order("000001", "buy", 100.0, target_weight=0.10,
                         delta_weight=0.10)]
        decs = m.apply_risk_limits(orders, state, self._rules(total_position_limit=0.80,
                                                              max_single_position=0.5))
        self.assertEqual(decs[0]["verdict"], "clamp")
        self.assertAlmostEqual(decs[0]["order"]["est_amount"], 20.0, places=4)

    def test_sector_clamp_when_map_available(self):
        # sector exposure cap enforced when a sector map is injected.
        data_dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(data_dir, "market-data"))
        with open(os.path.join(data_dir, "market-data", "watchlist.json"), "w") as f:
            json.dump({"stocks": [
                {"code": "600900", "sector": "电力"},
                {"code": "000001", "sector": "电力"},
            ]}, f)
        m = _model(data_dir=data_dir)
        # 600900 already 0.25 in 电力; buying 000001 (电力) 0.10 -> 0.35 > cap 0.30 -> clamp to +0.05
        state = self._state(positions=[{"code": "600900", "market_value": 250.0}])
        orders = [_order("000001", "buy", 100.0, target_weight=0.10, delta_weight=0.10)]
        decs = m.apply_risk_limits(orders, state, self._rules(max_sector_exposure=0.30,
                                                              max_single_position=0.5,
                                                              total_position_limit=0.99))
        self.assertEqual(decs[0]["verdict"], "clamp")
        self.assertAlmostEqual(decs[0]["order"]["est_amount"], 50.0, places=4)

    def test_sector_unavailable_is_noop_not_block(self):
        # No watchlist / sector map -> sector sub-check skipped, order still passes other limits.
        m = _model()  # fresh tmp dir, no market-data
        state = self._state(positions=[{"code": "600900", "market_value": 50.0}])
        orders = [_order("000001", "buy", 20.0, target_weight=0.07, delta_weight=0.02)]
        decs = m.apply_risk_limits(orders, state, self._rules())
        self.assertEqual(decs[0]["verdict"], "pass")
        self.assertIn("sector data unavailable", decs[0]["reason"])


# ───────────────────────────── G3 pre-trade sanity ─────────────────────────
class TestG3PreTrade(unittest.TestCase):

    def _cfg(self, **over):
        c = dict(DEFAULT_GATES)
        c.update(over)
        return c

    def test_normal_buy_passes(self):
        m = _model()
        order = _order("600900", "buy", 5000.0, price=27.72)
        day_state = {"order_count": 1, "holdings": {"600900"}, "prev_close": 27.50,
                     "whitelist": {"600900"}}
        res = m.pretrade_check(order, day_state, self._cfg())
        self.assertTrue(res["ok"])
        self.assertEqual(res["gate"], "G3")

    def test_too_many_orders_rejects(self):
        m = _model()
        order = _order("600900", "buy", 5000.0, price=27.72)
        day_state = {"order_count": 6, "holdings": {"600900"}, "prev_close": 27.72,
                     "whitelist": {"600900"}}
        res = m.pretrade_check(order, day_state, self._cfg(max_orders=5))
        self.assertFalse(res["ok"])
        self.assertIn("max_orders", res["reason"])

    def test_buy_below_min_amount_rejects(self):
        m = _model()
        order = _order("600900", "buy", 500.0, price=27.72)
        day_state = {"order_count": 1, "holdings": {"600900"}, "prev_close": 27.72,
                     "whitelist": {"600900"}}
        res = m.pretrade_check(order, day_state, self._cfg(min_order_amount=1000))
        self.assertFalse(res["ok"])
        self.assertIn("min_order_amount", res["reason"])

    def test_sell_below_min_amount_allowed(self):
        m = _model()
        order = _order("600900", "sell", 500.0, price=27.72)
        day_state = {"order_count": 1, "holdings": {"600900"}, "prev_close": 27.72,
                     "whitelist": {"600900"}}
        res = m.pretrade_check(order, day_state, self._cfg(min_order_amount=1000))
        self.assertTrue(res["ok"])

    def test_price_deviation_rejects(self):
        m = _model()
        order = _order("600900", "buy", 5000.0, price=33.0)  # +20% vs 27.5
        day_state = {"order_count": 1, "holdings": {"600900"}, "prev_close": 27.5,
                     "whitelist": {"600900"}}
        res = m.pretrade_check(order, day_state, self._cfg(price_deviation=0.1))
        self.assertFalse(res["ok"])
        self.assertIn("price_deviation", res["reason"])

    def test_price_ref_unavailable_skips_deviation(self):
        m = _model()
        order = _order("600900", "buy", 5000.0, price=33.0)
        day_state = {"order_count": 1, "holdings": {"600900"}, "prev_close": None,
                     "whitelist": {"600900"}}
        res = m.pretrade_check(order, day_state, self._cfg(price_deviation=0.1))
        self.assertTrue(res["ok"])
        self.assertIn("price ref unavailable", res["reason"])

    def test_non_whitelisted_buy_rejects(self):
        m = _model()
        order = _order("999999", "buy", 5000.0, price=10.0)
        day_state = {"order_count": 1, "holdings": {"600900"}, "prev_close": 10.0,
                     "whitelist": {"600900"}}
        res = m.pretrade_check(order, day_state, self._cfg())
        self.assertFalse(res["ok"])
        self.assertIn("whitelist", res["reason"])

    def test_non_whitelisted_sell_allowed(self):
        m = _model()
        order = _order("999999", "sell", 5000.0, price=10.0)
        day_state = {"order_count": 1, "holdings": {"600900"}, "prev_close": 10.0,
                     "whitelist": {"600900"}}
        res = m.pretrade_check(order, day_state, self._cfg())
        self.assertTrue(res["ok"])


# ───────────────────────────── G4 protections ─────────────────────────────
class TestG4Protections(unittest.TestCase):

    def _cfg(self, **over):
        c = dict(DEFAULT_GATES)
        c.update(over)
        return c

    def test_healthy_account_passes(self):
        m = _model()
        equity = [1.0, 1.02, 1.01, 1.03]
        res = m.protections("main", equity, realized_sells=[], cfg=self._cfg())
        self.assertTrue(res["allow_buys"])

    def test_drawdown_halts_new_buys(self):
        m = _model()
        # peak 1.10, trough 0.95 -> dd ~ -0.136 < -0.12 -> halt buys
        equity = [1.0, 1.10, 1.05, 0.95]
        res = m.protections("main", equity, realized_sells=[], cfg=self._cfg(drawdown_stop=-0.12))
        self.assertFalse(res["allow_buys"])
        self.assertIn("drawdown", res["reason"])

    def test_monthly_loss_halts_new_buys(self):
        m = _model()
        # worst monthly return < -0.08; provide month-tagged equity
        equity_series = {
            "2026-04": [1.0, 0.90],   # -10% month
            "2026-05": [0.90, 0.92],
        }
        res = m.protections("main", equity_series, realized_sells=[],
                            cfg=self._cfg(monthly_loss_stop=-0.08))
        self.assertFalse(res["allow_buys"])
        self.assertIn("monthly_loss", res["reason"])

    def test_consecutive_losing_sells_halts(self):
        m = _model()
        equity = [1.0, 1.01]
        sells = [{"action": "sell", "realized_pnl": -10}] * 5
        res = m.protections("main", equity, realized_sells=sells,
                            cfg=self._cfg(consec_losing_sells=5))
        self.assertFalse(res["allow_buys"])
        self.assertIn("consecutive_losing_sells", res["reason"])

    def test_turnover_block_halts(self):
        m = _model()
        equity = [1.0, 1.01]
        # turnover passed in pre-computed via realized_sells amounts? Use cfg + turnover input.
        res = m.protections("main", equity, realized_sells=[], cfg=self._cfg(turnover_block=0.75),
                            monthly_turnover=0.80)
        self.assertFalse(res["allow_buys"])
        self.assertIn("turnover", res["reason"])

    def test_history_unavailable_skips_protections(self):
        m = _model()
        res = m.protections("main", None, realized_sells=None, cfg=self._cfg())
        self.assertTrue(res["allow_buys"])
        self.assertIn("history unavailable", res["reason"])


# ───────────────────────────── G5 soft-brake ─────────────────────────────
class TestG5SoftBrake(unittest.TestCase):

    def _cfg(self, **over):
        c = dict(DEFAULT_GATES)
        c.update(over)
        return c

    def _state(self, total_value=1000.0):
        return {"total_value": total_value, "positions": []}

    def test_below_threshold_passes_all(self):
        m = _model()
        orders = [_order("600900", "sell", 100.0)]  # net sell 0.10 < 0.15
        decs = m.soft_brake(orders, self._state(), self._cfg(soft_brake_net_sell=0.15))
        self.assertTrue(all(d["verdict"] == "pass" for d in decs))

    def test_over_threshold_rejects_whole_account(self):
        m = _model()
        orders = [_order("600900", "sell", 120.0), _order("000333", "buy", 30.0)]
        # net sell = 120-30 = 90 -> 0.09? need > 0.15. make sells dominate.
        orders = [_order("600900", "sell", 200.0)]  # net sell 0.20 > 0.15
        decs = m.soft_brake(orders, self._state(), self._cfg(soft_brake_net_sell=0.15))
        self.assertTrue(all(d["verdict"] == "reject" for d in decs))
        self.assertTrue(all(d["gate"] == "G5" for d in decs))
        self.assertIn("reduce-only", decs[0]["reason"])

    def test_net_sell_nets_buys(self):
        m = _model()
        # sell 200, buy 100 -> net sell 100 -> 0.10 < 0.15 -> pass
        orders = [_order("600900", "sell", 200.0), _order("000333", "buy", 100.0)]
        decs = m.soft_brake(orders, self._state(), self._cfg(soft_brake_net_sell=0.15))
        self.assertTrue(all(d["verdict"] == "pass" for d in decs))


# ───────────────────────────── G6 kill-switch ─────────────────────────────
class TestG6KillSwitch(unittest.TestCase):

    def test_inactive_by_default(self):
        m = _model()
        self.assertFalse(m.kill_switch_active())

    def test_active_when_file_present(self):
        data_dir = tempfile.mkdtemp()
        open(os.path.join(data_dir, "EXECUTION_HALT"), "w").close()
        m = _model(data_dir=data_dir)
        self.assertTrue(m.kill_switch_active())

    def test_active_when_mode_halt(self):
        m = _model(mode="halt")
        self.assertTrue(m.kill_switch_active())

    def test_custom_kill_switch_path(self):
        data_dir = tempfile.mkdtemp()
        open(os.path.join(data_dir, "STOP_NOW"), "w").close()
        cfg = {"mode": "shadow", "eps": 0.005, "gates": dict(DEFAULT_GATES),
               "kill_switch_path": "STOP_NOW"}
        m = ExecutionModel(data_dir=data_dir, exec_config=cfg, mode="shadow")
        self.assertTrue(m.kill_switch_active())


# ─────────────────────── execute_plan orchestration ───────────────────────
class TestExecutePlanOrchestration(unittest.TestCase):

    def _setup(self, gates=None):
        data_dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(data_dir, "accounts"))
        os.makedirs(os.path.join(data_dir, "trades"))
        os.makedirs(os.path.join(data_dir, "market-data"))
        # sector + whitelist for 600900
        with open(os.path.join(data_dir, "market-data", "watchlist.json"), "w") as f:
            json.dump({"stocks": [{"code": "600900", "sector": "电力"},
                                  {"code": "000333", "sector": "家电"}]}, f)
        main_path = os.path.join(data_dir, "accounts", "main.json")
        with open(main_path, "w") as f:
            json.dump({"total_value": 1000.0, "positions": [
                {"code": "600900", "market_value": 100.0, "current_price": 27.72},
            ]}, f)
        cfg = {"mode": "shadow", "eps": 0.005, "gates": dict(gates or DEFAULT_GATES),
               "kill_switch_path": "EXECUTION_HALT"}
        m = ExecutionModel(data_dir=data_dir, exec_config=cfg, mode="shadow")
        return data_dir, main_path, m

    def test_passing_gates_shadow_does_not_execute_or_write_accounts(self):
        data_dir, main_path, m = self._setup()
        before = os.path.getmtime(main_path)
        trades_before = sorted(os.listdir(os.path.join(data_dir, "trades")))
        plan = {"decision": "APPROVED", "target_weights": {"600900": 0.15}}
        prices = {"600900": 27.72}
        # realistic NAV so the 0.05 buy (50k) clears default min_order_amount (1000)
        state = {"total_value": 1_000_000.0, "positions": [
            {"code": "600900", "market_value": 100_000.0, "current_price": 27.72}]}
        report = m.execute_plan("main", plan, prices, state,
                                rules={"max_single_position": 0.5, "max_sector_exposure": 0.5,
                                       "total_position_limit": 0.9},
                                date_str="2026-06-05", plan_date="2026-06-05")
        self.assertEqual(report["executed"], 0)
        # at least one pass decision
        verdicts = [d["verdict"] for d in report["decisions"]]
        self.assertIn("pass", verdicts)
        # accounts + trades untouched
        self.assertEqual(before, os.path.getmtime(main_path))
        self.assertEqual(trades_before, sorted(os.listdir(os.path.join(data_dir, "trades"))))

    def test_g0_failure_rejects_all_candidates(self):
        data_dir, main_path, m = self._setup()
        plan = {"decision": "REJECTED", "target_weights": {"600900": 0.15}}
        prices = {"600900": 27.72}
        state = {"total_value": 1000.0, "positions": [
            {"code": "600900", "market_value": 100.0, "current_price": 27.72}]}
        report = m.execute_plan("main", plan, prices, state, rules={},
                                date_str="2026-06-05", plan_date="2026-06-05")
        self.assertEqual(report["executed"], 0)
        self.assertTrue(all(d["gate"] == "G0" and d["verdict"] == "reject"
                            for d in report["decisions"]))

    def test_kill_switch_halts_all(self):
        data_dir, main_path, m = self._setup()
        open(os.path.join(data_dir, "EXECUTION_HALT"), "w").close()
        plan = {"decision": "APPROVED", "target_weights": {"600900": 0.15}}
        prices = {"600900": 27.72}
        state = {"total_value": 1000.0, "positions": [
            {"code": "600900", "market_value": 100.0, "current_price": 27.72}]}
        report = m.execute_plan("main", plan, prices, state,
                                rules={"max_single_position": 0.5, "max_sector_exposure": 0.5,
                                       "total_position_limit": 0.9},
                                date_str="2026-06-05", plan_date="2026-06-05")
        self.assertEqual(report["executed"], 0)
        self.assertTrue(all(d["verdict"] == "halt" and d["gate"] == "G6"
                            for d in report["decisions"]))

    def test_report_counts_present(self):
        data_dir, main_path, m = self._setup()
        plan = {"decision": "APPROVED", "target_weights": {"600900": 0.15}}
        prices = {"600900": 27.72}
        state = {"total_value": 1000.0, "positions": [
            {"code": "600900", "market_value": 100.0, "current_price": 27.72}]}
        report = m.execute_plan("main", plan, prices, state,
                                rules={"max_single_position": 0.5, "max_sector_exposure": 0.5,
                                       "total_position_limit": 0.9},
                                date_str="2026-06-05", plan_date="2026-06-05")
        self.assertIn("counts", report)
        for k in ("pass", "clamp", "reject", "halt"):
            self.assertIn(k, report["counts"])


if __name__ == "__main__":
    unittest.main()
