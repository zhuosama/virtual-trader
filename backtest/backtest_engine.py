#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股虚拟盘回测框架 v1.0
- 基于实际交易记录回放
- 参数敏感性分析
- 策略优化建议生成
"""

import json
import os

VTRADER_HOME = os.environ.get("VTRADER_HOME", os.path.expanduser("~/.hermes/virtual-trader"))
import math
from datetime import datetime, timedelta
from collections import defaultdict

# ============================================================
# 数据加载
# ============================================================

def load_price_data(path="/tmp/backtest_data.json"):
    """加载历史行情数据"""
    with open(path) as f:
        return json.load(f)

def load_trades(trade_dir=None):
    """加载所有交易记录"""
    if trade_dir is None:
        trade_dir = os.path.join(VTRADER_HOME, "trades", "2026-04")
    all_trades = []
    for f in sorted(os.listdir(trade_dir)):
        if f.endswith('.json'):
            with open(os.path.join(trade_dir, f)) as fp:
                data = json.load(fp)
                for t in data.get('trades', []):
                    t['date'] = data['date']
                    all_trades.append(t)
    return all_trades

def load_accounts():
    """加载当前账户状态"""
    base = os.path.join(VTRADER_HOME, "accounts")
    accounts = {}
    for name in ['main', 'lab']:
        with open(os.path.join(base, f"{name}.json")) as f:
            accounts[name] = json.load(f)
    return accounts

def load_performance_history():
    """加载绩效历史"""
    path = os.path.join(VTRADER_HOME, "strategies", "performance_history.json")
    with open(path) as f:
        return json.load(f)

# ============================================================
# 回测引擎核心
# ============================================================

class BacktestEngine:
    """回测引擎：基于实际交易记录回放组合表现"""

    def __init__(self, price_data, trades, initial_capital_main=1000000, initial_capital_lab=300000):
        self.price_data = price_data
        self.trades = trades
        self.initial_capital = {'main': initial_capital_main, 'lab': initial_capital_lab}

        # 构建 ticker -> name 映射
        self.ticker_name = {}
        for ticker, info in price_data.items():
            code = ticker.split('.')[0]
            self.ticker_name[code] = info['name']

        # 构建日期 -> 价格映射
        self.price_map = {}  # {code: {date: close_price}}
        for ticker, info in price_data.items():
            code = ticker.split('.')[0]
            self.price_map[code] = {}
            for d in info['data']:
                self.price_map[code][d['date']] = d['close']

        # 沪深300基准
        self.benchmark = {}
        if '000300.SS' in price_data:
            for d in price_data['000300.SS']['data']:
                self.benchmark[d['date']] = d['close']

    def _get_price(self, code, date):
        """获取某日收盘价"""
        if code in self.price_map and date in self.price_map[code]:
            return self.price_map[code][date]
        return None

    def _get_all_dates(self):
        """获取所有交易日期（从基准指数）"""
        return sorted(self.benchmark.keys())

    def _calc_commission(self, action, price, shares):
        """计算交易费用"""
        amount = price * shares
        commission = max(amount * 0.0003, 5)  # 佣金万三，最低5元
        stamp_tax = amount * 0.001 if action == 'sell' else 0  # 印花税千一（卖出）
        transfer_fee = amount * 0.00002  # 过户费
        return commission + stamp_tax + transfer_fee

    def run(self):
        """运行回测：逐日回放实际交易"""
        all_dates = self._get_all_dates()
        start_date = '2026-04-13'
        end_date = '2026-04-23'

        # 过滤回测日期范围
        dates = [d for d in all_dates if start_date <= d <= end_date]

        # 初始化账户状态
        accounts = {}
        for acc_name in ['main', 'lab']:
            accounts[acc_name] = {
                'cash': self.initial_capital[acc_name],
                'positions': {},  # {code: {shares, avg_cost, buy_date}}
                'total_value': self.initial_capital[acc_name],
            }

        # 按日期索引交易
        trades_by_date = defaultdict(list)
        for t in self.trades:
            trades_by_date[t['date']].append(t)

        # 逐日模拟
        daily_records = []
        for date in dates:
            day_trades = trades_by_date.get(date, [])

            # 1. 执行当日交易
            for t in day_trades:
                acc = accounts[t['account']]
                code = t['code']
                action = t['action']
                price = t['price']
                shares = t['shares']
                cost = self._calc_commission(action, price, shares)

                if action == 'buy':
                    total_cost = price * shares + cost
                    if acc['cash'] >= total_cost:
                        acc['cash'] -= total_cost
                        if code in acc['positions']:
                            pos = acc['positions'][code]
                            old_total = pos['avg_cost'] * pos['shares']
                            new_total = old_total + price * shares
                            pos['shares'] += shares
                            pos['avg_cost'] = new_total / pos['shares']
                        else:
                            acc['positions'][code] = {
                                'shares': shares,
                                'avg_cost': price,
                                'buy_date': date,
                            }
                elif action == 'sell':
                    if code in acc['positions']:
                        pos = acc['positions'][code]
                        sell_shares = min(shares, pos['shares'])
                        revenue = price * sell_shares - cost
                        acc['cash'] += revenue
                        pos['shares'] -= sell_shares
                        if pos['shares'] <= 0:
                            del acc['positions'][code]

            # 2. 计算当日市值
            for acc_name, acc in accounts.items():
                portfolio_value = 0
                for code, pos in acc['positions'].items():
                    p = self._get_price(code, date)
                    if p:
                        portfolio_value += p * pos['shares']
                    else:
                        portfolio_value += pos['avg_cost'] * pos['shares']
                acc['total_value'] = acc['cash'] + portfolio_value

            # 3. 记录当日数据
            record = {
                'date': date,
                'main_value': accounts['main']['total_value'],
                'lab_value': accounts['lab']['total_value'],
                'combined_value': accounts['main']['total_value'] + accounts['lab']['total_value'],
                'main_positions': len(accounts['main']['positions']),
                'lab_positions': len(accounts['lab']['positions']),
                'trades_count': len(day_trades),
            }
            daily_records.append(record)

        return daily_records, accounts

# ============================================================
# 绩效分析
# ============================================================

class PerformanceAnalyzer:
    """绩效分析器"""

    def __init__(self, daily_records, price_data, initial_capital_main=1000000, initial_capital_lab=300000):
        self.records = daily_records
        self.price_data = price_data
        self.initial_capital = {'main': initial_capital_main, 'lab': initial_capital_lab}

        # 沪深300基准
        self.benchmark_prices = {}
        if '000300.SS' in price_data:
            for d in price_data['000300.SS']['data']:
                self.benchmark_prices[d['date']] = d['close']

    def _daily_returns(self, values):
        """计算日收益率序列"""
        returns = []
        for i in range(1, len(values)):
            if values[i-1] > 0:
                returns.append((values[i] - values[i-1]) / values[i-1])
        return returns

    def calc_metrics(self, values, name="Strategy"):
        """计算核心绩效指标"""
        if len(values) < 2:
            return {}

        total_return = (values[-1] - values[0]) / values[0]
        daily_rets = self._daily_returns(values)

        # 年化收益（按交易日）
        trading_days = len(values)
        annualized = (1 + total_return) ** (252 / max(trading_days, 1)) - 1

        # 波动率
        if daily_rets:
            mean_ret = sum(daily_rets) / len(daily_rets)
            variance = sum((r - mean_ret) ** 2 for r in daily_rets) / max(len(daily_rets) - 1, 1)
            volatility = math.sqrt(variance) * math.sqrt(252)
        else:
            volatility = 0

        # 夏普比率（无风险利率2%）
        risk_free_daily = 0.02 / 252
        if daily_rets and volatility > 0:
            excess_returns = [r - risk_free_daily for r in daily_rets]
            sharpe = (sum(excess_returns) / len(excess_returns)) / (volatility / math.sqrt(252)) if volatility > 0 else 0
        else:
            sharpe = 0

        # 最大回撤
        peak = values[0]
        max_dd = 0
        for v in values:
            if v > peak:
                peak = v
            dd = (peak - v) / peak if peak > 0 else 0
            max_dd = max(max_dd, dd)

        # 胜率（日）
        win_days = sum(1 for r in daily_rets if r > 0)
        win_rate = win_days / len(daily_rets) if daily_rets else 0

        # 盈亏比
        gains = [r for r in daily_rets if r > 0]
        losses = [r for r in daily_rets if r < 0]
        avg_gain = sum(gains) / len(gains) if gains else 0
        avg_loss = abs(sum(losses) / len(losses)) if losses else 0.001
        profit_loss_ratio = avg_gain / avg_loss if avg_loss > 0 else float('inf')

        return {
            'name': name,
            'total_return_pct': round(total_return * 100, 4),
            'annualized_return_pct': round(annualized * 100, 2),
            'volatility_pct': round(volatility * 100, 2),
            'sharpe_ratio': round(sharpe, 3),
            'max_drawdown_pct': round(max_dd * 100, 2),
            'win_rate_pct': round(win_rate * 100, 1),
            'profit_loss_ratio': round(profit_loss_ratio, 2),
            'trading_days': trading_days,
            'final_value': round(values[-1], 2),
        }

    def full_analysis(self):
        """完整绩效分析"""
        results = {}

        # 主账户
        main_values = [r['main_value'] for r in self.records]
        results['main'] = self.calc_metrics(main_values, "主账户(价值趋势)")

        # 实验账户
        lab_values = [r['lab_value'] for r in self.records]
        results['lab'] = self.calc_metrics(lab_values, "实验账户(动量突破)")

        # 合计
        combined_values = [r['combined_value'] for r in self.records]
        results['combined'] = self.calc_metrics(combined_values, "合计")

        # 沪深300基准
        dates = [r['date'] for r in self.records]
        bench_values = []
        for d in dates:
            if d in self.benchmark_prices:
                bench_values.append(self.benchmark_prices[d])
        if bench_values:
            # 归一化到初始资本
            scale = combined_values[0] / bench_values[0] if bench_values[0] > 0 else 1
            bench_scaled = [v * scale for v in bench_values]
            results['benchmark'] = self.calc_metrics(bench_scaled, "沪深300")

        return results

    def daily_vs_benchmark(self):
        """每日收益率对比"""
        dates = [r['date'] for r in self.records]
        comparison = []

        for i in range(1, len(self.records)):
            date = dates[i]
            main_ret = (self.records[i]['main_value'] - self.records[i-1]['main_value']) / self.records[i-1]['main_value']
            lab_ret = (self.records[i]['lab_value'] - self.records[i-1]['lab_value']) / self.records[i-1]['lab_value']
            combined_ret = (self.records[i]['combined_value'] - self.records[i-1]['combined_value']) / self.records[i-1]['combined_value']

            bench_ret = 0
            if date in self.benchmark_prices and dates[i-1] in self.benchmark_prices:
                bp = self.benchmark_prices[date]
                bp_prev = self.benchmark_prices[dates[i-1]]
                if bp_prev > 0:
                    bench_ret = (bp - bp_prev) / bp_prev

            comparison.append({
                'date': date,
                'main_pct': round(main_ret * 100, 4),
                'lab_pct': round(lab_ret * 100, 4),
                'combined_pct': round(combined_ret * 100, 4),
                'benchmark_pct': round(bench_ret * 100, 4),
                'main_beat': main_ret > bench_ret,
                'lab_beat': lab_ret > bench_ret,
            })

        return comparison

# ============================================================
# 参数敏感性分析
# ============================================================

class ParameterSensitivity:
    """参数敏感性分析：测试不同参数对策略表现的影响"""

    def __init__(self, trades, price_data):
        self.trades = trades
        self.price_data = price_data

    def analyze_stop_loss_impact(self, account='lab'):
        """分析止损阈值对实验账户的影响"""
        # 筛选实验账户的卖出交易
        sell_trades = [t for t in self.trades if t['account'] == account and t['action'] == 'sell']

        results = []
        for threshold in [4.0, 4.5, 5.0, 5.5, 6.0, 7.0, 8.0]:
            # 模拟不同止损线的触发情况
            triggered = 0
            saved_loss = 0
            for t in sell_trades:
                signal = t.get('signal', '')
                if '止损' in signal or 'stop' in signal.lower():
                    # 检查买入价和卖出价
                    buy_price = t.get('price', 0)
                    # 找到对应的买入交易
                    for bt in self.trades:
                        if bt['account'] == account and bt['code'] == t['code'] and bt['action'] == 'buy':
                            buy_price = bt['price']
                            break
                    sell_price = t['price']
                    loss_pct = (sell_price - buy_price) / buy_price * 100 if buy_price > 0 else 0
                    if abs(loss_pct) >= threshold:
                        triggered += 1

            results.append({
                'threshold_pct': threshold,
                'would_trigger': triggered,
            })

        return results

    def analyze_take_profit_impact(self, account='main'):
        """分析止盈阈值对主账户的影响"""
        sell_trades = [t for t in self.trades if t['account'] == account and t['action'] == 'sell']

        results = []
        for threshold in [10, 12, 15, 18, 20]:
            # 模拟不同止盈线
            would_have_held = 0
            for t in sell_trades:
                for bt in self.trades:
                    if bt['account'] == account and bt['code'] == t['code'] and bt['action'] == 'buy':
                        gain_pct = (t['price'] - bt['price']) / bt['price'] * 100 if bt['price'] > 0 else 0
                        if gain_pct < threshold:
                            would_have_held += 1
                        break

            results.append({
                'threshold_pct': threshold,
                'would_still_hold': would_have_held,
            })

        return results

    def analyze_position_sizing(self, account='main'):
        """分析仓位管理效果"""
        # 按日期统计每笔交易的仓位占比
        buy_trades = [t for t in self.trades if t['account'] == account and t['action'] == 'buy']

        position_sizes = []
        for t in buy_trades:
            amount = t['price'] * t['shares']
            initial = 1000000 if account == 'main' else 300000
            pct = amount / initial * 100
            position_sizes.append({
                'date': t['date'],
                'code': t['code'],
                'name': t['name'],
                'amount': amount,
                'pct_of_portfolio': round(pct, 2),
                'exceeds_8pct': pct > 8,
                'exceeds_10pct': pct > 10,
            })

        return position_sizes

# ============================================================
# 策略诊断与优化建议
# ============================================================

class StrategyDiagnostics:
    """策略诊断：基于回测结果生成优化建议"""

    def __init__(self, metrics, daily_comparison, trades, price_data):
        self.metrics = metrics
        self.daily_comparison = daily_comparison
        self.trades = trades
        self.price_data = price_data

    def diagnose(self):
        """全面诊断"""
        issues = []
        suggestions = []

        # 1. 主账户诊断
        main = self.metrics.get('main', {})
        bench = self.metrics.get('benchmark', {})

        # 跑输基准
        if main.get('total_return_pct', 0) < bench.get('total_return_pct', 0):
            underperform = bench['total_return_pct'] - main['total_return_pct']
            issues.append(f"主账户跑输基准 {underperform:.2f}%")
            suggestions.append({
                'type': 'parameter',
                'target': 'main',
                'issue': '跑输基准',
                'suggestion': '考虑提高仓位上限或放宽入场条件',
                'priority': 'high',
            })

        # 最大回撤
        if main.get('max_drawdown_pct', 0) > 5:
            issues.append(f"主账户最大回撤 {main['max_drawdown_pct']:.2f}%偏高")
            suggestions.append({
                'type': 'rule',
                'target': 'main',
                'issue': '回撤偏大',
                'suggestion': '收紧止损线从7%到6%，或增加趋势过滤条件',
                'priority': 'medium',
            })

        # 2. 实验账户诊断
        lab = self.metrics.get('lab', {})

        # 高波动
        if lab.get('volatility_pct', 0) > 30:
            issues.append(f"实验账户年化波动率 {lab['volatility_pct']:.1f}%过高")
            suggestions.append({
                'type': 'parameter',
                'target': 'lab',
                'issue': '波动率过高',
                'suggestion': '降低单票仓位上限从20%到15%，增加分散度',
                'priority': 'medium',
            })

        # 3. 交易行为分析
        main_trades = [t for t in self.trades if t['account'] == 'main']
        lab_trades = [t for t in self.trades if t['account'] == 'lab']

        # 主账户买入后立即亏损的交易
        main_buys = {t['code']: t for t in main_trades if t['action'] == 'buy'}
        main_sells = [t for t in main_trades if t['action'] == 'sell']
        bad_trades = 0
        for sell in main_sells:
            if sell['code'] in main_buys:
                buy = main_buys[sell['code']]
                if sell['price'] < buy['price']:
                    bad_trades += 1

        if bad_trades > 0:
            issues.append(f"主账户{bad_trades}笔交易亏损卖出")
            suggestions.append({
                'type': 'rule',
                'target': 'main',
                'issue': '亏损交易较多',
                'suggestion': '增加入场确认条件：要求MA5>MA20至少3天后再建仓',
                'priority': 'high',
            })

        # 4. 仓位集中度分析
        position_analyzer = ParameterSensitivity(self.trades, self.price_data)
        main_positions = position_analyzer.analyze_position_sizing('main')
        oversized = [p for p in main_positions if p['exceeds_10pct']]
        if oversized:
            issues.append(f"主账户{len(oversized)}笔交易超过10%仓位限制")
            for p in oversized:
                suggestions.append({
                    'type': 'parameter',
                    'target': 'main',
                    'issue': f"{p['name']}仓位{p['pct_of_portfolio']:.1f}%超限",
                    'suggestion': '严格执行单笔8%上限，分批建仓间隔拉长到5个交易日',
                    'priority': 'high',
                })

        # 5. 实验账户假突破分析
        lab_sells_loss = [t for t in lab_trades if t['action'] == 'sell']
        quick_exits = 0
        for sell in lab_sells_loss:
            for buy in lab_trades:
                if buy['code'] == sell['code'] and buy['action'] == 'buy':
                    # 计算持有天数
                    try:
                        bd = datetime.strptime(buy['date'], '%Y-%m-%d')
                        sd = datetime.strptime(sell['date'], '%Y-%m-%d')
                        hold_days = (sd - bd).days
                        if hold_days <= 5 and sell['price'] < buy['price']:
                            quick_exits += 1
                    except:
                        pass
                    break

        if quick_exits > 0:
            issues.append(f"实验账户{quick_exits}笔交易在5天内亏损退出（假突破）")
            suggestions.append({
                'type': 'parameter',
                'target': 'lab',
                'issue': '假突破频繁',
                'suggestion': '量比阈值从1.4提高到1.6，或增加"板块连续2日上涨"确认条件',
                'priority': 'high',
            })

        # 6. 胜率分析
        if lab.get('win_rate_pct', 100) < 50:
            issues.append(f"实验账户日胜率仅{lab['win_rate_pct']:.1f}%")
            suggestions.append({
                'type': 'rule',
                'target': 'lab',
                'issue': '胜率偏低',
                'suggestion': '增加MACD金叉确认+RSI<65入场过滤，减少追高',
                'priority': 'medium',
            })

        return {
            'issues': issues,
            'suggestions': suggestions,
            'summary': self._generate_summary(issues, suggestions),
        }

    def _generate_summary(self, issues, suggestions):
        """生成诊断摘要"""
        high_priority = [s for s in suggestions if s['priority'] == 'high']
        medium_priority = [s for s in suggestions if s['priority'] == 'medium']

        lines = []
        lines.append(f"发现 {len(issues)} 个问题，{len(suggestions)} 条建议")
        lines.append(f"  高优先级: {len(high_priority)} 条")
        lines.append(f"  中优先级: {len(medium_priority)} 条")
        return '\n'.join(lines)


# ============================================================
# 报告生成
# ============================================================

def generate_report(metrics, daily_comparison, diagnostics, trades):
    """生成完整回测报告"""
    lines = []
    lines.append("=" * 60)
    lines.append("  A股虚拟盘回测报告")
    lines.append(f"  回测区间: 2026-04-13 ~ 2026-04-23 (9个交易日)")
    lines.append(f"  生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("=" * 60)

    # 核心指标对比
    lines.append("\n[ 一、核心绩效指标 ]\n")
    lines.append(f"{'指标':<18} {'主账户':>10} {'实验账户':>10} {'合计':>10} {'沪深300':>10}")
    lines.append("-" * 60)

    keys = [
        ('total_return_pct', '累计收益率(%)'),
        ('annualized_return_pct', '年化收益率(%)'),
        ('volatility_pct', '年化波动率(%)'),
        ('sharpe_ratio', '夏普比率'),
        ('max_drawdown_pct', '最大回撤(%)'),
        ('win_rate_pct', '日胜率(%)'),
        ('profit_loss_ratio', '盈亏比'),
        ('final_value', '期末资产(元)'),
    ]

    for key, label in keys:
        main_val = metrics.get('main', {}).get(key, '-')
        lab_val = metrics.get('lab', {}).get(key, '-')
        comb_val = metrics.get('combined', {}).get(key, '-')
        bench_val = metrics.get('benchmark', {}).get(key, '-')

        if isinstance(main_val, float):
            main_str = f"{main_val:>10.2f}"
        else:
            main_str = f"{str(main_val):>10}"
        if isinstance(lab_val, float):
            lab_str = f"{lab_val:>10.2f}"
        else:
            lab_str = f"{str(lab_val):>10}"
        if isinstance(comb_val, float):
            comb_str = f"{comb_val:>10.2f}"
        else:
            comb_str = f"{str(comb_val):>10}"
        if isinstance(bench_val, float):
            bench_str = f"{bench_val:>10.2f}"
        else:
            bench_str = f"{str(bench_val):>10}"

        lines.append(f"{label:<18} {main_str} {lab_str} {comb_str} {bench_str}")

    # 每日表现
    lines.append("\n[ 二、每日收益率对比 ]\n")
    lines.append(f"{'日期':<12} {'主账户':>8} {'实验':>8} {'合计':>8} {'沪深300':>8} {'战胜':>4}")
    lines.append("-" * 52)
    for dc in daily_comparison:
        beat = "✓" if dc['main_beat'] else "✗"
        lines.append(f"{dc['date']:<12} {dc['main_pct']:>+7.3f}% {dc['lab_pct']:>+7.3f}% {dc['combined_pct']:>+7.3f}% {dc['benchmark_pct']:>+7.3f}%  {beat}")

    # 胜负统计
    main_wins = sum(1 for dc in daily_comparison if dc['main_beat'])
    lab_wins = sum(1 for dc in daily_comparison if dc['lab_beat'])
    total_days = len(daily_comparison)
    lines.append(f"\n主账户战胜基准: {main_wins}/{total_days} 天 ({main_wins/total_days*100:.0f}%)")
    lines.append(f"实验账户战胜基准: {lab_wins}/{total_days} 天 ({lab_wins/total_days*100:.0f}%)")

    # 交易分析
    lines.append("\n[ 三、交易行为分析 ]\n")
    main_trades = [t for t in trades if t['account'] == 'main']
    lab_trades = [t for t in trades if t['account'] == 'lab']
    lines.append(f"主账户交易次数: {len(main_trades)} (买{sum(1 for t in main_trades if t['action']=='buy')}, 卖{sum(1 for t in main_trades if t['action']=='sell')})")
    lines.append(f"实验账户交易次数: {len(lab_trades)} (买{sum(1 for t in lab_trades if t['action']=='buy')}, 卖{sum(1 for t in lab_trades if t['action']=='sell')})")

    # 盈亏交易明细
    lines.append("\n  已平仓交易盈亏:")
    for acc_name, acc_trades in [('主账户', main_trades), ('实验账户', lab_trades)]:
        sells = [t for t in acc_trades if t['action'] == 'sell']
        buys_map = {}
        for t in acc_trades:
            if t['action'] == 'buy':
                buys_map[t['code']] = t
        for sell in sells:
            if sell['code'] in buys_map:
                buy = buys_map[sell['code']]
                pnl_pct = (sell['price'] - buy['price']) / buy['price'] * 100
                emoji = "+" if pnl_pct > 0 else ""
                lines.append(f"  {acc_name} | {sell['name']:8} | 买{buy['price']:.2f}→卖{sell['price']:.2f} | {emoji}{pnl_pct:.2f}%")

    # 诊断结果
    lines.append("\n[ 四、策略诊断 ]\n")
    for i, issue in enumerate(diagnostics['issues'], 1):
        lines.append(f"  ⚠️ {i}. {issue}")

    lines.append(f"\n{diagnostics['summary']}")

    lines.append("\n[ 五、优化建议 ]\n")
    for i, s in enumerate(diagnostics['suggestions'], 1):
        priority = "🔴" if s['priority'] == 'high' else "🟡"
        lines.append(f"  {priority} {i}. [{s['target']}账户] {s['issue']}")
        lines.append(f"     → {s['suggestion']}")

    lines.append("\n" + "=" * 60)
    return '\n'.join(lines)


# ============================================================
# 主入口
# ============================================================

def main():
    print("加载数据...")
    price_data = load_price_data()
    trades = load_trades()

    print(f"行情数据: {len(price_data)} 只标的")
    print(f"交易记录: {len(trades)} 笔交易")

    # 运行回测
    print("\n运行回测引擎...")
    engine = BacktestEngine(price_data, trades)
    daily_records, final_accounts = engine.run()
    print(f"回测完成: {len(daily_records)} 个交易日")

    # 绩效分析
    print("\n计算绩效指标...")
    analyzer = PerformanceAnalyzer(daily_records, price_data)
    metrics = analyzer.full_analysis()
    daily_comparison = analyzer.daily_vs_benchmark()

    # 参数敏感性
    print("\n运行参数敏感性分析...")
    sensitivity = ParameterSensitivity(trades, price_data)
    stop_loss_analysis = sensitivity.analyze_stop_loss_impact('lab')
    position_analysis = sensitivity.analyze_position_sizing('main')

    # 策略诊断
    print("\n运行策略诊断...")
    diagnostics_engine = StrategyDiagnostics(metrics, daily_comparison, trades, price_data)
    diagnostics = diagnostics_engine.diagnose()

    # 生成报告
    report = generate_report(metrics, daily_comparison, diagnostics, trades)
    print(report)

    # 保存报告
    report_path = os.path.join(VTRADER_HOME, "reports", "backtest_20260424.md")
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"\n报告已保存: {report_path}")

    # 保存详细数据
    detail_path = "/tmp/backtest_results.json"
    with open(detail_path, 'w', encoding='utf-8') as f:
        json.dump({
            'metrics': metrics,
            'daily_comparison': daily_comparison,
            'diagnostics': diagnostics,
            'stop_loss_sensitivity': stop_loss_analysis,
            'position_analysis': position_analysis,
        }, f, ensure_ascii=False, indent=2)
    print(f"详细数据已保存: {detail_path}")

if __name__ == '__main__':
    main()
