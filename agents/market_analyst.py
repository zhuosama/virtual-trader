#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Market Analyst Agent
市场分析专家 - 负责分析宏观、指数、板块轮动
"""

import json
import subprocess
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

class MarketAnalystAgent:
    """市场分析专家Agent"""
    
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
    
    def _fetch_stock_data(self, code: str) -> Optional[Dict]:
        """获取股票数据（腾讯API）"""
        try:
            # 确定市场代码
            if code.startswith('6') or code.startswith('5'):
                tx_code = f"sh{code}"
            else:
                tx_code = f"sz{code}"
            
            # 调用腾讯API
            cmd = f'curl -s "http://qt.gtimg.cn/q={tx_code}"'
            result = subprocess.run(
                cmd, 
                shell=True, 
                capture_output=True, 
                timeout=10
            )
            
            # GBK解码
            raw = result.stdout.decode('gbk', errors='ignore')
            
            if raw and '~' in raw:
                fields = raw.split('~')
                if len(fields) > 40:
                    return {
                        'code': code,
                        'name': fields[1],
                        'price': float(fields[3]) if fields[3] else None,
                        'prev_close': float(fields[4]) if fields[4] else None,
                        'change_pct': float(fields[32]) if fields[32] else None,
                        'volume': float(fields[6]) if fields[6] else None,
                        'high': float(fields[33]) if fields[33] else None,
                        'low': float(fields[34]) if fields[34] else None,
                    }
        except Exception as e:
            logger.error(f"获取股票数据失败 {code}: {e}")
        
        return None
    
    def analyze_macro_environment(self) -> Dict:
        """分析宏观环境"""
        logger.info("分析宏观环境...")
        
        # 这里应该实现真正的宏观分析
        # 目前先返回模拟数据
        return {
            'pmi': 50.2,  # 制造业PMI
            'cpi': 2.1,   # 消费者物价指数
            'interest_rate': 3.45,  # 利率水平
            'gdp_growth': 5.5,  # GDP增速
            'policy_stance': 'neutral',  # 政策立场
            'global_risk': 'medium'  # 全球风险
        }
    
    def analyze_index_trend(self) -> Dict:
        """分析指数趋势"""
        logger.info("分析指数趋势...")
        
        # 主要指数
        indices = {
            '000001': '上证指数',
            '399001': '深证成指', 
            '399006': '创业板指'
        }
        
        analysis = {}
        for code, name in indices.items():
            data = self._fetch_stock_data(code)
            if data:
                analysis[code] = {
                    'name': name,
                    'price': data['price'],
                    'change_pct': data['change_pct'],
                    'trend': self._judge_trend(data['change_pct'])
                }
        
        return analysis
    
    def _judge_trend(self, change_pct: float) -> str:
        """判断趋势"""
        if change_pct > 1.0:
            return 'strong_up'
        elif change_pct > 0.3:
            return 'up'
        elif change_pct > -0.3:
            return 'neutral'
        elif change_pct > -1.0:
            return 'down'
        else:
            return 'strong_down'
    
    def analyze_sector_rotation(self) -> Dict:
        """分析板块轮动"""
        logger.info("分析板块轮动...")
        
        # 关注板块
        sectors = {
            '煤炭': ['601088', '601225'],
            '新能源': ['300750', '300274'],
            '半导体': ['688981', '002049'],
            '消费电子': ['002475', '002230'],
            '白酒': ['600519', '000858'],
            '银行': ['600036', '601398']
        }
        
        sector_analysis = {}
        for sector, codes in sectors.items():
            sector_data = []
            for code in codes:
                data = self._fetch_stock_data(code)
                if data:
                    sector_data.append(data)
            
            if sector_data:
                avg_change = sum(d['change_pct'] for d in sector_data) / len(sector_data)
                sector_analysis[sector] = {
                    'avg_change': avg_change,
                    'strength': self._judge_strength(avg_change),
                    'stocks': len(sector_data)
                }
        
        return sector_analysis
    
    def _judge_strength(self, avg_change: float) -> str:
        """判断板块强度"""
        if avg_change > 2.0:
            return 'very_strong'
        elif avg_change > 1.0:
            return 'strong'
        elif avg_change > 0:
            return 'neutral'
        elif avg_change > -1.0:
            return 'weak'
        else:
            return 'very_weak'
    
    def identify_market_regime(self, macro: Dict, indices: Dict, sectors: Dict) -> str:
        """识别市场状态"""
        logger.info("识别市场状态...")
        
        # 简单判断逻辑
        # 1. 检查指数趋势
        index_trends = [v.get('trend') for v in indices.values()]
        
        # 2. 检查板块强度
        strong_sectors = sum(1 for v in sectors.values() if v['strength'] in ['strong', 'very_strong'])
        weak_sectors = sum(1 for v in sectors.values() if v['strength'] in ['weak', 'very_weak'])
        
        # 3. 判断市场状态
        if strong_sectors > weak_sectors and 'up' in index_trends:
            return 'bullish'
        elif weak_sectors > strong_sectors and 'down' in index_trends:
            return 'bearish'
        else:
            return 'neutral'
    
    def identify_risk_signals(self, macro: Dict, indices: Dict, sectors: Dict) -> List[str]:
        """识别风险信号"""
        logger.info("识别风险信号...")
        
        risk_signals = []
        
        # 1. 宏观风险
        if macro.get('pmi', 50) < 50:
            risk_signals.append("PMI低于50，经济收缩")
        
        if macro.get('cpi', 0) > 3:
            risk_signals.append("CPI超过3%，通胀压力")
        
        # 2. 指数风险
        for code, data in indices.items():
            if data.get('change_pct', 0) < -2:
                risk_signals.append(f"{data['name']}跌幅超过2%")
        
        # 3. 板块风险
        for sector, data in sectors.items():
            if data['strength'] == 'very_weak':
                risk_signals.append(f"{sector}板块表现极弱")
        
        return risk_signals
    
    def generate_sector_strength_ranking(self, sectors: Dict) -> List[Dict]:
        """生成板块强度排名"""
        logger.info("生成板块强度排名...")
        
        ranking = []
        for sector, data in sectors.items():
            ranking.append({
                'sector': sector,
                'avg_change': data['avg_change'],
                'strength': data['strength']
            })
        
        # 按平均涨幅排序
        ranking.sort(key=lambda x: x['avg_change'], reverse=True)
        
        return ranking
    
    def run_analysis(self) -> Dict:
        """运行完整分析"""
        logger.info("开始市场分析...")
        
        try:
            # 1. 分析宏观环境
            macro = self.analyze_macro_environment()
            
            # 2. 分析指数趋势
            indices = self.analyze_index_trend()
            
            # 3. 分析板块轮动
            sectors = self.analyze_sector_rotation()
            
            # 4. 识别市场状态
            market_regime = self.identify_market_regime(macro, indices, sectors)
            
            # 5. 识别风险信号
            risk_signals = self.identify_risk_signals(macro, indices, sectors)
            
            # 6. 生成板块排名
            sector_ranking = self.generate_sector_strength_ranking(sectors)
            
            # 7. 生成分析报告
            report = {
                'timestamp': datetime.now().isoformat(),
                'market_tone': market_regime,
                'sector_strength': sector_ranking,
                'risk_signals': risk_signals,
                'macro_indicators': macro,
                'index_analysis': indices,
                'sector_analysis': sectors,
                'analysis_summary': self._generate_summary(market_regime, sector_ranking, risk_signals)
            }
            
            logger.info("市场分析完成")
            return report
            
        except Exception as e:
            logger.error(f"市场分析失败: {e}")
            return {
                'timestamp': datetime.now().isoformat(),
                'error': str(e),
                'market_tone': 'unknown',
                'sector_strength': [],
                'risk_signals': [f"分析失败: {e}"]
            }
    
    def _generate_summary(self, market_regime: str, sector_ranking: List[Dict], risk_signals: List[str]) -> str:
        """生成分析摘要"""
        summary_parts = []
        
        # 市场状态
        regime_map = {
            'bullish': '看涨',
            'bearish': '看跌',
            'neutral': '中性',
            'unknown': '未知'
        }
        summary_parts.append(f"市场状态: {regime_map.get(market_regime, '未知')}")
        
        # 板块表现
        if sector_ranking:
            top_sector = sector_ranking[0]
            summary_parts.append(f"最强板块: {top_sector['sector']} (+{top_sector['avg_change']:.2f}%)")
        
        # 风险信号
        if risk_signals:
            summary_parts.append(f"风险信号: {len(risk_signals)}个")
        
        return " | ".join(summary_parts)
    
    def save_report(self, report: Dict, filename: str = None):
        """保存分析报告"""
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"market_analysis_{timestamp}.json"
        
        filepath = os.path.join(self.data_dir, "agents", "reports", filename)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            logger.info(f"分析报告已保存: {filepath}")
        except Exception as e:
            logger.error(f"保存报告失败: {e}")

def main():
    """主函数"""
    agent = MarketAnalystAgent()
    report = agent.run_analysis()
    
    # 打印报告摘要
    print("\n=== 市场分析报告摘要 ===")
    print(f"时间: {report['timestamp']}")
    print(f"市场状态: {report['market_tone']}")
    print(f"风险信号: {len(report['risk_signals'])}个")
    
    if report['sector_strength']:
        print("\n板块强度排名:")
        for i, sector in enumerate(report['sector_strength'][:5]):
            print(f"  {i+1}. {sector['sector']}: {sector['avg_change']:.2f}%")
    
    if report['risk_signals']:
        print("\n风险信号:")
        for signal in report['risk_signals']:
            print(f"  ⚠️ {signal}")
    
    # 保存报告
    agent.save_report(report)

if __name__ == "__main__":
    main()
