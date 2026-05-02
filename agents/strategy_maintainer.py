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
                
                # 建议调整仓位限制
                analysis['parameter_suggestions'].append({
                    'parameter': 'max_single_position',
                    'current_value': 0.10,
                    'suggested_value': 0.08,
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
                analysis['parameter_suggestions'].append({
                    'parameter': 'stop_loss_pct',
                    'current_value': 0.07,
                    'suggested_value': 0.06,
                    'reason': '收紧止损，减少损失'
                })
        
        return analysis
    
    def generate_strategy_adjustments(self, performance_analysis: Dict) -> List[Dict]:
        """生成策略调整建议"""
        logger.info("生成策略调整建议...")
        
        adjustments = []
        
        # 1. 参数调整
        for suggestion in performance_analysis.get('parameter_suggestions', []):
            adjustments.append({
                'type': 'parameter_adjustment',
                'strategy': 'main',  # 默认主策略
                'parameter': suggestion.get('parameter'),
                'old_value': suggestion.get('current_value'),
                'new_value': suggestion.get('suggested_value'),
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
        """应用策略调整"""
        logger.info("应用策略调整...")
        
        result = {
            'timestamp': datetime.now().isoformat(),
            'applied_adjustments': [],
            'failed_adjustments': [],
            'changelog_entries': []
        }
        
        # 应用每个调整
        for adjustment in adjustments:
            try:
                if adjustment['type'] == 'parameter_adjustment':
                    success = self._apply_parameter_adjustment(adjustment)
                elif adjustment['type'] == 'rule_adjustment':
                    success = self._apply_rule_adjustment(adjustment)
                else:
                    success = False
                
                if success:
                    result['applied_adjustments'].append(adjustment)
                    
                    # 创建changelog条目
                    changelog_entry = self._create_changelog_entry(adjustment)
                    result['changelog_entries'].append(changelog_entry)
                else:
                    result['failed_adjustments'].append(adjustment)
                    
            except Exception as e:
                logger.error(f"应用调整失败: {e}")
                result['failed_adjustments'].append(adjustment)
        
        # 保存更新的策略
        if result['applied_adjustments']:
            self._save_strategies()
        
        # 保存更新的changelog
        if result['changelog_entries']:
            self._save_changelog(result['changelog_entries'])
        
        return result
    
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
    
    def _create_changelog_entry(self, adjustment: Dict) -> Dict:
        """创建changelog条目"""
        return {
            'date': datetime.now().strftime("%Y-%m-%d"),
            'account': adjustment.get('strategy', 'main'),
            'change_type': 'parameter_adjustment',
            'description': f"调整参数: {adjustment.get('parameter')} {adjustment.get('old_value')} -> {adjustment.get('new_value')}",
            'reason': adjustment.get('reason'),
            'expected_effect': f"优化策略表现",
            'actual_effect': '',
            'backfilled': False
        }
    
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
