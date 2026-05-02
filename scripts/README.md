# 虚拟盘管理脚本

## 脚本说明

### 1. backup.sh - 数据备份
**用途**：定期备份虚拟盘关键数据
**使用**：`./scripts/backup.sh`
**备份内容**：
- accounts/main.json
- accounts/lab.json
- strategies/active.json
- strategies/changelog.json
- market-data/watchlist.json

**备份策略**：
- 保留最近10个备份
- 备份目录：~/.hermes/virtual-trader/backups/

### 2. check_integrity.sh - 数据完整性检查
**用途**：检查账户文件是否完整
**使用**：`./scripts/check_integrity.sh`
**检查内容**：
- 文件是否存在
- 文件大小是否合理
- JSON格式是否正确
- 账户摘要信息

### 3. restore_latest.sh - 快速恢复
**用途**：从最新备份恢复数据
**使用**：`./scripts/restore_latest.sh`
**恢复内容**：
- 从最新备份恢复所有关键文件
- 需要确认操作

## 定期任务建议

### 1. 每日备份
建议在每日收盘后运行备份：
```bash
# 添加到crontab
0 16 * * 1-5 /Users/zhuosama/.hermes/virtual-trader/scripts/backup.sh
```

### 2. 每周检查
建议每周运行完整性检查：
```bash
# 添加到crontab
0 9 * * 1 /Users/zhuosama/.hermes/virtual-trader/scripts/check_integrity.sh
```

### 3. 紧急恢复
当发现数据丢失时：
```bash
# 运行快速恢复
./scripts/restore_latest.sh

# 检查恢复结果
./scripts/check_integrity.sh
```

## 数据目录结构

```
~/.hermes/virtual-trader/
├── accounts/           # 账户数据
├── strategies/         # 策略数据
├── market-data/        # 市场数据
├── trades/             # 交易记录
├── reports/            # 分析报告
├── backups/            # 备份目录
│   └── backup_YYYYMMDD_HHMMSS/
└── scripts/            # 管理脚本
    ├── backup.sh
    ├── check_integrity.sh
    └── restore_latest.sh
```

## 注意事项

1. **备份频率**：建议每日备份，重要操作前手动备份
2. **恢复谨慎**：恢复操作会覆盖当前数据，请确认后再操作
3. **检查定期**：建议每周运行完整性检查，及时发现数据问题
4. **日志记录**：所有操作都会记录在changelog.json中

## 故障处理

### 数据丢失
1. 运行 `./scripts/restore_latest.sh` 从最新备份恢复
2. 运行 `./scripts/check_integrity.sh` 检查恢复结果
3. 检查changelog.json确认数据状态

### 文件损坏
1. 运行 `./scripts/check_integrity.sh` 定位问题
2. 从备份恢复损坏的文件
3. 检查JSON格式是否正确

### 备份失败
1. 检查备份目录权限
2. 检查磁盘空间
3. 手动运行备份脚本查看错误信息