# 多Agent交易系统使用指南

> 版本：v1.0  
> 创建时间：2026-04-22  
> 状态：已测试，可使用

---

## 一、系统概述

### 1. 系统架构

```
多Agent交易系统：

5个专业Agent：
├─ Market Analyst Agent：市场分析专家
├─ Execution Planner Agent：交易计划专家
├─ Risk Controller Agent：风控专家
├─ Review Agent：复盘分析专家
└─ Strategy Maintainer Agent：策略迭代专家

协调器：
└─ MultiAgent Coordinator：协调工作流
```

### 2. 工作流程

**盘前流程**（每日08:10北京）：
```
Market Analyst → Execution Planner → Risk Controller → 交易计划
```

**盘后流程**（每日15:30北京）：
```
Review Agent → Strategy Maintainer → 策略更新建议
```

**周末复盘**（周六08:00北京）：
```
扩展版盘后复盘
```

---

## 二、安装与配置

### 1. 目录结构

```
~/.hermes/virtual-trader/agents/
├─ market_analyst.py        # 市场分析Agent
├─ execution_planner.py     # 交易计划Agent
├─ risk_controller.py       # 风控Agent
├─ review_agent.py          # 复盘Agent
├─ strategy_maintainer.py   # 策略迭代Agent
├─ coordinator.py           # 协调器
├─ config.json              # 配置文件
└─ reports/                 # 分析报告目录
```

### 2. 配置文件

配置文件位置：`~/.hermes/virtual-trader/agents/config.json`

```json
{
  "agents": {
    "market_analyst": {
      "enabled": true,
      "timeout": 300,
      "retry_count": 3
    },
    "execution_planner": {
      "enabled": true,
      "timeout": 300,
      "retry_count": 3
    },
    "risk_controller": {
      "enabled": true,
      "timeout": 180,
      "retry_count": 2
    },
    "review_agent": {
      "enabled": true,
      "timeout": 600,
      "retry_count": 2
    },
    "strategy_maintainer": {
      "enabled": true,
      "timeout": 300,
      "retry_count": 2
    }
  },
  "workflow": {
    "pre_market": ["market_analyst", "execution_planner", "risk_controller"],
    "post_market": ["review_agent", "strategy_maintainer"],
    "weekly_review": ["review_agent", "strategy_maintainer", "risk_controller"]
  },
  "data_sources": {
    "primary": "tencent_api",
    "backup": ["sina_api", "eastmoney_api"]
  }
}
```

---

## 三、使用方法

### 1. 盘前分析

**命令**：
```bash
cd ~/.hermes/virtual-trader/agents
python coordinator.py --workflow pre_market
```

**输出示例**：
```
📊 盘前分析报告
市场基调: 中性
最强板块: 消费电子 (+2.49%)
风险信号: 1个

📈 交易计划
1. main账户: buy 长江电力 (600900)
   理由: 符合价值趋势混合策略入场条件

⚠️ 风险审查: 拒绝
拒绝理由:
  ❌ main账户前3大持仓占比68.4%超过限制50.0%

🎯 今日目标: 无交易，观察市场
```

### 2. 盘后复盘

**命令**：
```bash
cd ~/.hermes/virtual-trader/agents
python coordinator.py --workflow post_market
```

**输出示例**：
```
📊 盘后复盘报告
📈 main账户: +1,234元
📈 lab账户: +567元
今日交易: 2笔

有效做法:
  ✅ 今日执行了2笔交易

失败做法:
  ❌ main账户格力电器仓位11.1%超过限制10.0%

🔧 策略更新:
  无调整

📝 Changelog: 0条记录
```

### 3. 周末复盘

**命令**：
```bash
cd ~/.hermes/virtual-trader/agents
python coordinator.py --workflow weekly_review
```

---

## 四、工作流详解

### 1. 盘前工作流

