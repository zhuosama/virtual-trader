#!/bin/bash
# 虚拟盘数据备份脚本
# 用途：定期备份虚拟盘关键数据，防止数据丢失

BACKUP_DIR="$HOME/.hermes/virtual-trader/backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_SUBDIR="$BACKUP_DIR/backup_$TIMESTAMP"

# 创建备份目录
mkdir -p "$BACKUP_SUBDIR/accounts"
mkdir -p "$BACKUP_SUBDIR/strategies"
mkdir -p "$BACKUP_SUBDIR/market-data"

# 备份账户数据
cp "$HOME/.hermes/virtual-trader/accounts/main.json" "$BACKUP_SUBDIR/accounts/" 2>/dev/null || echo "main.json not found"
cp "$HOME/.hermes/virtual-trader/accounts/lab.json" "$BACKUP_SUBDIR/accounts/" 2>/dev/null || echo "lab.json not found"

# 备份策略数据
cp "$HOME/.hermes/virtual-trader/strategies/active.json" "$BACKUP_SUBDIR/strategies/" 2>/dev/null || echo "active.json not found"
cp "$HOME/.hermes/virtual-trader/strategies/changelog.json" "$BACKUP_SUBDIR/strategies/" 2>/dev/null || echo "changelog.json not found"

# 备份关注池
cp "$HOME/.hermes/virtual-trader/market-data/watchlist.json" "$BACKUP_SUBDIR/market-data/" 2>/dev/null || echo "watchlist.json not found"

# 创建备份元数据
cat > "$BACKUP_SUBDIR/backup_meta.json" << EOF
{
    "timestamp": "$TIMESTAMP",
    "backup_time": "$(date -Iseconds)",
    "reason": "定期备份",
    "files_backed_up": [
        "accounts/main.json",
        "accounts/lab.json",
        "strategies/active.json",
        "strategies/changelog.json",
        "market-data/watchlist.json"
    ]
}
EOF

# 清理旧备份（保留最近10个）
cd "$BACKUP_DIR"
ls -t | tail -n +11 | xargs -r rm -rf

echo "备份完成: $BACKUP_SUBDIR"
echo "备份文件数: 5"
echo "当前备份数: $(ls -1 | wc -l)"
