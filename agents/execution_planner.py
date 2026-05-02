#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Execution Planner Agent
交易计划专家 - 基于市场分析生成可执行交易计划
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

class ExecutionPlannerAgent:
    """交易计划专家Agent"""
    
    def __init__(self, config_path: str = None):
        """初始化Agent"""
        self.config = self._load_config(config_path)
        self.data_dir = VTRADER_HOME
        self.strategies = self._load_strategies()
        self.accounts = self._load_accounts()
        
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
    
    def _load_accounts(self) -> Dict:
        """加载账户数据"""
        accounts = {}
        for account_type in ['main', 'lab']:
            account_path = os.path.join(self.data_dir, "accounts", f"{account_type}.json")
            try:
                with open(account_path, 'r', encoding='utf-8') as f:
                    accounts[account_type] = json.load(f)
            except Exception as e:
                logger.error(f"加载账户失败 {account_type}: {e}")
        
        return accounts
    
    def load_market_analysis(self, analysis_path: str = None) -> Dict:
        """加载市场分析报告"""
        if analysis_path is None:
            # 查找最新的市场分析报告
            reports_dir = os.path.join(self.data_dir, "agents", "reports")
            if os.path.exists(reports_dir):
                files = [f for f in os.listdir(reports_dir) if f.startswith("market_analysis_")]
                if files:
                    latest_file = sorted(files)[-1]
                    analysis_path = os.path.join(reports_dir, latest_file)
        
        if analysis_path and os.path.exists(analysis_path):
            try:
                with open(analysis_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"加载市场分析失败: {e}")
        
        return {}
    
    def generate_trading_plan(self, market_analysis: Dict) -> Dict:
        """生成交易计划"""
        logger.info("生成交易计划...")
        
        # 获取市场状态
        market_tone = market_analysis.get('market_tone', 'neutral')
        sector_strength = market_analysis.get('sector_strength', [])
        risk_signals = market_analysis.get('risk_signals', [])
        
        # 根据市场状态生成计划
        if market_tone == 'bullish':
            plan = self._generate_bullish_plan(sector_strength, risk_signals)
        elif market_tone == 'bearish':
            plan = self._generate_bearish_plan(sector_strength, risk_signals)
        else:
            plan = self._generate_neutral_plan(sector_strength, risk_signals)
        
        # 添加风险信号
        plan['risk_signals'] = risk_signals
        
        return plan
    
    def _generate_bullish_plan(self, sector_strength: List[Dict], risk_signals: List[str]) -> Dict:
        """生成看涨计划"""
        logger.info("生成看涨市场计划...")
        
        plan = {
            'market_regime': 'bullish',
            'actions': [],
            'position_sizing': {
                'total_position': 0.70,  # 看涨时提高仓位
                'sector_allocation': {}
            },
            'grid_setup': None,
            'confidence': 'high' if not risk_signals else 'medium'
        }
        
        # 主账户策略
        main_strategy = self.strategies.get('main_strategy', {})
        if main_strategy:
            plan['actions'].extend(self._generate_main_account_actions(main_strategy, sector_strength))
        
        # 实验账户策略
        lab_strategy = self.strategies.get('lab_strategy', {})
        if lab_strategy:
            plan['actions'].extend(self._generate_lab_account_actions(lab_strategy, sector_strength))
        
        return plan
    
    def _generate_bearish_plan(self, sector_strength: List[Dict], risk_signals: List[str]) -> Dict:
        """生成看跌计划"""
        logger.info("生成看跌市场计划...")
        
        plan = {
            'market_regime': 'bearish',
            'actions': [],
            'position_sizing': {
                'total_position': 0.40,  # 看跌时降低仓位
                'sector_allocation': {}
            },
            'grid_setup': None,
            'confidence': 'high' if not risk_signals else 'medium'
        }
        
        # 看跌时主要是减仓和止损
        plan['actions'].append({
            'account': 'main',
            'code': 'ALL',
            'name': '所有持仓',
            'action': 'reduce_position',
            'reason': '市场看跌，降低仓位',
            'priority': 'high'
        })
        
        plan['actions'].append({
            'account': 'lab',
            'code': 'ALL',
            'name': '所有持仓',
            'action': 'reduce_position',
            'reason': '市场看跌，降低仓位',
            'priority': 'high'
        })
        
        return plan
    
    def _generate_neutral_plan(self, sector_strength: List[Dict], risk_signals: List[str]) -> Dict:
        """生成中性计划"""
        logger.info("生成中性市场计划...")
        
        plan = {
            'market_regime': 'neutral',
            'actions': [],
            'position_sizing': {
                'total_position': 0.55,  # 中性时适中仓位
                'sector_allocation': {}
            },
            'grid_setup': None,
            'confidence': 'medium'
        }
        
        # 中性市场主要是持有和微调
        plan['actions'].append({
            'account': 'both',
            'code': 'ALL',
            'name': '所有持仓',
            'action': 'hold',
            'reason': '市场中性，持有现有仓位',
            'priority': 'medium'
        })
        
        return plan
    
    def _generate_main_account_actions(self, strategy: Dict, sector_strength: List[Dict]) -> List[Dict]:
        """生成主账户交易动作"""
        actions = []
        
        # 检查是否有需要建仓的标的
        watchlist_path = os.path.join(self.data_dir, "market-data", "watchlist.json")
        if os.path.exists(watchlist_path):
            try:
                with open(watchlist_path, 'r', encoding='utf-8') as f:
                    watchlist = json.load(f)
                
                # 查找符合条件的标的
                for stock in watchlist.get('stocks', []):
                    if stock.get('tag') == 'main':  # 主账户标的
                        # 检查是否符合建仓条件
                        if self._check_entry_conditions(stock, strategy, sector_strength):
                            actions.append({
                                'account': 'main',
                                'code': stock['code'],
                                'name': stock['name'],
                                'action': 'buy',
                                'reason': f"符合{strategy.get('name', '主策略')}入场条件",
                                'priority': 'medium',
                                'position_size': strategy.get('parameters', {}).get('initial_position', 0.08)
                            })
            except Exception as e:
                logger.error(f"加载关注池失败: {e}")
        
        return actions
    
    def _generate_lab_account_actions(self, strategy: Dict, sector_strength: List[Dict]) -> List[Dict]:
        """生成实验账户交易动作"""
        actions = []
        
        # 检查是否有需要建仓的标的
        watchlist_path = os.path.join(self.data_dir, "market-data", "watchlist.json")
        if os.path.exists(watchlist_path):
            try:
                with open(watchlist_path, 'r', encoding='utf-8') as f:
                    watchlist = json.load(f)
                
                # 查找符合条件的标的
                for stock in watchlist.get('stocks', []):
                    if stock.get('tag') == 'lab':  # 实验账户标的
                        # 检查是否符合建仓条件
                        if self._check_entry_conditions(stock, strategy, sector_strength):
                            actions.append({
                                'account': 'lab',
                                'code': stock['code'],
                                'name': stock['name'],
                                'action': 'buy',
                                'reason': f"符合{strategy.get('name', '实验策略')}入场条件",
                                'priority': 'medium',
                                'position_size': strategy.get('parameters', {}).get('initial_position', 0.15)
                            })
            except Exception as e:
                logger.error(f"加载关注池失败: {e}")
        
        return actions
    
    def _check_entry_conditions(self, stock: Dict, strategy: Dict, sector_strength: List[Dict]) -> bool:
        """检查入场条件"""
        # 这里应该实现真正的入场条件检查
        # 目前先返回True，表示符合入场条件
        # 实际应该检查：
        # 1. 趋势条件（MA5>MA20等）
        # 2. 板块强度
        # 3. 基本面条件
        # 4. 估值条件
        
        return True
    
    def save_trading_plan(self, plan: Dict, filename: str = None):
        """保存交易计划"""
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"trading_plan_{timestamp}.json"
        
        filepath = os.path.join(self.data_dir, "agents", "plans", filename)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(plan, f, ensure_ascii=False, indent=2)
            logger.info(f"交易计划已保存: {filepath}")
        except Exception as e:
            logger.error(f"保存交易计划失败: {e}")
    
    def generate_plan_summary(self, plan: Dict) -> str:
        """生成交易计划摘要"""
        summary_parts = []
        
        # 市场状态
        regime_map = {
            'bullish': '看涨',
            'bearish': '看跌',
            'neutral': '中性'
        }
        summary_parts.append(f"市场状态: {regime_map.get(plan.get('market_regime'), '未知')}")
        
        # 交易动作
        actions = plan.get('actions', [])
        if actions:
            summary_parts.append(f"交易动作: {len(actions)}个")
            
            # 统计买卖动作
            buy_actions = [a for a in actions if a.get('action') == 'buy']
            sell_actions = [a for a in actions if a.get('action') == 'sell']
            
            if buy_actions:
                summary_parts.append(f"买入: {len(buy_actions)}个")
            if sell_actions:
                summary_parts.append(f"卖出: {len(sell_actions)}个")
        
        # 风险信号
        risk_signals = plan.get('risk_signals', [])
        if risk_signals:
            summary_parts.append(f"风险信号: {len(risk_signals)}个")
        
        # 信心水平
        confidence = plan.get('confidence', 'medium')
        confidence_map = {
            'high': '高',
            'medium': '中',
            'low': '低'
        }
        summary_parts.append(f"信心水平: {confidence_map.get(confidence, '中')}")
        
        return " | ".join(summary_parts)

