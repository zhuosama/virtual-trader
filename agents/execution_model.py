#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Execution Model（受控自主执行 · Phase 1 shadow 骨架 + Phase 2 闸门链 G0–G6）

职责 = "把 APPROVED 目标权重在闸门内变成候选订单"。本阶段仍 **shadow only**：
gate 链只 ANNOTATE/FILTER 候选订单并记录决策，绝不碰 accounts/*.json 或
trades/（真实成交属 Phase 3）。executed 恒为 0。设计见
docs/2026-06-05-controlled-autonomous-execution-design.md §5/§6/§7/§12 Phase 2。

闸门链顺序（每张候选订单产出 {order, verdict, gate, reason}，
verdict ∈ {pass, clamp, reject, halt}）：
  G0 计划契约（账户级，结构/新鲜度/decision==APPROVED）
  G1 目标→差额（diff_to_orders，已在 Phase 1 完成）
  G2 风控夹逼（单票/总仓/行业上限，clamp 买单；卖单永不被上限挡）
  G3 盘前防呆（每日单数/单笔下限/价格偏离/白名单）
  G4 protections / 熔断（回撤/月亏/连亏卖/换手——复用 h35 阈值数学；账户级）
  G5 软刹车（单账户单日净卖出 > 阈值 → 全账户当日降级 alert-only；账户级）
  G6 kill-switch（EXECUTION_HALT 文件存在 / mode==halt → 全 halt）

确定性 reduce-only 路径（coordinator._process_risk_actions 等）不在本模块内，
亦不被本模块触及——它仍是唯一无条件自动写盘的地板。

h35 阈值来源（scripts/h35_shadow_account_executor.py:82-241），本模块以纯函数
复用其阈值逻辑（不直接 import backtest-shaped 入口）：
  drawdown_stop -0.12 / monthly_loss_stop -0.08 / consec_losing_sells 5 /
  turnover_warn 0.40 / turnover_block 0.75。
"""

import json
import os
from datetime import datetime
from typing import Dict, List, Optional


# 闸门默认阈值（与 config/execution.json 的 gates 一致；config 缺省时兜底）。
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

# canary 灰度硬上限（mode==canary 时叠在闸门链之后；live 跳过；shadow 不执行）。
DEFAULT_CANARY = {
    "per_order": 0.02,
    "max_buys": 1,
    "max_sells": 1,
    "daily_turnover": 0.04,
}


# ── Phase 4 · 可观测（修假绿）：纯函数，把 run_autonomous_execution 的 summary 变成
#    (a) 落盘用的紧凑 execution_decisions 视图，(b) 企业微信/盘后人读的中文执行真相块。
#    无副作用、不碰盘，summary 注入即可单测。 ──────────────────────────────────────


def _agg_top_rejections(decisions, limit: int = 3):
    """把一个账户的 decisions 里 verdict==reject 的项按 (gate, reason) 去重聚合，
    返回出现次数最多的前 limit 条 [{gate, reason, count}]。"""
    buckets = {}
    order = []
    for d in decisions or []:
        if not isinstance(d, dict) or d.get("verdict") != "reject":
            continue
        gate = d.get("gate", "")
        reason = d.get("reason", "")
        key = (gate, reason)
        if key not in buckets:
            buckets[key] = 0
            order.append(key)
        buckets[key] += 1
    ranked = sorted(order, key=lambda k: (-buckets[k], order.index(k)))
    return [{"gate": k[0], "reason": k[1], "count": buckets[k]} for k in ranked[:limit]]


def build_execution_decisions(summary: Optional[Dict]) -> Dict:
    """从 run_autonomous_execution 的 summary 派生紧凑、可落盘的 execution_decisions 视图。

    每账户：{candidates, passed (pass+clamp), rejected, halted, executed,
    rolled_back, top_rejections:[{gate,reason,count} ≤3]}；顶层带 mode、
    ledger_validation_passed、degraded_reason（若有）。完整 summary 由调用方另存。
    对 {'error': ...} 之类异常 summary 也安全（accounts 置空）。
    """
    summary = summary or {}
    out: Dict = {
        "mode": summary.get("mode"),
        "date": summary.get("date"),
        "plan_found": summary.get("plan_found"),
        "accounts": {},
    }
    if "ledger_validation_passed" in summary:
        out["ledger_validation_passed"] = summary["ledger_validation_passed"]
    if summary.get("degraded_reason"):
        out["degraded_reason"] = summary["degraded_reason"]
    if summary.get("error"):
        out["error"] = summary["error"]

    accounts = summary.get("accounts")
    if isinstance(accounts, dict):
        for acct, a in accounts.items():
            a = a or {}
            counts = a.get("counts", {}) or {}
            out["accounts"][acct] = {
                "candidates": a.get("candidates", 0),
                "passed": counts.get("pass", 0) + counts.get("clamp", 0),
                "rejected": counts.get("reject", 0),
                "halted": counts.get("halt", 0),
                "executed": a.get("executed", 0),
                "rolled_back": bool(a.get("rolled_back", False)),
                "top_rejections": _agg_top_rejections(a.get("decisions", [])),
            }
    return out


def _is_halted(summary: Dict) -> bool:
    if summary.get("mode") == "halt":
        return True
    counts = summary.get("counts", {}) or {}
    return counts.get("halt", 0) > 0


def _is_rolled_back_or_degraded(summary: Dict) -> bool:
    if summary.get("degraded_reason"):
        return True
    accounts = summary.get("accounts", {}) or {}
    return any((a or {}).get("rolled_back") for a in accounts.values())


def format_execution_summary(summary: Optional[Dict]) -> str:
    """中文执行真相块（企业微信/盘后人读）。让以下状态绝不被误读为成功：

    - 正常：`🤖 自主执行(<mode>): 计划N → 闸门通过M → 否决K | 成交X`
    - 有否决：附 ≤3 行 `· 否决K1: <gate> <reason>`（按原因聚合）
    - kill-switch/halt：醒目 `⛔ 自主执行已停 (kill-switch/halt) — 仅确定性 reduce-only 运行`
    - 回滚/降级：醒目 `⚠️ 自主执行已回滚: <degraded_reason>` 且 成交0
    - shadow：标注 `（影子模式 · 未真实成交）`，避免把影子候选误当真实成交
    - 无计划/无候选：`🤖 自主执行(<mode>): 无计划/无候选`
    """
    summary = summary or {}
    mode = summary.get("mode", "?")
    lines = []

    if summary.get("error"):
        lines.append(f"🤖 自主执行({mode}): 异常 — {summary['error']}")
        return "\n".join(lines)

    counts = summary.get("counts", {}) or {}
    n_plan = sum(
        (a or {}).get("candidates", 0)
        for a in (summary.get("accounts", {}) or {}).values()
    )
    passed = counts.get("pass", 0) + counts.get("clamp", 0)
    vetoed = counts.get("reject", 0) + counts.get("halt", 0)
    executed = sum(
        (a or {}).get("executed", 0)
        for a in (summary.get("accounts", {}) or {}).values()
    )

    halted = _is_halted(summary)
    degraded = _is_rolled_back_or_degraded(summary)

    # 无计划/无候选（且未 halt/降级）→ 简短一行。
    if not summary.get("plan_found") or (n_plan == 0 and not halted and not degraded):
        line = f"🤖 自主执行({mode}): 无计划/无候选"
        if mode == "shadow":
            line += "（影子模式 · 未真实成交）"
        lines.append(line)
        if degraded:
            lines.append(f"⚠️ 自主执行已回滚: {summary.get('degraded_reason', '降级')}")
        return "\n".join(lines)

    # 主行
    head = (f"🤖 自主执行({mode}): 计划{n_plan} → 闸门通过{passed} → "
            f"否决{vetoed} | 成交{0 if degraded else executed}")
    if mode == "shadow":
        head += "（影子模式 · 未真实成交）"
    lines.append(head)

    # halt 横幅（最醒目，紧跟主行）
    if halted:
        lines.append("⛔ 自主执行已停 (kill-switch/halt) — 仅确定性 reduce-only 运行")

    # 回滚/降级横幅
    if degraded:
        lines.append(f"⚠️ 自主执行已回滚: {summary.get('degraded_reason', '降级')}")

    # 否决原因明细（跨账户聚合，最多 3 行）
    all_rejects = []
    for a in (summary.get("accounts", {}) or {}).values():
        all_rejects.extend((a or {}).get("decisions", []) or [])
    top = _agg_top_rejections(all_rejects, limit=3)
    for r in top:
        lines.append(f"· 否决{r['count']}: {r['gate']} {r['reason']}")

    return "\n".join(lines)


class ExecutionModel:
    """受控自主执行模型（Phase 2：shadow + 闸门链 G0–G6）。"""

    def __init__(self, data_dir: str, exec_config: Optional[Dict] = None, mode: Optional[str] = None):
        self.data_dir = data_dir
        # 配置优先级：显式 exec_config > config/execution.json > 默认 shadow
        cfg = exec_config if exec_config is not None else self._load_config(data_dir)
        self.config = cfg or {}
        self.mode = mode or self.config.get("mode") or "shadow"
        self.eps = self.config.get("eps", 0.005)
        if self.eps is None:
            self.eps = 0.005
        self.kill_switch_path = self.config.get("kill_switch_path") or "EXECUTION_HALT"

    @staticmethod
    def _load_config(data_dir: str) -> Dict:
        """读 config/execution.json（缺省返回 {} → 默认 shadow）。"""
        path = os.path.join(data_dir, "config", "execution.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}

    def gates_config(self, account: Optional[str] = None) -> Dict:
        """返回闸门阈值：per_account[account] 覆盖 top-level gates，缺省用 DEFAULT_GATES。"""
        merged = dict(DEFAULT_GATES)
        merged.update(self.config.get("gates", {}) or {})
        if account:
            per = (self.config.get("per_account", {}) or {}).get(account, {}) or {}
            per_gates = per.get("gates", per)  # 容许直接给阈值或嵌套 gates
            if isinstance(per_gates, dict):
                merged.update({k: v for k, v in per_gates.items() if k in DEFAULT_GATES})
        return merged

    def canary_config(self) -> Dict:
        """canary 灰度上限：config.canary 覆盖 DEFAULT_CANARY。"""
        merged = dict(DEFAULT_CANARY)
        cfg = self.config.get("canary", {}) or {}
        if isinstance(cfg, dict):
            merged.update({k: v for k, v in cfg.items() if k in DEFAULT_CANARY})
        return merged

    # ──────────────────── G7 canary 灰度硬上限（canary mode only）────────────────
    def apply_canary_caps(self, orders: List[Dict], account_state: Dict,
                          cfg: Dict) -> List[Dict]:
        """canary 模式专属硬上限（live 跳过、shadow 不执行）。纯函数。

        对【已过闸门链的存活订单】依次施加：
          1. per_order：单笔 est_amount ≤ per_order × NAV，超出则 clamp。
          2. max_buys / max_sells：每账户每日最多 N 买 + N 卖。**保留规模最大、
             否决规模最小**的额外单（reject extras, smallest first）——大单代表
             diff 给出的最强意见，小单优先牺牲，便于灰度期最小风险曝光。
          3. daily_turnover：一旦累计 Σ(clamp 后 est_amount)/NAV 将 > daily_turnover，
             该单及其后所有单 reject（按输入顺序流式判定）。

        返回 [{order, verdict: pass|clamp|reject, gate:"G7", reason}]，逐单留痕。
        输入顺序在返回中保持不变（便于 caller 与候选订单对齐）。
        """
        cfg = cfg or {}
        nav = (account_state or {}).get("total_value", 0) or 0
        per_order = cfg.get("per_order")
        max_buys = cfg.get("max_buys")
        max_sells = cfg.get("max_sells")
        daily_turnover = cfg.get("daily_turnover")

        n = len(orders or [])
        verdicts: List[Optional[str]] = [None] * n
        reasons: List[str] = [""] * n
        clamped_amounts: List[float] = [0.0] * n
        work_orders: List[Dict] = [dict(o) for o in (orders or [])]

        # ── 1. per_order clamp（先夹单笔，再做计数/换手判定）────────────────
        for i, o in enumerate(work_orders):
            amt = o.get("est_amount", 0) or 0
            if per_order is not None and nav > 0:
                cap = per_order * nav
                if amt > cap + 1e-9:
                    o["est_amount"] = cap
                    delta = abs(o.get("delta_weight") or 0)
                    if delta:
                        # 同比缩 delta_weight/target（保持与新 est_amount 一致）
                        scale = cap / amt if amt else 0
                        o["delta_weight"] = (o.get("delta_weight") or 0) * scale
                    amt = cap
                    verdicts[i] = "clamp"
                    reasons[i] = f"per_order cap {per_order} of NAV"
            clamped_amounts[i] = amt

        # ── 2. 买/卖计数上限：保留规模最大者，否决规模最小的额外单 ────────────
        def _apply_count_cap(side: str, cap: Optional[int], label: str):
            if cap is None:
                return
            idxs = [i for i, o in enumerate(work_orders) if o.get("side") == side]
            # 按 clamp 后规模降序排序，超出 cap 的（最小者）reject
            idxs_sorted = sorted(idxs, key=lambda i: clamped_amounts[i], reverse=True)
            for rank, i in enumerate(idxs_sorted):
                if rank >= cap:
                    verdicts[i] = "reject"
                    reasons[i] = f"{label} {cap} per account/day"

        _apply_count_cap("buy", max_buys, "max_buys")
        _apply_count_cap("sell", max_sells, "max_sells")

        # ── 3. 累计换手上限（流式，按输入顺序，跳过已 reject 的单）──────────────
        cum = 0.0
        for i in range(n):
            if verdicts[i] == "reject":
                continue
            if daily_turnover is not None and nav > 0:
                prospective = cum + clamped_amounts[i]
                if prospective / nav > daily_turnover + 1e-9:
                    verdicts[i] = "reject"
                    reasons[i] = f"daily_turnover cap {daily_turnover} of NAV"
                    continue
                cum = prospective
            else:
                cum += clamped_amounts[i]

        decisions: List[Dict] = []
        for i, o in enumerate(work_orders):
            v = verdicts[i] or "pass"
            r = reasons[i] or "within canary caps"
            decisions.append({"order": o, "verdict": v, "gate": "G7", "reason": r})
        return decisions

    # ──────────────────────────── G1：目标→差额 ────────────────────────────
    def diff_to_orders(
        self,
        positions: List[Dict],
        target_weights: Dict[str, float],
        prices: Dict[str, float],
        account: str,
        total_value: float,
    ) -> List[Dict]:
        """G1 纯函数：现持仓权重 vs 目标权重 → 候选订单。

        - 现仓权重 = market_value / total_value。
        - 对 (现持仓 ∪ 目标) 的每个 code：delta = target - current。
          |delta| >= eps 才出单：delta>0 → buy，delta<0 → sell。
        - 幂等：现仓已等于目标（|delta|<eps）→ 返回 []。
        """
        if not total_value or total_value <= 0:
            return []

        # 安全语义：空/缺目标 = 无意见 = 持有不动，绝不当成"目标全 0 → 清仓"。
        if not target_weights:
            return []

        current: Dict[str, float] = {}
        for pos in positions or []:
            current[pos["code"]] = pos.get("market_value", 0) / total_value

        codes = set(current) | set(target_weights or {})
        orders: List[Dict] = []
        for code in sorted(codes):
            cur_w = current.get(code, 0.0)
            tgt_w = (target_weights or {}).get(code, 0.0)
            delta = tgt_w - cur_w
            if abs(delta) < self.eps:
                continue
            orders.append({
                "account": account,
                "code": code,
                "side": "buy" if delta > 0 else "sell",
                "delta_weight": delta,
                "target_weight": tgt_w,
                "est_amount": abs(delta) * total_value,
                "price": (prices or {}).get(code),
            })
        return orders

    # ──────────────────────────── G0：计划契约 ────────────────────────────
    def check_plan_contract(self, plan: Dict, date_str: str,
                            plan_date: Optional[str] = None) -> Dict:
        """G0 账户级结构/新鲜度校验（非签名校验——每日计划无签名，已确认）。

        校验：plan 是 dict、decision=="APPROVED"、target_weights 是 dict、
        plan_date == date_str（caller 提供；None 表示无法判定新鲜度 → 跳过该子项）。
        返回 {ok, gate:"G0", reason}。任一失败 → ok=False，caller 据此否决全账户候选。
        """
        if not isinstance(plan, dict):
            return {"ok": False, "gate": "G0", "reason": "plan not a dict"}
        if plan.get("decision") != "APPROVED":
            return {"ok": False, "gate": "G0",
                    "reason": f"decision!=APPROVED ({plan.get('decision')!r})"}
        tw = plan.get("target_weights")
        if not isinstance(tw, dict):
            return {"ok": False, "gate": "G0", "reason": "target_weights not a dict"}
        if plan_date is not None and plan_date != date_str:
            return {"ok": False, "gate": "G0",
                    "reason": f"freshness: plan_date {plan_date} != {date_str}"}
        reason = "plan contract ok"
        if plan_date is None:
            reason = "plan contract ok (freshness unverifiable — plan_date unavailable)"
        return {"ok": True, "gate": "G0", "reason": reason}

    # ──────────────────────────── G2：风控夹逼 ────────────────────────────
    def _load_sector_map(self) -> Optional[Dict[str, str]]:
        """holdings→行业映射：优先 market-data/watchlist.json 的 stocks[].sector。

        返回 code→sector dict；无可读映射 → None（G2 行业子项降级为 no-op）。
        """
        path = os.path.join(self.data_dir, "market-data", "watchlist.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return None
        stocks = data.get("stocks") if isinstance(data, dict) else None
        if not isinstance(stocks, list):
            return None
        m = {s.get("code"): s.get("sector") for s in stocks
             if isinstance(s, dict) and s.get("code") and s.get("sector")}
        return m or None

    def apply_risk_limits(self, orders: List[Dict], account_state: Dict,
                          rules: Dict) -> List[Dict]:
        """G2：按 active.json 硬上限夹逼【结果组合】（现持仓 ± 订单）。

        - 单票目标 ≤ max_single_position；总仓 ≤ total_position_limit；
          单行业 ≤ max_sector_exposure（仅当有 sector map，否则记 no-op）。
        - 越限的 BUY 被 clamp 到刚好不越限；clamp 后规模 < eps → reject。
        - SELL 永不被上限挡（降仓只会让组合更安全）。
        这是 compute_target_weights 上游夹逼之上的 defense-in-depth。
        """
        rules = rules or {}
        total_value = (account_state or {}).get("total_value", 0) or 0
        positions = (account_state or {}).get("positions", []) or []
        max_single = rules.get("max_single_position")
        max_total = rules.get("total_position_limit")
        max_sector = rules.get("max_sector_exposure")
        sector_map = self._load_sector_map()

        # 现持仓权重
        cur_w: Dict[str, float] = {}
        for pos in positions:
            if total_value > 0:
                cur_w[pos["code"]] = pos.get("market_value", 0) / total_value
        book_w = sum(cur_w.values())

        decisions: List[Dict] = []
        for order in orders:
            o = dict(order)
            code = o["code"]
            reasons: List[str] = []
            verdict = "pass"

            if o["side"] == "sell":
                # 卖单永不被上限挡（仅降仓）
                if sector_map is None:
                    reasons.append("sector data unavailable — skipped")
                decisions.append({"order": o, "verdict": "pass", "gate": "G2",
                                  "reason": "; ".join(reasons) or "sell never blocked by upper limits"})
                # 卖出降低该名/总仓/行业敞口
                cur = cur_w.get(code, 0.0)
                new = max(0.0, cur - (o.get("est_amount", 0) / total_value if total_value > 0 else 0))
                book_w += (new - cur)
                cur_w[code] = new
                continue

            # BUY：以"现权重 + buy 增量"为结果权重逐项夹逼
            cur = cur_w.get(code, 0.0)
            delta = (o.get("est_amount", 0) / total_value) if total_value > 0 else 0.0
            allowed_delta = delta
            # 行业 map 不可读 → 行业子项整段降级为 no-op（graceful），统一记一次
            if sector_map is None:
                reasons.append("sector data unavailable — skipped")

            # 单票上限
            if max_single is not None:
                room = max_single - cur
                if delta > room:
                    allowed_delta = min(allowed_delta, max(0.0, room))
                    reasons.append(f"max_single_position {max_single}")
            # 总仓上限
            if max_total is not None:
                room = max_total - book_w
                if allowed_delta > room:
                    allowed_delta = min(allowed_delta, max(0.0, room))
                    reasons.append(f"total_position_limit {max_total}")
            # 行业上限（仅当有 sector map 且该 code 有行业归属）
            if max_sector is not None and sector_map is not None:
                sector = sector_map.get(code)
                if sector is None:
                    reasons.append("sector data unavailable — skipped")
                else:
                    sec_cur = sum(cur_w.get(c, 0.0) for c, s in sector_map.items()
                                  if s == sector and c in cur_w)
                    room = max_sector - sec_cur
                    if allowed_delta > room:
                        allowed_delta = min(allowed_delta, max(0.0, room))
                        reasons.append(f"max_sector_exposure {max_sector}")

            if allowed_delta < self.eps:
                verdict = "reject"
                o["est_amount"] = 0.0
                reason = "clamped below eps: " + ("; ".join(reasons) or "no room")
                decisions.append({"order": o, "verdict": verdict, "gate": "G2", "reason": reason})
                # 被否，不改变 book
                continue

            if allowed_delta < delta - 1e-12:
                verdict = "clamp"
                o["est_amount"] = allowed_delta * total_value
                o["delta_weight"] = allowed_delta
                o["target_weight"] = cur + allowed_delta
            reason = "; ".join(reasons) if reasons else "within risk limits"
            decisions.append({"order": o, "verdict": verdict, "gate": "G2", "reason": reason})
            # 结果组合更新（即便 clamp，按 allowed_delta 累加）
            cur_w[code] = cur + allowed_delta
            book_w += allowed_delta

        return decisions

    # ──────────────────────────── G3：盘前防呆 ────────────────────────────
    def pretrade_check(self, order: Dict, day_state: Dict, cfg: Dict) -> Dict:
        """G3 单订单防呆。返回 {ok, gate:"G3", reason}。

        - 当日该账户单数 > max_orders → reject。
        - BUY 且 est_amount < min_order_amount → reject（SELL 不受下限挡）。
        - |price - prev_close|/prev_close > price_deviation → reject；
          prev_close 不可得（None）→ 记 "price ref unavailable — skipped" 不挡。
        - BUY 不在白名单 → reject；SELL 永远放行（清仓不需白名单）。
        """
        cfg = cfg or {}
        side = order.get("side")
        reasons: List[str] = []

        max_orders = cfg.get("max_orders")
        if max_orders is not None and day_state.get("order_count", 0) > max_orders:
            return {"ok": False, "gate": "G3",
                    "reason": f"max_orders exceeded ({day_state.get('order_count')} > {max_orders})"}

        if side == "buy":
            min_amt = cfg.get("min_order_amount")
            if min_amt is not None and (order.get("est_amount", 0) or 0) < min_amt:
                return {"ok": False, "gate": "G3",
                        "reason": f"below min_order_amount ({order.get('est_amount')} < {min_amt})"}

        dev = cfg.get("price_deviation")
        prev_close = day_state.get("prev_close")
        price = order.get("price")
        if dev is not None and price is not None and prev_close:
            d = abs(price - prev_close) / prev_close
            if d > dev:
                return {"ok": False, "gate": "G3",
                        "reason": f"price_deviation {d:.3f} > {dev}"}
        elif dev is not None and (prev_close in (None, 0)):
            reasons.append("price ref unavailable — skipped")

        if side == "buy":
            whitelist = day_state.get("whitelist")
            if whitelist is not None and order.get("code") not in whitelist:
                return {"ok": False, "gate": "G3",
                        "reason": f"not in whitelist: {order.get('code')}"}

        return {"ok": True, "gate": "G3",
                "reason": "; ".join(reasons) if reasons else "pretrade ok"}

    # ──────────────────────────── G4：protections ────────────────────────────
    @staticmethod
    def _drawdown_from_equity(equity_series) -> Optional[float]:
        """从权益序列（list 或 month→list dict）计算 peak→trough 最大回撤。"""
        seq = ExecutionModel._flatten_equity(equity_series)
        if not seq:
            return None
        peak = seq[0]
        max_dd = 0.0
        for v in seq:
            if v > peak:
                peak = v
            if peak > 0:
                dd = (v - peak) / peak
                if dd < max_dd:
                    max_dd = dd
        return max_dd

    @staticmethod
    def _flatten_equity(equity_series) -> List[float]:
        if equity_series is None:
            return []
        if isinstance(equity_series, dict):
            seq: List[float] = []
            for month in sorted(equity_series.keys()):
                seq.extend(equity_series[month])
            return [float(x) for x in seq]
        return [float(x) for x in equity_series]

    @staticmethod
    def _worst_monthly_return(equity_series) -> Optional[float]:
        """月度区间收益最差者。equity_series 为 month→[values] dict 时按月分组；
        为扁平 list 时无月度切分 → 返回 None（不触发月亏熔断）。
        复用 h35.worst_monthly_return 的"区间首尾比"阈值逻辑。"""
        if not isinstance(equity_series, dict):
            return None
        worst = None
        for month in sorted(equity_series.keys()):
            vals = equity_series[month]
            if not vals:
                continue
            start, end = float(vals[0]), float(vals[-1])
            if start > 0:
                r = end / start - 1.0
                worst = r if worst is None else min(worst, r)
        return worst

    @staticmethod
    def _consecutive_losing_sells(realized_sells: List[Dict]) -> int:
        """复用 h35.compute_consecutive_losing_sells 阈值逻辑：从尾部数连续亏损卖出。"""
        streak = 0
        for t in reversed(realized_sells or []):
            if t.get("action") != "sell":
                continue
            pnl = t.get("realized_pnl", t.get("pnl", 0))
            if pnl is not None and pnl <= 0:
                streak += 1
            else:
                break
        return streak

    def protections(self, account: str, equity_series, realized_sells,
                    cfg: Dict, monthly_turnover: Optional[float] = None) -> Dict:
        """G4 账户级熔断。返回 {allow_buys, gate:"G4", reason}。

        - 回撤（peak→trough）< drawdown_stop → halt 新买。
        - 最差月度收益 < monthly_loss_stop → halt 新买。
        - 连续亏损卖出 >= consec_losing_sells → halt 新买。
        - monthly_turnover > turnover_block → block 新买（warn 仅记录不挡）。
        SELL 始终放行（本函数只裁 BUY）。
        history 不可读（equity_series 与 realized_sells 均 None）→ 跳过、不挡。
        复用 h35 阈值数学（h35_shadow_account_executor.py:82-241）。
        """
        cfg = cfg or {}
        if equity_series is None and realized_sells is None:
            return {"allow_buys": True, "gate": "G4",
                    "reason": "history unavailable — protections skipped"}

        reasons: List[str] = []
        allow = True

        dd_stop = cfg.get("drawdown_stop")
        dd = self._drawdown_from_equity(equity_series)
        if dd_stop is not None and dd is not None and dd < dd_stop:
            allow = False
            reasons.append(f"drawdown {dd:.3f} < {dd_stop}")

        ml_stop = cfg.get("monthly_loss_stop")
        worst = self._worst_monthly_return(equity_series)
        if ml_stop is not None and worst is not None and worst < ml_stop:
            allow = False
            reasons.append(f"monthly_loss {worst:.3f} < {ml_stop}")

        consec = cfg.get("consec_losing_sells")
        streak = self._consecutive_losing_sells(realized_sells or [])
        if consec is not None and streak >= consec:
            allow = False
            reasons.append(f"consecutive_losing_sells {streak} >= {consec}")

        warn = cfg.get("turnover_warn")
        block = cfg.get("turnover_block")
        if monthly_turnover is not None:
            if block is not None and monthly_turnover > block:
                allow = False
                reasons.append(f"turnover {monthly_turnover:.3f} > block {block}")
            elif warn is not None and monthly_turnover > warn:
                reasons.append(f"turnover {monthly_turnover:.3f} > warn {warn}")

        return {"allow_buys": allow, "gate": "G4",
                "reason": "; ".join(reasons) if reasons else "protections ok"}

    # ──────────────────────────── G5：软刹车 ────────────────────────────
    def soft_brake(self, orders: List[Dict], account_state: Dict, cfg: Dict) -> List[Dict]:
        """G5 账户级软刹车：单日净卖出 / NAV > soft_brake_net_sell → 全账户当日
        降级 alert-only（全部订单 reject，gate="G5"）。否则全 pass（gate="G5"）。
        净卖出 = Σ(sell est_amount) - Σ(buy est_amount)。
        """
        cfg = cfg or {}
        total_value = (account_state or {}).get("total_value", 0) or 0
        threshold = cfg.get("soft_brake_net_sell")
        net_sell = 0.0
        for o in orders or []:
            amt = o.get("est_amount", 0) or 0
            if o.get("side") == "sell":
                net_sell += amt
            else:
                net_sell -= amt
        frac = (net_sell / total_value) if total_value > 0 else 0.0

        if threshold is not None and frac > threshold:
            reason = (f"soft-brake tripped: net sell {frac:.3f} > {threshold}; "
                      f"account degraded to alert-only for the day "
                      f"(deterministic reduce-only floor still runs)")
            return [{"order": dict(o), "verdict": "reject", "gate": "G5", "reason": reason}
                    for o in orders or []]
        return [{"order": dict(o), "verdict": "pass", "gate": "G5",
                 "reason": f"net sell {frac:.3f} <= {threshold}"} for o in orders or []]

    # ──────────────────────────── G6：kill-switch ────────────────────────────
    def kill_switch_active(self) -> bool:
        """G6：data_dir 下存在 kill_switch_path 文件，或 mode=='halt' → True。"""
        if self.mode == "halt":
            return True
        return os.path.exists(os.path.join(self.data_dir, self.kill_switch_path))

    # ──────────────────────────── 编排 ────────────────────────────
    def execute_plan(
        self,
        account: str,
        plan: Dict,
        prices: Dict[str, float],
        account_state: Dict,
        rules: Optional[Dict] = None,
        date_str: Optional[str] = None,
        plan_date: Optional[str] = None,
        equity_series=None,
        realized_sells=None,
        monthly_turnover: Optional[float] = None,
        day_order_count: int = 0,
    ) -> Dict:
        """消费单账户计划，跑闸门链 G0→G2→G3→G4→G5→G6，构建 decisions。

        STILL SHADOW-ONLY：reject/halt 丢弃订单、clamp 留调整后订单，但
        executed 恒为 0，绝不写 accounts/*.json 或 trades/（Phase 3 才执行）。

        legacy（Phase 1）调用 execute_plan(account, plan, prices, account_state)
        不传 date_str → G0 新鲜度无从判定，但仍跑 G2–G6（graceful）。
        """
        total_value = (account_state or {}).get("total_value", 0) or 0
        positions = (account_state or {}).get("positions", []) or []
        target_weights = (plan or {}).get("target_weights", {}) or {}
        gates = self.gates_config(account)

        candidates = self.diff_to_orders(positions, target_weights, prices, account, total_value)
        decisions: List[Dict] = []

        # ── G6 kill-switch（最高优先，先判：若 halt 则全部 halt）──────────────
        if self.kill_switch_active():
            decisions = [{"order": o, "verdict": "halt", "gate": "G6",
                          "reason": "kill-switch active (EXECUTION_HALT / mode=halt) — all orders halted"}
                         for o in candidates]
            return self._finalize(account, total_value, candidates, decisions)

        # ── G0 计划契约（账户级；date_str 已知才判）──────────────────────────
        if date_str is not None:
            g0 = self.check_plan_contract(plan, date_str, plan_date=plan_date)
            if not g0["ok"]:
                decisions = [{"order": o, "verdict": "reject", "gate": "G0",
                              "reason": g0["reason"]} for o in candidates]
                return self._finalize(account, total_value, candidates, decisions)

        # ── G2 风控夹逼 ──────────────────────────────────────────────────────
        g2_decisions = self.apply_risk_limits(candidates, account_state, rules or {})
        # 通过/夹逼的订单进入后续闸门；reject 留痕
        surviving = []  # list of (order, g2_decision)
        for d in g2_decisions:
            if d["verdict"] in ("reject", "halt"):
                decisions.append(d)
            else:
                surviving.append(d)

        # ── G4 账户级 protections（决定是否 halt 新买）──────────────────────
        g4 = self.protections(account, equity_series, realized_sells, gates,
                              monthly_turnover=monthly_turnover)
        buys_halted = not g4["allow_buys"]

        # ── G5 账户级软刹车（基于 G2 通过/夹逼后的订单）────────────────────
        survive_orders = [d["order"] for d in surviving]
        g5_decisions = self.soft_brake(survive_orders, account_state, gates)
        if g5_decisions and all(d["verdict"] == "reject" for d in g5_decisions):
            # 软刹车触发：全账户降级；surviving 全 reject(G5)
            decisions.extend(g5_decisions)
            return self._finalize(account, total_value, candidates, decisions)

        # ── 逐单 G3 防呆 + G4 buy-halt 应用 ─────────────────────────────────
        whitelist = self._build_whitelist(positions, gates, self._watchlist_codes())
        order_count = day_order_count
        for d in surviving:
            o = d["order"]
            # G4：若新买被熔断，buy 直接 reject(G4)
            if buys_halted and o.get("side") == "buy":
                decisions.append({"order": o, "verdict": "reject", "gate": "G4",
                                  "reason": "new buys halted: " + g4["reason"]})
                continue
            order_count += 1
            day_state = {
                "order_count": order_count,
                "holdings": {p["code"] for p in positions},
                "prev_close": (prices or {}).get(o.get("code")),
                "whitelist": whitelist,
            }
            g3 = self.pretrade_check(o, day_state, gates)
            if not g3["ok"]:
                order_count -= 1  # 否决的不计入当日单数
                decisions.append({"order": o, "verdict": "reject", "gate": "G3",
                                  "reason": g3["reason"]})
                continue
            # 通过全部闸门：保留 G2 的 verdict（pass 或 clamp）+ 注记
            verdict = d["verdict"] if d["verdict"] == "clamp" else "pass"
            reason = d["reason"]
            decisions.append({"order": o, "verdict": verdict,
                              "gate": "G3", "reason": reason})

        return self._finalize(account, total_value, candidates, decisions)

    def _watchlist_codes(self):
        """market-data/watchlist.json 的 stocks[].code 集合（缺省空集）。"""
        path = os.path.join(self.data_dir, "market-data", "watchlist.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return set()
        stocks = data.get("stocks") if isinstance(data, dict) else None
        if not isinstance(stocks, list):
            return set()
        return {s.get("code") for s in stocks if isinstance(s, dict) and s.get("code")}

    @staticmethod
    def _build_whitelist(positions: List[Dict], gates: Dict, watchlist_codes=None):
        """白名单 = config.whitelist（若有）否则 现持仓 ∪ watchlist codes。"""
        wl = gates.get("whitelist")
        holdings = {p["code"] for p in positions}
        if wl:
            return set(wl) | holdings
        return holdings | (watchlist_codes or set())

    def _finalize(self, account: str, total_value: float,
                  candidates: List[Dict], decisions: List[Dict]) -> Dict:
        """组装 report + 计数，shadow 写影子账本。executed 恒为 0。"""
        counts = {"pass": 0, "clamp": 0, "reject": 0, "halt": 0}
        for d in decisions:
            v = d.get("verdict")
            if v in counts:
                counts[v] += 1
        report = {
            "account": account,
            "mode": self.mode,
            "candidates": candidates,
            "executed": 0,
            "decisions": decisions,
            "counts": counts,
        }
        if self.mode == "shadow":
            self._write_shadow(account, report, total_value)
        return report

    def _write_shadow(self, account: str, report: Dict, total_value: float) -> None:
        """写影子账本：append jsonl 日志 + overwrite state 快照。绝不写 accounts/trades。"""
        logs_dir = os.path.join(self.data_dir, "value_account", "logs")
        reports_dir = os.path.join(self.data_dir, "value_account", "reports")
        os.makedirs(logs_dir, exist_ok=True)
        os.makedirs(reports_dir, exist_ok=True)

        ts = datetime.now().isoformat()
        record = {
            "timestamp": ts,
            "account": account,
            "mode": self.mode,
            "total_value": total_value,
            "candidates": report["candidates"],
            "decisions": report["decisions"],
            "counts": report.get("counts"),
            "executed": report["executed"],
        }

        log_path = os.path.join(logs_dir, f"exec_shadow_{account}.jsonl")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        # state 快照：按 account 覆盖最新一笔
        state_path = os.path.join(reports_dir, "exec_shadow_state.json")
        state: Dict = {}
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                state = json.load(f)
        except (OSError, json.JSONDecodeError):
            state = {}
        if not isinstance(state, dict):
            state = {}
        state[account] = record
        tmp_path = f"{state_path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, state_path)
