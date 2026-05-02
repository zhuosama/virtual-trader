#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Risk Controller Agent
风控专家 - 验证交易计划，检查风险敞口
"""

import json
import os

VTRADER_HOME = os.environ.get("VTRADER_HOME", os.path.expanduser("~/.hermes/virtual-trader"))
from datetime import datetime
from typing import Dict, List, Optional, Tuple
import logging

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class RiskControllerAgent:
    """风控专家Agent"""
    
    def __init__(self, config_path: str = None):
        """初始化Agent"""
        self.config = self._load_config(config_path)
        self.data_dir = VTRADER_HOME
        self.risk_rules = self._load_risk_rules()
        
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
    
    def _load_risk_rules(self) -> Dict:
        """加载风险规则"""
        # 风险规则配置
        return {
            'position_limits': {
                'main': {
                    'single_stock': 0.10,  # 单只股票最大仓位10%
                    'single_sector': 0.30,  # 单板块最大仓位30%
                    'total_position': 0.80,  # 总仓位最大80%
                    'cash_minimum': 0.20     # 最低现金比例20%
                },
                'lab': {
                    'single_stock': 0.20,  # 单只股票最大仓位20%
                    'single_sector': 0.40,  # 单板块最大仓位40%
                    'total_position': 0.90,  # 总仓位最大90%
                    'cash_minimum': 0.10     # 最低现金比例10%
                }
            },
            'stop_loss_rules': {
                'main': {
                    'default': 0.07,  # 默认止损7%
                    'high_volatility': 0.08,  # 高波动止损8%
                    'time_stop': 20  # 时间止损20天
                },
                'lab': {
                    'default': 0.055,  # 默认止损5.5%
                    'high_volatility': 0.06,  # 高波动止损6%
                    'time_stop': 5  # 时间止损5天
                }
            },
            'concentration_limits': {
                'top3_holdings': 0.50,  # 前3大持仓占比不超过50%
                'correlation_threshold': 0.7  # 持仓相关性阈值
            }
        }
    
    def load_trading_plan(self, plan_path: str = None) -> Dict:
        """加载交易计划"""
        if plan_path is None:
            # 查找最新的交易计划
            plans_dir = os.path.join(self.data_dir, "agents", "plans")
            if os.path.exists(plans_dir):
                files = [f for f in os.listdir(plans_dir) if f.startswith("trading_plan_")]
                if files:
                    latest_file = sorted(files)[-1]
                    plan_path = os.path.join(plans_dir, latest_file)
        
        if plan_path and os.path.exists(plan_path):
            try:
                with open(plan_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"加载交易计划失败: {e}")
        
        return {}
    
    def validate_trading_plan(self, trading_plan: Dict) -> Dict:
        """验证交易计划"""
        logger.info("验证交易计划...")
        
        validation_result = {
            'timestamp': datetime.now().isoformat(),
            'decision': 'APPROVED',
            'warnings': [],
            'modifications': [],
            'reason': '',
            'checks_performed': []
        }
        
        # 1. 检查仓位限制
        position_check = self._check_position_limits(trading_plan)
        validation_result['checks_performed'].append('position_limits')
        
        if not position_check['passed']:
            validation_result['decision'] = 'REJECTED'
            validation_result['warnings'].extend(position_check['warnings'])
            validation_result['reason'] = '仓位限制检查失败'
        
        # 2. 检查止损规则
        stop_loss_check = self._check_stop_loss_rules(trading_plan)
        validation_result['checks_performed'].append('stop_loss_rules')
        
        if not stop_loss_check['passed']:
            if validation_result['decision'] != 'REJECTED':
                validation_result['decision'] = 'MODIFY'
            validation_result['warnings'].extend(stop_loss_check['warnings'])
            validation_result['modifications'].extend(stop_loss_check['modifications'])
        
        # 3. 检查集中度限制
        concentration_check = self._check_concentration_limits(trading_plan)
        validation_result['checks_performed'].append('concentration_limits')
        
        if not concentration_check['passed']:
            if validation_result['decision'] != 'REJECTED':
                validation_result['decision'] = 'MODIFY'
            validation_result['warnings'].extend(concentration_check['warnings'])
            validation_result['modifications'].extend(concentration_check['modifications'])
        
        # 4. 检查交易有效性
        trade_check = self._check_trade_validity(trading_plan)
        validation_result['checks_performed'].append('trade_validity')
        
        if not trade_check['passed']:
            validation_result['decision'] = 'REJECTED'
            validation_result['warnings'].extend(trade_check['warnings'])
            validation_result['reason'] = '交易有效性检查失败'
        
        # 5. 生成最终决策理由
        if validation_result['decision'] == 'APPROVED':
            validation_result['reason'] = '所有风险检查通过'
        elif validation_result['decision'] == 'MODIFY':
            validation_result['reason'] = '需要修改后重新审查'
        else:
            validation_result['reason'] = '风险检查未通过'
        
        logger.info(f"交易计划验证完成: {validation_result['decision']}")
        return validation_result
    
    def _check_position_limits(self, trading_plan: Dict) -> Dict:
        """检查仓位限制"""
        logger.info("检查仓位限制...")
        
        result = {
            'passed': True,
            'warnings': [],
            'modifications': []
        }
        
        # 获取当前账户状态
        accounts = self._load_accounts()
        
        # 检查每个账户
        for account_type in ['main', 'lab']:
            if account_type not in accounts:
                continue
            
            account = accounts[account_type]
            rules = self.risk_rules['position_limits'][account_type]
            
            # 检查总仓位
            total_position = account.get('position_pct', 0) / 100
            if total_position > rules['total_position']:
                result['passed'] = False
                result['warnings'].append(
                    f"{account_type}账户总仓位{total_position:.1%}超过限制{rules['total_position']:.1%}"
                )
            
            # 检查现金比例
            cash_ratio = account.get('cash', 0) / account.get('total_value', 1)
            if cash_ratio < rules['cash_minimum']:
                result['passed'] = False
                result['warnings'].append(
                    f"{account_type}账户现金比例{cash_ratio:.1%}低于最低要求{rules['cash_minimum']:.1%}"
                )
        
        return result
    
    def _check_stop_loss_rules(self, trading_plan: Dict) -> Dict:
        """检查止损规则"""
        logger.info("检查止损规则...")
        
        result = {
            'passed': True,
            'warnings': [],
            'modifications': []
        }
        
        # 检查交易计划中的止损设置
        actions = trading_plan.get('actions', [])
        for action in actions:
            if action.get('action') == 'buy':
                # 买入操作必须设置止损
                if 'stop_loss' not in action:
                    result['passed'] = False
                    result['warnings'].append(
                        f"{action.get('name', '')}买入操作未设置止损"
                    )
                    result['modifications'].append({
                        'action': 'add_stop_loss',
                        'code': action.get('code'),
                        'suggested_stop_loss': self.risk_rules['stop_loss_rules']['main']['default']
                    })
        
        return result
    
    def _check_concentration_limits(self, trading_plan: Dict) -> Dict:
        """检查集中度限制"""
        logger.info("检查集中度限制...")
        
        result = {
            'passed': True,
            'warnings': [],
            'modifications': []
        }
        
        # 获取当前持仓
        accounts = self._load_accounts()
        
        for account_type in ['main', 'lab']:
            if account_type not in accounts:
                continue
            
            account = accounts[account_type]
            positions = account.get('positions', [])
            
            if not positions:
                continue
            
            # 计算前3大持仓占比
            market_values = [p.get('market_value', 0) for p in positions]
            total_value = sum(market_values)
            
            if total_value > 0:
                sorted_values = sorted(market_values, reverse=True)
                top3_value = sum(sorted_values[:3])
                top3_ratio = top3_value / total_value
                
                limit = self.risk_rules['concentration_limits']['top3_holdings']
                if top3_ratio > limit:
                    result['passed'] = False
                    result['warnings'].append(
                        f"{account_type}账户前3大持仓占比{top3_ratio:.1%}超过限制{limit:.1%}"
                    )
        
        return result
    
    def _check_trade_validity(self, trading_plan: Dict) -> Dict:
        """检查交易有效性"""
        logger.info("检查交易有效性...")
        
        result = {
            'passed': True,
            'warnings': []
        }
        
        actions = trading_plan.get('actions', [])
        for action in actions:
            # 检查必要字段
            required_fields = ['account', 'action', 'code']
            for field in required_fields:
                if field not in action:
                    result['passed'] = False
                    result['warnings'].append(
                        f"交易动作缺少必要字段: {field}"
                    )
            
            # 检查账户有效性
            account = action.get('account')
            if account not in ['main', 'lab', 'both']:
                result['passed'] = False
                result['warnings'].append(
                    f"无效账户类型: {account}"
                )
            
            # 检查动作有效性
            action_type = action.get('action')
            if action_type not in ['buy', 'sell', 'hold', 'wait', 'reduce_position']:
                result['passed'] = False
                result['warnings'].append(
                    f"无效交易动作: {action_type}"
                )
        
        return result
    
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
    
    def save_validation_result(self, result: Dict, filename: str = None):
        """保存验证结果"""
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"risk_validation_{timestamp}.json"
        
        filepath = os.path.join(self.data_dir, "agents", "validations", filename)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            logger.info(f"验证结果已保存: {filepath}")
        except Exception as e:
            logger.error(f"保存验证结果失败: {e}")
    
    def generate_validation_summary(self, result: Dict) -> str:
        """生成验证摘要"""
        summary_parts = []
        
        # 决策结果
        decision_map = {
            'APPROVED': '通过',
            'REJECTED': '拒绝',
            'MODIFY': '需要修改'
        }
        summary_parts.append(f"决策: {decision_map.get(result.get('decision'), '未知')}")
        
        # 检查项目
        checks = result.get('checks_performed', [])
        summary_parts.append(f"检查项目: {len(checks)}个")
        
        # 警告数量
        warnings = result.get('warnings', [])
        if warnings:
            summary_parts.append(f"警告: {len(warnings)}个")
        
        # 修改建议
        modifications = result.get('modifications', [])
        if modifications:
            summary_parts.append(f"修改建议: {len(modifications)}个")
        
        return " | ".join(summary_parts)

def main():
    """主函数"""
    # 加载最新的交易计划
    agent = RiskControllerAgent()
    trading_plan = agent.load_trading_plan()
    
    if not trading_plan:
        print("❌ 未找到交易计划")
        print("请先运行Execution Planner Agent")
        return
    
    # 验证交易计划
    validation_result = agent.validate_trading_plan(trading_plan)
    
    # 打印验证摘要
    print("\n=== 风险验证摘要 ===")
    print(agent.generate_validation_summary(validation_result))
    
    # 打印详细结果
    print("\n=== 详细验证结果 ===")
    print(f"决策: {validation_result.get('decision')}")
    print(f"理由: {validation_result.get('reason')}")
    
    warnings = validation_result.get('warnings', [])
    if warnings:
        print(f"\n警告 ({len(warnings)}个):")
        for warning in warnings:
            print(f"  ⚠️ {warning}")
    
    modifications = validation_result.get('modifications', [])
    if modifications:
        print(f"\n修改建议 ({len(modifications)}个):")
        for mod in modifications:
            print(f"  🔧 {mod.get('action')}: {mod.get('code', '')}")
    
    checks = validation_result.get('checks_performed', [])
    print(f"\n检查项目: {', '.join(checks)}")
    
    # 保存验证结果
    agent.save_validation_result(validation_result)

if __name__ == "__main__":
    main()
