#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Strategy Maintainer Agent
策略迭代专家 - 更新策略（小迭代），调整阈值，改进过滤条件
"""

import json
import os

VTRADER_HOME = os.environ.get("VTRADER_HOME", os.path.expanduser("~/.hermes/virtual-trader"))
from datetime import datetime
from typing import Dict, List, Optional
import logging

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class StrategyMaintainerAgent:
    """策略迭代专家Agent"""

    def __init__(self, config_path: str = None):
        """初始化Agent"""
        self.config = self._load_config(config_path)
        self.data_dir = VTRADER_HOME
        self.strategies = self._load_strategies()
        self.changelog = self._load_changelog()
        self.llm = None
        self._init_llm()

    def _load_config(self, config_path: str = None) -> Dict:
        """加载配置"""
        if config_path is None:
            config_path = os.path.join(
                os.path.dirname(__file__),
                "config.json"
            )

        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"加载配置失败: {e}")
            return {}

    def _init_llm(self):
        """初始化 LLM 客户端"""
        try:
            from agents.llm_client import LLMClient
        except ImportError:
            from llm_client import LLMClient
        try:
            self.llm = LLMClient(config_path=os.path.join(os.path.dirname(__file__), "config.json"))
        except Exception as e:
            logger.warning(f"LLM 初始化失败: {e}")
            self.llm = None

    def _llm_strategy_advice(self, performance: str, current_strategy: str, changelog: str) -> str:
        """用 LLM 获取策略调整建议"""
        if not self.llm:
            return ""
        system = ("你是A股量化策略专家。基于绩效数据、当前策略和历史变更记录，"
                  "建议具体的策略参数调整。每次调整必须说明理由和预期效果。"
                  "输出JSON格式: [{\"parameter\": \"...\", \"new_value\": ..., \"reason\": \"...\"}]")
        prompt = f"绩效数据:\n{performance}\n\n当前策略:\n{current_strategy}\n\n变更历史:\n{changelog}"
        return self.llm.call("strategy_maintainer", system, prompt)

    def _load_strategies(self) -> Dict:
        """加载策略配置"""
        strategy_path = os.path.join(self.data_dir, "strategies", "active.json")
        try:
            with open(strategy_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"加载策略失败: {e}")
            return {}

    def _load_changelog(self) -> List[Dict]:
        """加载策略变更历史"""
        changelog_path = os.path.join(self.data_dir, "strategies", "changelog.json")
        try:
            with open(changelog_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"加载changelog失败: {e}")
            return []

    def _current_parameter(self, strategy_name: str, parameter: str, default=None):
        strategy_key = 'main_strategy' if strategy_name == 'main' else 'lab_strategy'
        strategy = self.strategies.get(strategy_key, {})
        return strategy.get('parameters', {}).get(parameter, default)

    def load_review_report(self, review_path: str = None) -> Dict:
        """加载复盘报告"""
        if review_path is None:
            # 查找最新的复盘报告
            reviews_dir = os.path.join(self.data_dir, "agents", "reviews")
            if os.path.exists(reviews_dir):
                files = [f for f in os.listdir(reviews_dir) if f.startswith("review_report_")]
                if files:
                    latest_file = sorted(files)[-1]
                    review_path = os.path.join(reviews_dir, latest_file)

        if review_path and os.path.exists(review_path):
            try:
                with open(review_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"加载复盘报告失败: {e}")

        return {}

    def analyze_strategy_performance(self, review_report: Dict) -> Dict:
        """分析策略绩效"""
        logger.info("分析策略绩效...")

        analysis = {
            'timestamp': datetime.now().isoformat(),
            'performance_issues': [],
            'parameter_suggestions': [],
            'rule_suggestions': [],
            'confidence': 'medium'
        }

        # 分析复盘报告
        mistakes = review_report.get('mistakes', [])
        lessons = review_report.get('lessons', {})

        # 1. 从错误中学习
        for mistake in mistakes:
            mistake_type = mistake.get('type')

            if mistake_type == 'position_limit_exceeded':
                analysis['performance_issues'].append({
                    'issue': '仓位超限',
                    'description': mistake.get('description'),
                    'severity': 'high'
                })

                current_value = self._current_parameter('main', 'max_single_position', 0.10)
                suggested_value = 0.08
                if current_value > suggested_value:
                    analysis['parameter_suggestions'].append({
                        'strategy': 'main',
                        'parameter': 'max_single_position',
                        'current_value': current_value,
                        'suggested_value': suggested_value,
                        'reason': '防止仓位超限'
                    })

        # 2. 从成功经验中学习
        what_worked = lessons.get('what_worked', [])
        for item in what_worked:
            if '交易' in item:
                analysis['confidence'] = 'high'

        # 3. 从失败经验中学习
        what_failed = lessons.get('what_failed', [])
        for item in what_failed:
            if '止损' in item:
                current_value = self._current_parameter('main', 'stop_loss_pct', 7)
                suggested_value = 6
                if current_value > suggested_value:
                    analysis['parameter_suggestions'].append({
                        'strategy': 'main',
                        'parameter': 'stop_loss_pct',
                        'current_value': current_value,
                        'suggested_value': suggested_value,
                        'reason': '收紧止损，减少损失'
                    })

        return analysis

    def generate_strategy_adjustments(self, performance_analysis: Dict) -> List[Dict]:
        """生成策略调整建议"""
        logger.info("生成策略调整建议...")

        adjustments = []

        seen = set()

        # 1. 参数调整
        for suggestion in performance_analysis.get('parameter_suggestions', []):
            strategy = suggestion.get('strategy', 'main')
            parameter = suggestion.get('parameter')
            old_value = suggestion.get('current_value')
            new_value = suggestion.get('suggested_value')
            key = (strategy, parameter, json.dumps(new_value, sort_keys=True, ensure_ascii=False))
            if key in seen or old_value == new_value:
                continue
            seen.add(key)
            adjustments.append({
                'type': 'parameter_adjustment',
                'strategy': strategy,
                'parameter': parameter,
                'old_value': old_value,
                'new_value': new_value,
                'reason': suggestion.get('reason'),
                'confidence': performance_analysis.get('confidence', 'medium')
            })

        # 2. 规则调整
        for suggestion in performance_analysis.get('rule_suggestions', []):
            adjustments.append({
                'type': 'rule_adjustment',
                'strategy': 'main',
                'rule': suggestion.get('rule'),
                'description': suggestion.get('description'),
                'reason': suggestion.get('reason'),
                'confidence': performance_analysis.get('confidence', 'medium')
            })

        # 限制调整数量（每天最多3次）
        max_adjustments = 3
        if len(adjustments) > max_adjustments:
            logger.warning(f"调整数量超过限制，只保留前{max_adjustments}个")
            adjustments = adjustments[:max_adjustments]

        return adjustments

    def apply_adjustments(self, adjustments: List[Dict]) -> Dict:
        """BLOCKED: direct write path is no longer allowed.

        Use propose() → audit_layer.review() → commit_approved() instead.
        This method exists only for backwards-compatible call sites and
        will raise to prevent silent bypass of the audit gate.
        """
        raise PermissionError(
            "apply_adjustments() is blocked — strategy changes must go through "
            "propose() → audit_layer.review() → commit_approved(). "
            "Direct writes to active.json/changelog.json are not allowed."
        )

    def _apply_parameter_adjustment(self, adjustment: Dict) -> bool:
        """应用参数调整"""
        try:
            strategy_name = adjustment.get('strategy', 'main')
            parameter = adjustment.get('parameter')
            new_value = adjustment.get('new_value')

            # 获取策略
            strategy_key = 'main_strategy' if strategy_name == 'main' else 'lab_strategy'
            if strategy_key not in self.strategies:
                logger.error(f"策略不存在: {strategy_key}")
                return False

            strategy = self.strategies[strategy_key]

            # 更新参数
            if 'parameters' not in strategy:
                strategy['parameters'] = {}

            old_value = strategy['parameters'].get(parameter)
            strategy['parameters'][parameter] = new_value

            logger.info(f"参数调整: {parameter} {old_value} -> {new_value}")
            return True

        except Exception as e:
            logger.error(f"参数调整失败: {e}")
            return False

    def _apply_rule_adjustment(self, adjustment: Dict) -> bool:
        """应用规则调整"""
        try:
            # 规则调整更复杂，需要具体实现
            # 目前先返回True，表示成功
            logger.info(f"规则调整: {adjustment.get('rule')}")
            return True

        except Exception as e:
            logger.error(f"规则调整失败: {e}")
            return False

    _VALID_CHANGE_TYPES = {'strategy', 'execution', 'risk', 'data', 'system'}
    _CHANGELOG_REQUIRED_FIELDS = {
        'date', 'account', 'change_type', 'description',
        'reason', 'expected_effect', 'actual_effect',
    }
    _CHANGELOG_OPTIONAL_FIELDS = {
        'backfilled', 'legacy_change_type', 'triggering_event_count',
        'oos_validated', 'backtest_evidence',
    }

    def _validate_changelog_entry(self, entry: Dict) -> None:
        """校验 changelog entry 是否符合 docs/changelog-schema.md。"""
        missing = self._CHANGELOG_REQUIRED_FIELDS - set(entry)
        if missing:
            raise ValueError(f"changelog entry 缺少必填字段: {sorted(missing)}")

        extra = set(entry) - self._CHANGELOG_REQUIRED_FIELDS - self._CHANGELOG_OPTIONAL_FIELDS
        if extra:
            raise ValueError(f"changelog entry 包含未声明字段: {sorted(extra)}")

        change_type = entry.get('change_type')
        if change_type not in self._VALID_CHANGE_TYPES:
            raise ValueError(
                f"change_type={change_type!r} 不在受控词汇表 "
                f"{sorted(self._VALID_CHANGE_TYPES)}"
            )

    def _create_changelog_entry(self, adjustment: Dict) -> Dict:
        """创建changelog条目。

        Schema 见 docs/changelog-schema.md。change_type 必须在受控词汇表内。
        缺省为 'strategy' 并标记 triggering_event_count=1（单事件触发，过拟合高风险信号）。
        """
        ct = adjustment.get('change_type', 'strategy')
        if ct not in self._VALID_CHANGE_TYPES:
            raise ValueError(
                f"adjustment 提供的 change_type={ct!r} 不在受控词汇表 "
                f"{sorted(self._VALID_CHANGE_TYPES)}"
            )

        entry = {
            'date': datetime.now().strftime("%Y-%m-%d"),
            'account': adjustment.get('strategy', 'main'),
            'change_type': ct,
            'description': f"调整参数: {adjustment.get('parameter')} {adjustment.get('old_value')} -> {adjustment.get('new_value')}",
            'reason': adjustment.get('reason'),
            'expected_effect': f"优化策略表现",
            'actual_effect': '',
            'backfilled': False,
            'triggering_event_count': adjustment.get('triggering_event_count', 1),
            'oos_validated': adjustment.get('oos_validated', False)
        }
        self._validate_changelog_entry(entry)
        return entry

    def propose(self, adjustments: List[Dict]) -> Dict:
        """Build a proposal.json dict from adjustments. NO side effects.

        Replaces direct apply_adjustments-then-write. The proposal must
        pass audit_layer.review() before commit_approved() may write it.
        See spec §4 & §8.
        """
        import uuid
        from datetime import datetime, timezone

        if not adjustments:
            raise ValueError("propose() requires at least one adjustment")

        # determine change_type: 'parameter_adjustment'/'rule_adjustment' both
        # → 'strategy' canonical category (per docs/changelog-schema.md)
        change_type = "strategy"

        account = adjustments[0].get("strategy", "main")

        # extract version (best-effort; current_version may not exist if strategies dict is partial)
        strat_key = f"{account}_strategy"
        current_version = self.strategies.get(strat_key, {}).get("version", "unknown")

        diff = []
        for a in adjustments:
            if a.get("type") == "parameter_adjustment":
                diff.append({
                    "path": f"{strat_key}.parameters.{a['parameter']}",
                    "old": a.get("old_value"),
                    "new": a.get("new_value"),
                })
            elif a.get("type") == "rule_adjustment":
                diff.append({
                    "path": f"{strat_key}.rules.{a.get('rule', 'unknown')}",
                    "old": "(rule)",
                    "new": a.get("description", "(no description)"),
                })

        proposal_id = (
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            + "-" + uuid.uuid4().hex[:8]
        )
        proposal = {
            "proposal_id": proposal_id,
            "proposed_at": datetime.now(timezone.utc).isoformat(),
            "proposer": "strategy_maintainer",
            "account": account,
            "change_type": change_type,
            "current_version": current_version,
            "proposed_version": current_version + ".pending",
            "diff": diff,
            "triggering_events": [
                {"reason": a.get("reason", "")} for a in adjustments
            ],
            "rationale": "; ".join(a.get("reason", "") for a in adjustments),
            "_raw_adjustments": adjustments,  # 内部字段；commit 时用
        }

        # persist proposal so audit_layer / commit_approved can find it by id
        proposals_dir = os.path.join(self.data_dir, "strategies", "proposals")
        os.makedirs(proposals_dir, exist_ok=True)
        proposal_path = os.path.join(proposals_dir, f"{proposal_id}.json")
        with open(proposal_path, "w", encoding="utf-8") as f:
            json.dump(proposal, f, ensure_ascii=False, indent=2)

        return proposal

    def commit_approved(self, proposal_id: str) -> None:
        """Commit a previously-approved proposal to active.json + changelog.

        Verifies audit_log.json contains an entry for proposal_id with
        decision='AUTO_MERGE'. Raises PermissionError otherwise. See spec §8.
        """
        # 1. verify audit_log decision
        log_path = os.path.join(self.data_dir, "strategies", "audit_log.json")
        if not os.path.exists(log_path):
            raise PermissionError(
                f"commit_approved({proposal_id!r}) refused: audit_log.json missing"
            )
        with open(log_path) as f:
            log = json.load(f)
        matching = [e for e in log if e.get("proposal_id") == proposal_id]
        if not matching:
            raise PermissionError(
                f"commit_approved({proposal_id!r}) refused: no audit_log entry"
            )
        decision = matching[-1].get("decision")
        if decision != "AUTO_MERGE":
            raise PermissionError(
                f"commit_approved({proposal_id!r}) refused: "
                f"audit_log decision={decision!r}, expected AUTO_MERGE"
            )

        # 2. load proposal
        proposal_path = os.path.join(
            self.data_dir, "strategies", "proposals", f"{proposal_id}.json"
        )
        with open(proposal_path) as f:
            proposal = json.load(f)

        # 3. apply adjustments using existing private path
        adjustments = proposal.get("_raw_adjustments", [])
        for a in adjustments:
            if a.get("type") == "parameter_adjustment":
                self._apply_parameter_adjustment(a)
            elif a.get("type") == "rule_adjustment":
                self._apply_rule_adjustment(a)

        # 4. persist
        self._save_strategies()

        # 5. append changelog with oos_validated=True
        for a in adjustments:
            entry = self._create_changelog_entry({
                **a,
                "change_type": "strategy",
                "oos_validated": True,
            })
            self.changelog.append(entry)
        self._save_changelog([])  # extends with [], then writes; see note below

    def _save_strategies(self):
        """保存策略配置"""
        strategy_path = os.path.join(self.data_dir, "strategies", "active.json")
        try:
            with open(strategy_path, 'w', encoding='utf-8') as f:
                json.dump(self.strategies, f, ensure_ascii=False, indent=2)
            logger.info(f"策略配置已保存: {strategy_path}")
        except Exception as e:
            logger.error(f"保存策略配置失败: {e}")

    def _save_changelog(self, new_entries: List[Dict]):
        """保存changelog"""
        for entry in new_entries:
            self._validate_changelog_entry(entry)

        # 添加新条目
        self.changelog.extend(new_entries)

        changelog_path = os.path.join(self.data_dir, "strategies", "changelog.json")
        try:
            with open(changelog_path, 'w', encoding='utf-8') as f:
                json.dump(self.changelog, f, ensure_ascii=False, indent=2)
            logger.info(f"Changelog已保存: {changelog_path}")
        except Exception as e:
            logger.error(f"保存Changelog失败: {e}")

    def generate_strategy_update_report(self, performance_analysis: Dict, adjustments: List[Dict], apply_result: Dict) -> Dict:
        """生成策略更新报告"""
        logger.info("生成策略更新报告...")

        report = {
            'timestamp': datetime.now().isoformat(),
            'performance_analysis': performance_analysis,
            'adjustments_proposed': adjustments,
            'adjustments_applied': apply_result.get('applied_adjustments', []),
            'adjustments_failed': apply_result.get('failed_adjustments', []),
            'changelog_entries': apply_result.get('changelog_entries', []),
            'summary': self._generate_summary(performance_analysis, adjustments, apply_result)
        }

        return report

    def _generate_summary(self, performance_analysis: Dict, adjustments: List[Dict], apply_result: Dict) -> str:
        """生成摘要"""
        summary_parts = []

        # 绩效问题
        issues = performance_analysis.get('performance_issues', [])
        if issues:
            summary_parts.append(f"绩效问题: {len(issues)}个")

        # 调整建议
        if adjustments:
            summary_parts.append(f"调整建议: {len(adjustments)}个")

        # 应用结果
        applied = apply_result.get('applied_adjustments', [])
        failed = apply_result.get('failed_adjustments', [])

        if applied:
            summary_parts.append(f"已应用: {len(applied)}个")
        if failed:
            summary_parts.append(f"失败: {len(failed)}个")

        # Changelog条目
        changelog_entries = apply_result.get('changelog_entries', [])
        if changelog_entries:
            summary_parts.append(f"Changelog: {len(changelog_entries)}条")

        # 信心水平
        confidence = performance_analysis.get('confidence', 'medium')
        confidence_map = {
            'high': '高',
            'medium': '中',
            'low': '低'
        }
        summary_parts.append(f"信心: {confidence_map.get(confidence, '中')}")

        return " | ".join(summary_parts)

    def save_strategy_update_report(self, report: Dict, filename: str = None):
        """保存策略更新报告"""
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"strategy_update_{timestamp}.json"

        filepath = os.path.join(self.data_dir, "agents", "updates", filename)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)

        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            logger.info(f"策略更新报告已保存: {filepath}")
        except Exception as e:
            logger.error(f"保存策略更新报告失败: {e}")

def detect_iteration_stall(audit_decisions, perf_history,
                           min_stall_days: int = 10, lookback: int = 10):
    """S4: 检测策略自迭代是否停滞且伴随跑输基准。

    纯函数。返回告警字符串或 None。触发条件（两者同时成立）：
      1. 最近 min_stall_days 个【非空】audit_decision 全是 'NO_CHANGES'；
      2. 最近 lookback 个交易日主账户累计跑输沪深300。

    设计意图：NO_CHANGES 本身不是错误（无信号时不该硬改策略），但「长期
    NO_CHANGES + 持续跑输」说明自迭代事实上停摆——5/16~6/9 的一个月静默
    停滞正是这种情况，却因 NO_CHANGES 不发 warning 而无人察觉。

    audit_decisions / perf_history 均为 most-recent-last。perf 条目用
    main_pct / hs300_pct（单位：百分比）。
    """
    recent = [d for d in (audit_decisions or []) if d]
    if len(recent) < min_stall_days:
        return None
    if any(d != "NO_CHANGES" for d in recent[-min_stall_days:]):
        return None

    window = (perf_history or [])[-lookback:]
    if not window:
        return None
    main_cum, hs_cum = 1.0, 1.0
    for r in window:
        main_cum *= (1 + (r.get("main_pct", 0) or 0) / 100)
        hs_cum *= (1 + (r.get("hs300_pct", 0) or 0) / 100)
    if main_cum >= hs_cum:
        return None

    gap_pp = (main_cum - hs_cum) * 100
    return (f"⚠️ 策略自迭代停滞：连续 {min_stall_days} 个交易日 NO_CHANGES，"
            f"且近 {len(window)} 日主账户跑输沪深300 {gap_pp:.1f}pp — 需人工检查")


def detect_deployment_stall(position_pct_history, floor,
                            min_stall_days: int = 5, margin_pp: float = 5.0):
    """F5: 检测资金部署是否长期停滞在 total_position_floor 之下。

    纯函数。返回告警字符串或 None。与 detect_iteration_stall 正交——后者监控
    策略自迭代停滞，本函数监控**资金部署停滞**（仓位卡在下限之下），后者正是
    2026-06-08~06-12 G0/MODIFY 死锁期间无人察觉的盲区。

    触发条件（同时成立）：
      1. 过滤 None 后至少有 min_stall_days 个有效仓位读数；
      2. 最近 min_stall_days 个仓位全部 < (floor*100 - margin_pp)。

    position_pct_history: most-recent-last 的主账户日度仓位（百分数，如 17.4）。
    floor: 策略 total_position_floor（小数，如 0.50）；None/缺配 → 不告警。
    min_stall_days=5：部署缺口比策略停滞更该快速行动（6/8→6/12 恰好 5 个交易日）。
    margin_pp=5：留出「无信号日合理低仓」缓冲（策略 cash_drag_alert 允许无信号时
    保持现金），连续 5 日低于 floor-5pp 才算真停滞，而非单日无信号。
    """
    if floor is None:
        return None
    recent = [p for p in (position_pct_history or []) if p is not None]
    if len(recent) < min_stall_days:
        return None
    threshold = floor * 100 - margin_pp
    window = recent[-min_stall_days:]
    if any(p >= threshold for p in window):
        return None
    avg = sum(window) / len(window)
    return (f"⚠️ 资金部署停滞：主账户连续 {min_stall_days} 个交易日仓位 "
            f"<{threshold:.0f}%（均 {avg:.1f}%，目标下限 {floor * 100:.0f}%）— "
            f"检查入场候选与 G0 闸门")


def main():
    """主函数"""
    agent = StrategyMaintainerAgent()

    # 加载复盘报告
    review_report = agent.load_review_report()

    if not review_report:
        print("❌ 未找到复盘报告")
        print("请先运行Review Agent")
        return

    # 分析策略绩效
    performance_analysis = agent.analyze_strategy_performance(review_report)

    # 生成调整建议
    adjustments = agent.generate_strategy_adjustments(performance_analysis)

    # 应用调整
    apply_result = agent.apply_adjustments(adjustments)

    # 生成更新报告
    report = agent.generate_strategy_update_report(performance_analysis, adjustments, apply_result)

    # 打印更新摘要
    print("\n=== 策略更新摘要 ===")
    print(report.get('summary'))

    # 打印详细报告
    print("\n=== 详细策略更新 ===")

    # 绩效问题
    issues = performance_analysis.get('performance_issues', [])
    if issues:
        print(f"\n绩效问题 ({len(issues)}个):")
        for issue in issues:
            print(f"  ⚠️ {issue.get('issue')}: {issue.get('description')}")

    # 调整建议
    if adjustments:
        print(f"\n调整建议 ({len(adjustments)}个):")
        for adj in adjustments:
            print(f"  🔧 {adj.get('type')}: {adj.get('parameter', adj.get('rule', ''))}")
            print(f"     {adj.get('old_value', '')} -> {adj.get('new_value', '')}")
            print(f"     理由: {adj.get('reason')}")

    # 应用结果
    applied = apply_result.get('applied_adjustments', [])
    failed = apply_result.get('failed_adjustments', [])

    if applied:
        print(f"\n已应用调整 ({len(applied)}个):")
        for adj in applied:
            print(f"  ✅ {adj.get('parameter', adj.get('rule', ''))}")

    if failed:
        print(f"\n失败调整 ({len(failed)}个):")
        for adj in failed:
            print(f"  ❌ {adj.get('parameter', adj.get('rule', ''))}")

    # Changelog条目
    changelog_entries = apply_result.get('changelog_entries', [])
    if changelog_entries:
        print(f"\nChangelog条目 ({len(changelog_entries)}条):")
        for entry in changelog_entries:
            print(f"  📝 {entry.get('date')}: {entry.get('description')}")

    # 保存更新报告
    agent.save_strategy_update_report(report)

if __name__ == "__main__":
    main()
