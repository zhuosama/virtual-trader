#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
多Agent协调器
负责协调各个Agent的执行流程
"""

import json
import os

VTRADER_HOME = os.environ.get("VTRADER_HOME", os.path.expanduser("~/.hermes/virtual-trader"))
import sys
from datetime import datetime
from typing import Dict, List, Optional
import logging

# 添加当前目录到路径
sys.path.append(os.path.dirname(__file__))

# 导入各个Agent
from market_analyst import MarketAnalystAgent
from execution_planner import ExecutionPlannerAgent
from risk_controller import RiskControllerAgent
from review_agent import ReviewAgent
from strategy_maintainer import StrategyMaintainerAgent

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class MultiAgentCoordinator:
    """多Agent协调器"""
    
    def __init__(self, config_path: str = None):
        """初始化协调器"""
        self.config = self._load_config(config_path)
        self.data_dir = VTRADER_HOME
        self.agents = self._initialize_agents()
    
    # 辅助函数：数据格式化
    def _format_currency(self, amount: float) -> str:
        """格式化货币"""
        if amount >= 0:
            return f"+{amount:,.0f}元"
        else:
            return f"{amount:,.0f}元"
    
    def _format_percentage(self, value: float) -> str:
        """格式化百分比"""
        if value >= 0:
            return f"+{value:.2f}%"
        else:
            return f"{value:.2f}%"
    
    def _format_ratio(self, value: float) -> str:
        """格式化比率"""
        return f"{value:.1f}%"
    
    def _get_trend_emoji(self, value: float) -> str:
        """获取趋势emoji"""
        if value > 0:
            return "📈"
        elif value < 0:
            return "📉"
        else:
            return "➡️"
    
    def _get_priority_emoji(self, priority: str) -> str:
        """获取优先级emoji"""
        priority_map = {
            'high': '🔴',
            'medium': '🟡',
            'low': '🟢'
        }
        return priority_map.get(priority, '⚪')
        
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
    
    def _initialize_agents(self) -> Dict:
        """初始化各个Agent"""
        logger.info("初始化Agent...")
        
        agents = {
            'market_analyst': MarketAnalystAgent(),
            'execution_planner': ExecutionPlannerAgent(),
            'risk_controller': RiskControllerAgent(),
            'review_agent': ReviewAgent(),
            'strategy_maintainer': StrategyMaintainerAgent()
        }
        
        return agents
    
    def run_pre_market_workflow(self) -> Dict:
        """运行盘前工作流"""
        logger.info("开始盘前工作流...")
        
        workflow_result = {
            'timestamp': datetime.now().isoformat(),
            'workflow_type': 'pre_market',
            'steps': [],
            'final_output': None,
            'status': 'success'
        }
        
        try:
            # 步骤1: Market Analyst Agent
            logger.info("步骤1: Market Analyst Agent")
            market_analysis = self.agents['market_analyst'].run_analysis()
            
            workflow_result['steps'].append({
                'step': 1,
                'agent': 'market_analyst',
                'status': 'success',
                'output': market_analysis.get('analysis_summary', '')
            })
            
            # 步骤2: Execution Planner Agent
            logger.info("步骤2: Execution Planner Agent")
            trading_plan = self.agents['execution_planner'].generate_trading_plan(market_analysis)
            
            workflow_result['steps'].append({
                'step': 2,
                'agent': 'execution_planner',
                'status': 'success',
                'output': self.agents['execution_planner'].generate_plan_summary(trading_plan)
            })
            
            # 步骤3: Risk Controller Agent
            logger.info("步骤3: Risk Controller Agent")
            validation_result = self.agents['risk_controller'].validate_trading_plan(trading_plan)
            
            workflow_result['steps'].append({
                'step': 3,
                'agent': 'risk_controller',
                'status': 'success',
                'output': self.agents['risk_controller'].generate_validation_summary(validation_result)
            })
            
            # 最终输出
            decision = validation_result.get('decision', 'REJECTED')
            
            if decision == 'APPROVED':
                final_output = self._generate_approved_output(market_analysis, trading_plan, validation_result)
            elif decision == 'MODIFY':
                final_output = self._generate_modify_output(market_analysis, trading_plan, validation_result)
            else:
                final_output = self._generate_rejected_output(validation_result)
            
            workflow_result['final_output'] = final_output
            workflow_result['status'] = 'success'
            
            logger.info("盘前工作流完成")
            
        except Exception as e:
            logger.error(f"盘前工作流失败: {e}")
            workflow_result['status'] = 'failed'
            workflow_result['error'] = str(e)
            workflow_result['final_output'] = f"盘前分析失败: {e}"
        
        return workflow_result
    
    def _generate_approved_output(self, market_analysis: Dict, trading_plan: Dict, validation_result: Dict) -> str:
        """生成批准输出"""
        output_parts = []
        
        # 标题和时间
        output_parts.append("📊 虚拟盘盘前分析")
        output_parts.append("────────────────────────")
        output_parts.append(f"📅 日期: {datetime.now().strftime('%Y-%m-%d (%A)')}")
        output_parts.append(f"⏰ 时间: {datetime.now().strftime('%H:%M')} 北京时间")
        
        # 市场概览
        output_parts.append("")
        output_parts.append("🔍 市场概览")
        
        # 市场状态
        market_tone = market_analysis.get('market_tone', 'neutral')
        tone_map = {
            'bullish': '看涨 ⬆️',
            'bearish': '看跌 ⬇️',
            'neutral': '中性 ➡️'
        }
        output_parts.append(f"• 市场状态: {tone_map.get(market_tone, '未知')}")
        
        # 板块强度
        sector_strength = market_analysis.get('sector_strength', [])
        if sector_strength:
            top_sector = sector_strength[0]
            avg_change = top_sector.get('avg_change', 0)
            output_parts.append(f"• 最强板块: {top_sector.get('sector')} ({self._format_percentage(avg_change)})")
        
        # 风险信号
        risk_signals = market_analysis.get('risk_signals', [])
        if risk_signals:
            output_parts.append(f"• 风险信号: {len(risk_signals)}个 ⚠️")
        
        # 交易计划
        output_parts.append("")
        output_parts.append("📈 交易计划")
        
        # 市场判断
        confidence = trading_plan.get('confidence', 'medium')
        confidence_map = {
            'high': '高',
            'medium': '中',
            'low': '低'
        }
        output_parts.append(f"• 市场判断: {tone_map.get(market_tone, '未知')}")
        
        # 目标仓位
        position_sizing = trading_plan.get('position_sizing', {})
        total_position = position_sizing.get('total_position', 0)
        output_parts.append(f"• 目标仓位: {self._format_ratio(total_position * 100)}")
        
        # 信心水平
        output_parts.append(f"• 信心水平: {confidence_map.get(confidence, '中')}")
        
        # 今日操作
        actions = trading_plan.get('actions', [])
        if actions:
            output_parts.append("")
            output_parts.append("🎯 今日操作")
            for i, action in enumerate(actions[:3], 1):  # 只显示前3个
                account = action.get('account', '')
                action_type = action.get('action', '')
                name = action.get('name', '')
                code = action.get('code', '')
                priority = action.get('priority', 'medium')
                reason = action.get('reason', '')
                
                priority_emoji = self._get_priority_emoji(priority)
                output_parts.append(f"{i}. {priority_emoji} {account}账户: {action_type} {name}")
                output_parts.append(f"   理由: {reason}")
        
        # 风险审查
        output_parts.append("")
        decision = validation_result.get('decision', 'APPROVED')
        decision_map = {
            'APPROVED': '通过 ✅',
            'MODIFY': '需要修改 ⚠️',
            'REJECTED': '拒绝 ❌'
        }
        output_parts.append(f"⚠️ 风险审查: {decision_map.get(decision, '未知')}")
        
        warnings = validation_result.get('warnings', [])
        if warnings:
            for warning in warnings[:2]:  # 只显示前2个
                output_parts.append(f"• {warning}")
        
        # 今日目标
        output_parts.append("")
        if decision == 'APPROVED':
            output_parts.append("🎯 今日目标: 执行交易计划，监控风险")
        elif decision == 'MODIFY':
            output_parts.append("🎯 今日目标: 修改交易计划后重新审查")
        else:
            output_parts.append("🎯 今日目标: 无交易，观察市场")
        
        output_parts.append("────────────────────────")
        
        return "\n".join(output_parts)
    
    def _generate_modify_output(self, market_analysis: Dict, trading_plan: Dict, validation_result: Dict) -> str:
        """生成修改输出"""
        output_parts = []
        
        # 标题和时间
        output_parts.append("📊 虚拟盘盘前分析")
        output_parts.append("────────────────────────")
        output_parts.append(f"📅 日期: {datetime.now().strftime('%Y-%m-%d (%A)')}")
        output_parts.append(f"⏰ 时间: {datetime.now().strftime('%H:%M')} 北京时间")
        
        # 市场概览
        output_parts.append("")
        output_parts.append("🔍 市场概览")
        
        # 市场状态
        market_tone = market_analysis.get('market_tone', 'neutral')
        tone_map = {
            'bullish': '看涨 ⬆️',
            'bearish': '看跌 ⬇️',
            'neutral': '中性 ➡️'
        }
        output_parts.append(f"• 市场状态: {tone_map.get(market_tone, '未知')}")
        
        # 板块强度
        sector_strength = market_analysis.get('sector_strength', [])
        if sector_strength:
            top_sector = sector_strength[0]
            avg_change = top_sector.get('avg_change', 0)
            output_parts.append(f"• 最强板块: {top_sector.get('sector')} ({self._format_percentage(avg_change)})")
        
        # 风险信号
        risk_signals = market_analysis.get('risk_signals', [])
        if risk_signals:
            output_parts.append(f"• 风险信号: {len(risk_signals)}个 ⚠️")
        
        # 风险审查
        output_parts.append("")
        output_parts.append("⚠️ 风险审查: 需要修改 ⚠️")
        
        warnings = validation_result.get('warnings', [])
        if warnings:
            for warning in warnings:
                output_parts.append(f"• {warning}")
        
        # 修改建议
        modifications = validation_result.get('modifications', [])
        if modifications:
            output_parts.append("")
            output_parts.append("🔧 修改建议")
            
            for mod in modifications:
                action = mod.get('action', '')
                code = mod.get('code', '')
                output_parts.append(f"• {action}: {code}")
        
        # 今日目标
        output_parts.append("")
        output_parts.append("🎯 今日目标: 修改交易计划后重新审查")
        output_parts.append("────────────────────────")
        
        return "\n".join(output_parts)
    
    def _generate_rejected_output(self, validation_result: Dict) -> str:
        """生成拒绝输出"""
        output_parts = []
        
        # 标题和时间
        output_parts.append("📊 虚拟盘盘前分析")
        output_parts.append("────────────────────────")
        output_parts.append(f"📅 日期: {datetime.now().strftime('%Y-%m-%d (%A)')}")
        output_parts.append(f"⏰ 时间: {datetime.now().strftime('%H:%M')} 北京时间")
        
        # 风险审查
        output_parts.append("")
        output_parts.append("⚠️ 风险审查: 拒绝 ❌")
        
        warnings = validation_result.get('warnings', [])
        if warnings:
            for warning in warnings:
                output_parts.append(f"• {warning}")
        
        # 今日目标
        output_parts.append("")
        output_parts.append("🎯 今日目标: 无交易，观察市场")
        output_parts.append("────────────────────────")
        
        return "\n".join(output_parts)
    
    def run_post_market_workflow(self) -> Dict:
        """运行盘后工作流"""
        logger.info("开始盘后工作流...")
        
        workflow_result = {
            'timestamp': datetime.now().isoformat(),
            'workflow_type': 'post_market',
            'steps': [],
            'final_output': None,
            'status': 'success'
        }
        
        try:
            # 步骤1: Review Agent
            logger.info("步骤1: Review Agent")
            daily_data = self.agents['review_agent'].load_daily_data()
            review_report = self.agents['review_agent'].generate_review_report(daily_data)
            
            workflow_result['steps'].append({
                'step': 1,
                'agent': 'review_agent',
                'status': 'success',
                'output': self.agents['review_agent'].generate_review_summary(review_report)
            })
            
            # 步骤2: Strategy Maintainer Agent
            logger.info("步骤2: Strategy Maintainer Agent")
            performance_analysis = self.agents['strategy_maintainer'].analyze_strategy_performance(review_report)
            adjustments = self.agents['strategy_maintainer'].generate_strategy_adjustments(performance_analysis)
            apply_result = self.agents['strategy_maintainer'].apply_adjustments(adjustments)
            update_report = self.agents['strategy_maintainer'].generate_strategy_update_report(performance_analysis, adjustments, apply_result)
            
            workflow_result['steps'].append({
                'step': 2,
                'agent': 'strategy_maintainer',
                'status': 'success',
                'output': update_report.get('summary', '')
            })
            
            # 最终输出
            final_output = self._generate_post_market_output(review_report, update_report)
            workflow_result['final_output'] = final_output
            workflow_result['status'] = 'success'
            
            logger.info("盘后工作流完成")
            
        except Exception as e:
            logger.error(f"盘后工作流失败: {e}")
            workflow_result['status'] = 'failed'
            workflow_result['error'] = str(e)
            workflow_result['final_output'] = f"盘后复盘失败: {e}"
        
        return workflow_result
    
    def _generate_post_market_output(self, review_report: Dict, update_report: Dict) -> str:
        """生成盘后输出"""
        output_parts = []
        
        # 标题和时间
        output_parts.append("📊 虚拟盘盘后复盘")
        output_parts.append("────────────────────────")
        output_parts.append(f"📅 日期: {datetime.now().strftime('%Y-%m-%d (%A)')}")
        output_parts.append(f"⏰ 时间: {datetime.now().strftime('%H:%M')} 北京时间")
        
        # 今日绩效
        output_parts.append("")
        output_parts.append("💰 今日绩效")
        
        performance = review_report.get('performance', {})
        daily_return = performance.get('daily_return', {})
        
        total_pnl = 0
        for account_type, data in daily_return.items():
            pnl = data.get('pnl', 0)
            total_pnl += pnl
            emoji = self._get_trend_emoji(pnl)
            pnl_str = self._format_currency(pnl)
            output_parts.append(f"• {account_type}账户: {pnl_str} {emoji}")
        
        # 合计
        if len(daily_return) > 1:
            emoji = self._get_trend_emoji(total_pnl)
            total_str = self._format_currency(total_pnl)
            output_parts.append(f"• 合计: {total_str} {emoji}")
        
        # 持仓状况
        output_parts.append("")
        output_parts.append("📊 持仓状况")
        
        # 从账户数据获取持仓信息
        accounts = review_report.get('accounts', {})
        total_value = 0
        total_positions = 0
        
        for account_type, account in accounts.items():
            total_value += account.get('total_value', 0)
            positions = account.get('positions', [])
            total_positions += len(positions)
        
        output_parts.append(f"• 总资产: {total_value:,.0f}元")
        output_parts.append(f"• 持仓数量: {total_positions}只")
        
        # 持仓详情
        if accounts:
            output_parts.append("")
            output_parts.append("📋 持仓详情")
            
            for account_type, account in accounts.items():
                output_parts.append(f"\n【{account_type}账户】")
                output_parts.append(f"  总资产: {account.get('total_value', 0):,.0f}元 | 现金: {account.get('cash', 0):,.0f}元")
                
                positions = account.get('positions', [])
                if positions:
                    # 按盈亏排序
                    sorted_positions = sorted(positions, key=lambda x: x.get('unrealized_pnl', 0), reverse=True)
                    
                    # 显示前5个持仓
                    for i, pos in enumerate(sorted_positions[:5], 1):
                        name = pos.get('name', '')
                        code = pos.get('code', '')
                        shares = pos.get('shares', 0)
                        avg_cost = pos.get('avg_cost', 0)
                        current_price = pos.get('current_price', 0)
                        market_value = pos.get('market_value', 0)
                        unrealized_pnl = pos.get('unrealized_pnl', 0)
                        unrealized_pnl_pct = pos.get('unrealized_pnl_pct', 0)
                        
                        # 计算仓位占比
                        position_pct = (market_value / account.get('total_value', 1)) * 100
                        
                        # 盈亏emoji
                        pnl_emoji = self._get_trend_emoji(unrealized_pnl)
                        
                        output_parts.append(f"  {i}. {name} ({code})")
                        output_parts.append(f"     数量: {shares}股 | 成本: {avg_cost:.2f}元 | 现价: {current_price:.2f}元")
                        output_parts.append(f"     市值: {market_value:,.0f}元 ({position_pct:.1f}%) | 盈亏: {self._format_currency(unrealized_pnl)} ({self._format_percentage(unrealized_pnl_pct)}) {pnl_emoji}")
                    
                    # 如果持仓超过5个，显示提示
                    if len(positions) > 5:
                        output_parts.append(f"  ... (共{len(positions)}只持仓，显示前5只)")
                else:
                    output_parts.append("  (空仓)")
        
        # 今日交易
        output_parts.append("")
        output_parts.append("🔄 今日交易")
        
        trade_analysis = review_report.get('trade_analysis', {})
        total_trades = trade_analysis.get('total_trades', 0)
        buy_trades = trade_analysis.get('buy_trades', 0)
        sell_trades = trade_analysis.get('sell_trades', 0)
        
        if total_trades > 0:
            output_parts.append(f"• 交易笔数: {total_trades}笔")
            output_parts.append(f"• 买入: {buy_trades}笔")
            output_parts.append(f"• 卖出: {sell_trades}笔")
            
            # 显示交易详情
            trade_details = trade_analysis.get('trade_details', [])
            if trade_details:
                output_parts.append("")
                output_parts.append("📝 交易详情")
                
                for i, trade in enumerate(trade_details[:3], 1):  # 只显示前3笔
                    account = trade.get('account', '')
                    action = trade.get('action', '')
                    name = trade.get('name', '')
                    code = trade.get('code', '')
                    price = trade.get('price', 0)
                    shares = trade.get('shares', 0)
                    amount = trade.get('amount', 0)
                    signal = trade.get('signal', '')
                    
                    action_map = {
                        'buy': '买入',
                        'sell': '卖出'
                    }
                    
                    output_parts.append(f"  {i}. {account}账户 {action_map.get(action, action)} {name} ({code})")
                    output_parts.append(f"     价格: {price:.2f}元 | 数量: {shares}股 | 金额: {amount:,.0f}元")
                    if signal:
                        output_parts.append(f"     信号: {signal}")
                
                if len(trade_details) > 3:
                    output_parts.append(f"  ... (共{len(trade_details)}笔交易，显示前3笔)")
        else:
            output_parts.append("• 今日无交易")
        
        # 错误识别
        mistakes = review_report.get('mistakes', [])
        if mistakes:
            output_parts.append("")
            output_parts.append(f"⚠️ 错误识别 ({len(mistakes)}个)")
            
            for mistake in mistakes[:3]:  # 只显示前3个
                mistake_type = mistake.get('type', '')
                description = mistake.get('description', '')
                impact = mistake.get('impact', '')
                prevention = mistake.get('prevention', '')
                
                impact_map = {
                    'high': '高',
                    'medium': '中',
                    'low': '低'
                }
                
                output_parts.append(f"❌ {description}")
                output_parts.append(f"   影响: {impact_map.get(impact, '中')}")
                if prevention:
                    output_parts.append(f"   建议: {prevention}")
        
        # 有效做法
        lessons = review_report.get('lessons', {})
        what_worked = lessons.get('what_worked', [])
        
        if what_worked:
            output_parts.append("")
            output_parts.append(f"✅ 有效做法 ({len(what_worked)}个)")
            
            for item in what_worked[:3]:  # 只显示前3个
                output_parts.append(f"• {item}")
        
        # 策略更新
        adjustments_applied = update_report.get('adjustments_applied', [])
        if adjustments_applied:
            output_parts.append("")
            output_parts.append("🔧 策略更新")
            
            for adj in adjustments_applied:
                parameter = adj.get('parameter', adj.get('rule', ''))
                old_value = adj.get('old_value', '')
                new_value = adj.get('new_value', '')
                reason = adj.get('reason', '')
                
                # 格式化参数名称
                param_map = {
                    'max_single_position': '单笔最大仓位',
                    'stop_loss_pct': '止损比例',
                    'take_profit_pct': '止盈比例'
                }
                param_name = param_map.get(parameter, parameter)
                
                output_parts.append(f"• {param_name}: {old_value} → {new_value}")
                output_parts.append(f"  原因: {reason}")
        
        # Changelog
        changelog_entries = update_report.get('changelog_entries', [])
        if changelog_entries:
            output_parts.append("")
            output_parts.append(f"📝 Changelog: {len(changelog_entries)}条记录已保存")
        
        output_parts.append("────────────────────────")
        
        return "\n".join(output_parts)
    
    def run_weekly_review_workflow(self) -> Dict:
        """运行周末复盘工作流"""
        logger.info("开始周末复盘工作流...")
        
        # 周末复盘是盘后复盘的扩展版
        # 可以复用盘后工作流，但增加更多分析
        
        workflow_result = self.run_post_market_workflow()
        workflow_result['workflow_type'] = 'weekly_review'
        
        # 添加周末特有的分析
        workflow_result['final_output'] = "📊 周末深度复盘\n" + workflow_result.get('final_output', '')
        
        return workflow_result
    
    def save_workflow_result(self, result: Dict, filename: str = None):
        """保存工作流结果"""
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            workflow_type = result.get('workflow_type', 'unknown')
            filename = f"workflow_{workflow_type}_{timestamp}.json"
        
        filepath = os.path.join(self.data_dir, "agents", "workflows", filename)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            logger.info(f"工作流结果已保存: {filepath}")
        except Exception as e:
            logger.error(f"保存工作流结果失败: {e}")

def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description='多Agent协调器')
    parser.add_argument('--workflow', type=str, required=True, 
                       choices=['pre_market', 'post_market', 'weekly_review'],
                       help='工作流类型')
    
    args = parser.parse_args()
    
    # 创建协调器
    coordinator = MultiAgentCoordinator()
    
    # 运行工作流
    if args.workflow == 'pre_market':
        result = coordinator.run_pre_market_workflow()
    elif args.workflow == 'post_market':
        result = coordinator.run_post_market_workflow()
    elif args.workflow == 'weekly_review':
        result = coordinator.run_weekly_review_workflow()
    else:
        print(f"未知工作流类型: {args.workflow}")
        return
    
    # 打印结果
    print("\n" + "="*50)
    print(result.get('final_output', '无输出'))
    print("="*50)
    
    # 保存结果
    coordinator.save_workflow_result(result)

if __name__ == "__main__":
    main()
