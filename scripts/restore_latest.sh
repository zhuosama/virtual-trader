#!/bin/bash
# 虚拟盘数据快速恢复脚本
# 用途：从最新备份恢复数据

BACKUP_DIR="$HOME/.hermes/virtual-trader/backups"
VT_DIR="$HOME/.hermes/virtual-trader"

echo "=== 虚拟盘数据快速恢复 ==="

# 查找最新备份
LATEST_BACKUP=$(ls -t "$BACKUP_DIR" | head -1)

if [ -z "$LATEST_BACKUP" ]; then
    echo "❌ 没有找到备份文件"
    exit 1
fi

BACKUP_PATH="$BACKUP_DIR/$LATEST_BACKUP"
echo "找到最新备份: $LATEST_BACKUP"
echo "备份时间: $(ls -l "$BACKUP_PATH" | awk '{print $6, $7, $8}')"

# 确认恢复
read -p "确认从该备份恢复？(y/N): " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "取消恢复"
    exit 0
fi

# 恢复文件
echo "开始恢复..."

# 恢复账户数据
if [ -f "$BACKUP_PATH/accounts/main.json" ]; then
    cp "$BACKUP_PATH/accounts/main.json" "$VT_DIR/accounts/"
    echo "✅ 恢复: accounts/main.json"
fi

if [ -f "$BACKUP_PATH/accounts/lab.json" ]; then
    cp "$BACKUP_PATH/accounts/lab.json" "$VT_DIR/accounts/"
    echo "✅ 恢复: accounts/lab.json"
fi

# 恢复策略数据
if [ -f "$BACKUP_PATH/strategies/active.json" ]; then
    cp "$BACKUP_PATH/strategies/active.json" "$VT_DIR/strategies/"
    echo "✅ 恢复: strategies/active.json"
fi

if [ -f "$BACKUP_PATH/strategies/changelog.json" ]; then
    cp "$BACKUP_PATH/strategies/changelog.json" "$VT_DIR/strategies/"
    echo "✅ 恢复: strategies/changelog.json"
fi

# 恢复关注池
if [ -f "$BACKUP_PATH/market-data/watchlist.json" ]; then
    cp "$BACKUP_PATH/market-data/watchlist.json" "$VT_DIR/market-data/"
    echo "✅ 恢复: market-data/watchlist.json"
fi

echo
echo "=== 恢复完成 ==="
echo "请运行完整性检查: ./scripts/check_integrity.sh"
echo "如需查看账户状态: cat $VT_DIR/accounts/main.json | jq ."