**步骤1: Market Analyst Agent**
- 分析宏观经济环境
- 分析大盘指数走势
- 分析板块轮动情况
- 识别市场状态（牛市/熊市/震荡）
- 识别风险信号

**步骤2: Execution Planner Agent**
- 基于市场分析生成交易计划
- 检查入场条件
- 设置仓位建议
- 生成网格交易设置（如适用）

**步骤3: Risk Controller Agent**
- 验证交易计划
- 检查仓位限制
- 检查止损规则
- 检查集中度限制
- 检查交易有效性
- 输出决策：APPROVED/REJECTED/MODIFY

**最终输出**：
- 如果APPROVED → 输出交易计划
- 如果MODIFY → 输出修改建议
- 如果REJECTED → 输出拒绝理由

### 2. 盘后工作流

**步骤1: Review Agent**
- 加载今日数据（交易记录、日报、账户）
- 分析绩效（收益率、归因）
- 分析交易（买卖统计）
- 识别错误（仓位超限、止损未执行）
- 提取经验教训

**步骤2: Strategy Maintainer Agent**
- 分析策略绩效
- 生成调整建议
- 应用调整（最多3个/天）
- 更新changelog
- 生成更新报告

**最终输出**：
- 复盘摘要
- 策略更新（如有）
- Changelog条目（如有）

---

## 五、全局规则

### 1. 执行规则

```python
execution_rules = {
    "mandatory": [
        "不跳过任何步骤",
        "不合并任何角色",
        "不生成不必要的交易",
        "无行动优于低信心行动",
        "始终尊重仓位限制"
    ],
    "preferred": [
        "优先考虑可逆的更改",
        "添加错误处理",
        "破坏性操作前确认",
        "深入思考后再决策"
    ]
}
```

### 2. 输出风格

```python
output_style = {
    "language": "中文",
    "format": "Markdown",
    "characteristics": [
        "简洁",
        "可执行",
        "数据驱动",
        "逻辑清晰"
    ]
}
```

### 3. 失败模式预防

```python
failure_prevention = {
    "avoid": [
        "过度交易",
        "盲目追涨",
        "忽视止损规则",
        "无市场背景的决策",
        "情绪化交易",
        "过度自信",
        "忽视风险"
    ],
    "prevent": [
        "强制风控检查",
        "多Agent制衡",
        "流程标准化",
        "错误日志记录"
    ]
}
```

---

## 六、与现有系统集成

### 1. Cron任务集成

**盘前分析**（08:10北京）：
```bash
# 添加到cron
10 2 * * 1-5 cd /Users/zhuosama/.hermes/virtual-trader/agents && python coordinator.py --workflow pre_market >> /tmp/pre_market.log 2>&1
```

**盘后复盘**（15:30北京）：
```bash
# 添加到cron
30 9 * * 1-5 cd /Users/zhuosama/.hermes/virtual-trader/agents && python coordinator.py --workflow post_market >> /tmp/post_market.log 2>&1
```

**周末复盘**（周六08:00北京）：
```bash
# 添加到cron
0 2 * * 6 cd /Users/zhuosama/.hermes/virtual-trader/agents && python coordinator.py --workflow weekly_review >> /tmp/weekly_review.log 2>&1
```

### 2. 数据集成

**输入数据**：
- 账户数据：`~/.hermes/virtual-trader/accounts/`
- 策略配置：`~/.hermes/virtual-trader/strategies/`
- 交易记录：`~/.hermes/virtual-trader/trades/`
- 关注池：`~/.hermes/virtual-trader/market-data/watchlist.json`

**输出数据**：
- 分析报告：`~/.hermes/virtual-trader/agents/reports/`
- 交易计划：`~/.hermes/virtual-trader/agents/plans/`
- 验证结果：`~/.hermes/virtual-trader/agents/validations/`
- 复盘报告：`~/.hermes/virtual-trader/agents/reviews/`
- 策略更新：`~/.hermes/virtual-trader/agents/updates/`
- 工作流结果：`~/.hermes/virtual-trader/agents/workflows/`

