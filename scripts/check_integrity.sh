#!/bin/bash
# 虚拟盘数据完整性检查脚本
# 用途：检查账户文件是否完整，防止数据丢失

VT_DIR="$HOME/.hermes/virtual-trader"
ERRORS=0

echo "=== 虚拟盘数据完整性检查 ==="
echo "检查时间: $(date)"
echo

# 检查关键文件是否存在
files=(
    "accounts/main.json"
    "accounts/lab.json"
    "strategies/active.json"
    "strategies/changelog.json"
    "market-data/watchlist.json"
)

for file in "${files[@]}"; do
    filepath="$VT_DIR/$file"
    if [ -f "$filepath" ]; then
        # 检查文件大小
        size=$(stat -f%z "$filepath" 2>/dev/null || stat -c%s "$filepath" 2>/dev/null)
        if [ "$size" -lt 100 ]; then
            echo "⚠️  文件过小: $file (${size}字节)"
            ERRORS=$((ERRORS+1))
        else
            echo "✅ $file (${size}字节)"
        fi
        
        # 检查JSON格式
        if [[ "$file" == *.json ]]; then
            if ! jq empty "$filepath" 2>/dev/null; then
                echo "❌ JSON格式错误: $file"
                ERRORS=$((ERRORS+1))
            fi
        fi
    else
        echo "❌ 文件不存在: $file"
        ERRORS=$((ERRORS+1))
    fi
done

echo
echo "=== 检查结果 ==="
if [ $ERRORS -eq 0 ]; then
    echo "✅ 所有文件检查通过"
else
    echo "❌ 发现 $ERRORS 个问题"
    echo "建议运行备份恢复: ./scripts/restore_latest.sh"
fi

echo
echo "=== 账户摘要 ==="
if [ -f "$VT_DIR/accounts/main.json" ]; then
    main_total=$(jq -r '.total_value // "N/A"' "$VT_DIR/accounts/main.json")
    main_positions=$(jq -r '.positions | length' "$VT_DIR/accounts/main.json")
    echo "主账户: $main_total元, $main_positions只持仓"
fi

if [ -f "$VT_DIR/accounts/lab.json" ]; then
    lab_total=$(jq -r '.total_value // "N/A"' "$VT_DIR/accounts/lab.json")
    lab_positions=$(jq -r '.positions | length' "$VT_DIR/accounts/lab.json")
    echo "实验账户: $lab_total元, $lab_positions只持仓"
fi