def main():
    """主函数"""
    # 加载最新的市场分析报告
    agent = ExecutionPlannerAgent()
    market_analysis = agent.load_market_analysis()
    
    if not market_analysis:
        print("❌ 未找到市场分析报告")
        print("请先运行Market Analyst Agent")
        return
    
    # 生成交易计划
    plan = agent.generate_trading_plan(market_analysis)
    
    # 打印计划摘要
    print("\n=== 交易计划摘要 ===")
    print(agent.generate_plan_summary(plan))
    
    # 打印详细计划
    print("\n=== 详细交易计划 ===")
    print(f"市场状态: {plan.get('market_regime')}")
    print(f"总仓位目标: {plan.get('position_sizing', {}).get('total_position', 0) * 100:.0f}%")
    print(f"信心水平: {plan.get('confidence')}")
    
    actions = plan.get('actions', [])
    if actions:
        print(f"\n交易动作 ({len(actions)}个):")
        for i, action in enumerate(actions):
            print(f"  {i+1}. {action.get('account')}账户: {action.get('action')} {action.get('name', '')} ({action.get('code', '')})")
            print(f"     理由: {action.get('reason')}")
    
    risk_signals = plan.get('risk_signals', [])
    if risk_signals:
        print(f"\n风险信号 ({len(risk_signals)}个):")
        for signal in risk_signals:
            print(f"  ⚠️ {signal}")
    
    # 保存交易计划
    agent.save_trading_plan(plan)

if __name__ == "__main__":
    main()