---

## 七、故障排除

### 1. 常见问题

**问题1：Agent初始化失败**
```bash
# 检查配置文件
cat ~/.hermes/virtual-trader/agents/config.json

# 检查依赖
python -c "import json, os, datetime"
```

**问题2：数据获取失败**
```bash
# 检查网络连接
curl -s "http://qt.gtimg.cn/q=sh600036"

# 检查数据目录权限
ls -la ~/.hermes/virtual-trader/
```

**问题3：工作流执行失败**
```bash
# 查看详细日志
python coordinator.py --workflow pre_market 2>&1 | tee /tmp/debug.log

# 检查各个Agent
python market_analyst.py
python execution_planner.py
python risk_controller.py
```

### 2. 调试技巧

**启用详细日志**：
```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

**单独测试Agent**：
```bash
# 测试市场分析
python market_analyst.py

# 测试交易计划
python execution_planner.py

# 测试风控
python risk_controller.py
```

**查看生成的文件**：
```bash
# 查看分析报告
ls -la ~/.hermes/virtual-trader/agents/reports/

# 查看交易计划
ls -la ~/.hermes/virtual-trader/agents/plans/

# 查看验证结果
ls -la ~/.hermes/virtual-trader/agents/validations/
```

---

## 八、优化与扩展

### 1. 性能优化

**数据缓存**：
- 实现数据缓存机制
- 减少重复API调用
- 优化数据获取速度

**并行执行**：
- Agent并行执行（如适用）
- 异步I/O操作
- 多线程数据获取

**内存优化**：
- 优化数据结构
- 及时释放内存
- 避免内存泄漏

### 2. 功能扩展

**新增Agent**：
- 新闻分析Agent
- 资金流向Agent
- 情绪分析Agent
- 宏观预测Agent

**新增工作流**：
- 盘中监控工作流
- 紧急响应工作流
- 回测验证工作流
- 策略优化工作流

**新增数据源**：
- 更多财经API
- 社交媒体数据
- 新闻数据
- 宏观数据

### 3. 智能化升级

**机器学习集成**：
- 预测模型
- 分类模型
- 聚类模型
- 强化学习

**自然语言处理**：
- 新闻情感分析
- 研报分析
- 社交媒体分析

**知识图谱**：
- 公司关系图谱
- 行业关系图谱
- 概念关系图谱

---

## 九、总结

### 1. 系统优势

**专业分工**：
- 每个Agent专注自己的领域
- 提高分析质量和效率
- 减少错误和遗漏

**相互制衡**：
- 风控Agent强制检查
- 防止过度交易
- 降低操作风险

**流程标准化**：
- 执行流程固定
- 输出格式统一
- 便于监控和优化

**可追溯性**：
- 所有决策有记录
- 便于复盘和优化
- 支持审计和合规

### 2. 预期收益

**效率提升**：
- 盘前分析：15-20分钟 → 5-10分钟
- 盘后复盘：20-30分钟 → 10-15分钟
- 策略迭代：手动 → 自动化

**质量提升**：
- 分析更全面
- 决策更理性
- 风险控制更严格

**风险降低**：
- 减少人为错误
- 防止过度交易
- 提高止损纪律

### 3. 下一步行动

**立即行动**：
1. ✅ 系统已创建并测试
2. [ ] 集成到Cron任务
3. [ ] 监控运行状态
4. [ ] 优化性能

**短期优化**：
1. 完善Agent功能
2. 优化数据获取
3. 增强错误处理
4. 改进输出格式

**长期发展**：
1. 智能化升级
2. 功能扩展
3. 性能优化
4. 生态建设

---

**指南版本**：v1.0  
**创建时间**：2026-04-22  
**维护人员**：Hermes Agent  
**下一步**：集成到Cron任务，开始实际使用