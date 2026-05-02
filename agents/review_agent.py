#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Review Agent
复盘分析专家 - 盘后分析，对比预期与实际，提取经验教训
"""

import json
import os

VTRADER_HOME = os.environ.get("VTRADER_HOME", os.path.expanduser("~/.hermes/virtual-trader"))
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import logging

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class ReviewAgent:
    """复盘分析专家Agent"""
    
    def __init__(self, config_path: str = None):
        """初始化Agent"""
        self.config = self._load_config(config_path)
        self.data_dir = VTRADER_HOME
        
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
    
    def load_daily_data(self, date_str: str = None) -> Dict:
        """加载日度数据"""
        if date_str is None:
            date_str = datetime.now().strftime("%Y-%m-%d")
        
        # 加载交易记录
        trades_path = os.path.join(self.data_dir, "trades", f"{date_str[:7]}", f"{date_str}.json")
        trades = {}
        if os.path.exists(trades_path):
            try:
                with open(trades_path, 'r', encoding='utf-8') as f:
                    trades = json.load(f)
            except Exception as e:
                logger.error(f"加载交易记录失败: {e}")
        
        # 加载日报
        report_path = os.path.join(self.data_dir, "reports", "daily", f"{date_str}.md")
        report_content = ""
        if os.path.exists(report_path):
            try:
                with open(report_path, 'r', encoding='utf-8') as f:
                    report_content = f.read()
            except Exception as e:
                logger.error(f"加载日报失败: {e}")
        
        # 加载账户数据
        accounts = self._load_accounts()
        
        return {
            'date': date_str,
            'trades': trades,
            'report': report_content,
            'accounts': accounts
        }
    
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
    
    def analyze_performance(self, daily_data: Dict) -> Dict:
        """分析绩效"""
        logger.info("分析绩效...")
        
        accounts = daily_data.get('accounts', {})
        trades = daily_data.get('trades', {})
        
        performance = {
            'daily_return': {},
            'benchmark_comparison': {},
            'risk_adjusted_return': {},
            'attribution': {}
        }
        
        # 分析每个账户
        for account_type in ['main', 'lab']:
            if account_type not in accounts:
                continue
            
            account = accounts[account_type]
            
            # 日收益率
            daily_pnl = account.get('daily_pnl', 0)
            total_value = account.get('total_value', 1)
            daily_return = daily_pnl / total_value if total_value > 0 else 0
            
            performance['daily_return'][account_type] = {
                'pnl': daily_pnl,
                'return': daily_return,
                'total_value': total_value
            }
            
            # 绩效归因
            if account_type in trades.get('account_snapshots', {}):
                snapshot = trades['account_snapshots'][account_type]
                performance['attribution'][account_type] = {
                    'daily_pnl': snapshot.get('daily_pnl', 0),
                    'daily_pnl_pct': snapshot.get('daily_pnl_pct', 0)
                }
        
        return performance
    
    def analyze_trades(self, daily_data: Dict) -> Dict:
        """分析交易"""
        logger.info("分析交易...")
        
        trades = daily_data.get('trades', {})
        trade_list = trades.get('trades', [])
        
        analysis = {
            'total_trades': len(trade_list),
            'buy_trades': 0,
            'sell_trades': 0,
            'trade_details': [],
            'best_trade': None,
            'worst_trade': None
        }
        
        # 分析每笔交易
        for trade in trade_list:
            action = trade.get('action', '')
            if action == 'buy':
                analysis['buy_trades'] += 1
            elif action == 'sell':
                analysis['sell_trades'] += 1
            
            # 交易详情
            analysis['trade_details'].append({
                'account': trade.get('account'),
                'action': action,
                'code': trade.get('code'),
                'name': trade.get('name'),
                'price': trade.get('price'),
                'shares': trade.get('shares'),
                'amount': trade.get('amount'),
                'signal': trade.get('signal')
            })
        
        return analysis
    
    def identify_mistakes(self, daily_data: Dict, performance: Dict, trade_analysis: Dict) -> List[Dict]:
        """识别错误"""
        logger.info("识别错误...")
        
        mistakes = []
        
        # 1. 检查仓位错误
        accounts = daily_data.get('accounts', {})
        for account_type in ['main', 'lab']:
            if account_type not in accounts:
                continue
            
            account = accounts[account_type]
            positions = account.get('positions', [])
            
            # 检查仓位超限
            for position in positions:
                position_pct = position.get('market_value', 0) / account.get('total_value', 1)
                limit = 0.10 if account_type == 'main' else 0.20
                
                if position_pct > limit:
                    mistakes.append({
                        'type': 'position_limit_exceeded',
                        'description': f"{account_type}账户{position.get('name')}仓位{position_pct:.1%}超过限制{limit:.1%}",
                        'impact': 'high',
                        'prevention': '严格执行仓位限制，分批建仓'
                    })
        
        # 2. 检查止损错误
        # 这里应该检查是否有持仓触发止损但未执行
        # 目前先返回空列表
        
        return mistakes
    
    def extract_lessons(self, daily_data: Dict, performance: Dict, trade_analysis: Dict, mistakes: List[Dict]) -> Dict:
        """提取经验教训"""
        logger.info("提取经验教训...")
        
        lessons = {
            'what_worked': [],
            'what_failed': [],
            'why': {}
        }
        
        # 分析有效的做法
        if trade_analysis['total_trades'] > 0:
            lessons['what_worked'].append(f"今日执行了{trade_analysis['total_trades']}笔交易")
        
        # 分析失败的做法
        for mistake in mistakes:
            lessons['what_failed'].append(mistake.get('description'))
            lessons['why'][mistake.get('type')] = mistake.get('prevention')
        
        # 从日报中提取经验
        report = daily_data.get('report', '')
        if '洞察' in report:
            lessons['what_worked'].append("日报中包含有价值的洞察")
        
        return lessons
    
    def generate_review_report(self, daily_data: Dict) -> Dict:
        """生成复盘报告"""
        logger.info("生成复盘报告...")
        
        # 1. 分析绩效
        performance = self.analyze_performance(daily_data)
        
        # 2. 分析交易
        trade_analysis = self.analyze_trades(daily_data)
        
        # 3. 识别错误
        mistakes = self.identify_mistakes(daily_data, performance, trade_analysis)
        
        # 4. 提取经验教训
        lessons = self.extract_lessons(daily_data, performance, trade_analysis, mistakes)
        
        # 5. 生成报告
        report = {
            'timestamp': datetime.now().isoformat(),
            'date': daily_data.get('date'),
            'performance': performance,
            'trade_analysis': trade_analysis,
            'mistakes': mistakes,
            'lessons': lessons,
            'accounts': daily_data.get('accounts', {}),  # 添加账户数据
            'summary': self._generate_summary(performance, trade_analysis, mistakes, lessons)
        }
        
        return report
    
    def _generate_summary(self, performance: Dict, trade_analysis: Dict, mistakes: List[Dict], lessons: Dict) -> str:
        """生成摘要"""
        summary_parts = []
        
        # 绩效摘要
        for account_type, data in performance.get('daily_return', {}).items():
            pnl = data.get('pnl', 0)
            return_pct = data.get('return', 0)
            emoji = "📈" if pnl > 0 else "📉" if pnl < 0 else "➡️"
            summary_parts.append(f"{emoji} {account_type}账户: {pnl:+,.0f}元 ({return_pct:+.2%})")
        
        # 交易摘要
        if trade_analysis['total_trades'] > 0:
            summary_parts.append(f"今日交易: {trade_analysis['total_trades']}笔")
        
        # 错误摘要
        if mistakes:
            summary_parts.append(f"发现错误: {len(mistakes)}个")
        
        # 经验教训
        if lessons.get('what_worked'):
            summary_parts.append(f"有效做法: {len(lessons['what_worked'])}个")
        
        return " | ".join(summary_parts)
    
    def save_review_report(self, report: Dict, filename: str = None):
        """保存复盘报告"""
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"review_report_{timestamp}.json"
        
        filepath = os.path.join(self.data_dir, "agents", "reviews", filename)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            logger.info(f"复盘报告已保存: {filepath}")
        except Exception as e:
            logger.error(f"保存复盘报告失败: {e}")
    
    def generate_review_summary(self, report: Dict) -> str:
        """生成复盘摘要"""
        summary_parts = []
        
        # 日期
        date = report.get('date', '')
        summary_parts.append(f"日期: {date}")
        
        # 绩效
        performance = report.get('performance', {})
        daily_return = performance.get('daily_return', {})
        
        for account_type, data in daily_return.items():
            pnl = data.get('pnl', 0)
            emoji = "📈" if pnl > 0 else "📉" if pnl < 0 else "➡️"
            summary_parts.append(f"{emoji} {account_type}: {pnl:+,.0f}元")
        
        # 交易
        trade_analysis = report.get('trade_analysis', {})
        total_trades = trade_analysis.get('total_trades', 0)
        if total_trades > 0:
            summary_parts.append(f"交易: {total_trades}笔")
        
        # 错误
        mistakes = report.get('mistakes', [])
        if mistakes:
            summary_parts.append(f"错误: {len(mistakes)}个")
        
        # 经验教训
        lessons = report.get('lessons', {})
        what_worked = lessons.get('what_worked', [])
        what_failed = lessons.get('what_failed', [])
        
        if what_worked:
            summary_parts.append(f"有效: {len(what_worked)}个")
        if what_failed:
            summary_parts.append(f"失败: {len(what_failed)}个")
        
        return " | ".join(summary_parts)

def main():
    """主函数"""
    agent = ReviewAgent()
    
    # 加载今日数据
    daily_data = agent.load_daily_data()
    
    if not daily_data.get('accounts'):
        print("❌ 未找到账户数据")
        return
    
    # 生成复盘报告
    report = agent.generate_review_report(daily_data)
    
    # 打印复盘摘要
    print("\n=== 复盘报告摘要 ===")
    print(agent.generate_review_summary(report))
    
    # 打印详细报告
    print("\n=== 详细复盘报告 ===")
    print(f"日期: {report.get('date')}")
    
    # 绩效
    performance = report.get('performance', {})
    daily_return = performance.get('daily_return', {})
    if daily_return:
        print("\n绩效:")
        for account_type, data in daily_return.items():
            pnl = data.get('pnl', 0)
            return_pct = data.get('return', 0)
            emoji = "📈" if pnl > 0 else "📉" if pnl < 0 else "➡️"
            print(f"  {emoji} {account_type}账户: {pnl:+,.0f}元 ({return_pct:+.2%})")
    
    # 交易
    trade_analysis = report.get('trade_analysis', {})
    if trade_analysis.get('total_trades', 0) > 0:
        print(f"\n交易: {trade_analysis['total_trades']}笔")
        print(f"  买入: {trade_analysis['buy_trades']}笔")
        print(f"  卖出: {trade_analysis['sell_trades']}笔")
    
    # 错误
    mistakes = report.get('mistakes', [])
    if mistakes:
        print(f"\n错误 ({len(mistakes)}个):")
        for mistake in mistakes:
            print(f"  ❌ {mistake.get('description')}")
    
    # 经验教训
    lessons = report.get('lessons', {})
    what_worked = lessons.get('what_worked', [])
    what_failed = lessons.get('what_failed', [])
    
    if what_worked:
        print(f"\n有效做法 ({len(what_worked)}个):")
        for item in what_worked:
            print(f"  ✅ {item}")
    
    if what_failed:
        print(f"\n失败做法 ({len(what_failed)}个):")
        for item in what_failed:
            print(f"  ❌ {item}")
    
    # 保存复盘报告
    agent.save_review_report(report)

if __name__ == "__main__":
    main()
