#!/usr/bin/env python3
"""
虚拟盘绩效图表生成脚本
生成4张PNG图表，保存到指定输出目录
"""
import json
import os
import sys
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')

VT_DIR = os.environ.get("VTRADER_HOME", os.path.expanduser("~/.hermes/virtual-trader"))

def load_json(path):
    with open(path, 'r') as f:
        return json.load(f)

def generate_charts(output_dir=None):
    """生成全部图表，返回生成的文件路径列表"""
    import matplotlib
    matplotlib.use('Agg')  # 非交互模式
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    from matplotlib import font_manager
    
    # 尝试使用中文字体
    plt.rcParams['font.sans-serif'] = ['Arial Unicode MS', 'SimHei', 'DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False
    
    if output_dir is None:
        output_dir = os.path.join(VT_DIR, "reports", "charts")
    os.makedirs(output_dir, exist_ok=True)
    
    # 加载数据
    perf_path = os.path.join(VT_DIR, "strategies", "performance_history.json")
    if not os.path.exists(perf_path):
        print("ERROR: performance_history.json not found", file=sys.stderr)
        return []
    
    history = load_json(perf_path)
    if len(history) == 0:
        print("ERROR: performance_history.json is empty", file=sys.stderr)
        return []
    
    main_acct = load_json(os.path.join(VT_DIR, "accounts", "main.json"))
    lab_acct = load_json(os.path.join(VT_DIR, "accounts", "lab.json"))
    
    # 解析数据
    dates = [datetime.strptime(h["date"], "%Y-%m-%d") for h in history]
    main_pct = [h["main_pct"] for h in history]
    lab_pct = [h["lab_pct"] for h in history]
    hs300_pct = [h["hs300_pct"] for h in history]
    
    # 计算累计收益
    cum_main = []
    cum_lab = []
    cum_hs300 = []
    cm, cl, ch = 0, 0, 0
    for m, l, h in zip(main_pct, lab_pct, hs300_pct):
        cm += m
        cl += l
        ch += h
        cum_main.append(cm)
        cum_lab.append(cl)
        cum_hs300.append(ch)
    
    generated_files = []
    
    # === 图表1：累计收益曲线 ===
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(dates, cum_main, 'b-o', label='Main Account', linewidth=2, markersize=4)
    ax.plot(dates, cum_lab, 'r-s', label='Lab Account', linewidth=2, markersize=4)
    ax.plot(dates, cum_hs300, 'g--^', label='CSI 300', linewidth=1.5, markersize=3)
    ax.axhline(y=0, color='gray', linestyle='-', linewidth=0.5)
    ax.set_title('Cumulative Returns (%)', fontsize=14, fontweight='bold')
    ax.set_xlabel('Date')
    ax.set_ylabel('Return (%)')
    ax.legend(loc='best')
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d'))
    ax.xaxis.set_major_locator(mdates.DayLocator(interval=2))
    plt.xticks(rotation=45)
    plt.tight_layout()
    path1 = os.path.join(output_dir, "cumulative_returns.png")
    fig.savefig(path1, dpi=150, bbox_inches='tight')
    plt.close(fig)
    generated_files.append(path1)
    
    # === 图表2：每日盈亏柱状图 ===
    fig, ax = plt.subplots(figsize=(10, 5))
    x = range(len(dates))
    width = 0.35
    colors_main = ['#2ecc71' if v >= 0 else '#e74c3c' for v in main_pct]
    colors_lab = ['#27ae60' if v >= 0 else '#c0392b' for v in lab_pct]
    bars1 = ax.bar([i - width/2 for i in x], main_pct, width, label='Main', color=colors_main, alpha=0.8)
    bars2 = ax.bar([i + width/2 for i in x], lab_pct, width, label='Lab', color=colors_lab, alpha=0.8)
    ax.axhline(y=0, color='gray', linestyle='-', linewidth=0.5)
    ax.set_title('Daily P&L (%)', fontsize=14, fontweight='bold')
    ax.set_xlabel('Date')
    ax.set_ylabel('Daily Return (%)')
    ax.set_xticks(list(x))
    ax.set_xticklabels([d.strftime('%m/%d') for d in dates], rotation=45)
    ax.legend()
    ax.grid(True, alpha=0.3, axis='y')
    plt.tight_layout()
    path2 = os.path.join(output_dir, "daily_pnl.png")
    fig.savefig(path2, dpi=150, bbox_inches='tight')
    plt.close(fig)
    generated_files.append(path2)
    
    # === 图表3：持仓分布 ===
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))
    
    for ax, acct, title in [(ax1, main_acct, 'Main Account'), (ax2, lab_acct, 'Lab Account')]:
        positions = acct.get("positions", [])
        cash = acct.get("cash", 0)
        
        labels = [p["name"] for p in positions] + ["Cash"]
        values = [p["market_value"] for p in positions] + [cash]
        
        colors = plt.cm.Set3(range(len(labels)))
        wedges, texts, autotexts = ax.pie(values, labels=labels, autopct='%1.1f%%', 
                                           colors=colors, startangle=90, pctdistance=0.85)
        for text in texts:
            text.set_fontsize(8)
        for autotext in autotexts:
            autotext.set_fontsize(7)
        ax.set_title(f'{title}\nTotal: {acct["total_value"]:,.0f}', fontsize=12, fontweight='bold')
    
    plt.tight_layout()
    path3 = os.path.join(output_dir, "position_distribution.png")
    fig.savefig(path3, dpi=150, bbox_inches='tight')
    plt.close(fig)
    generated_files.append(path3)
    
    # === 图表4：回撤趋势 ===
    fig, ax = plt.subplots(figsize=(10, 5))
    
    # 计算回撤（基于累计收益的峰值回撤）
    for cum, label, color in [(cum_main, 'Main', 'blue'), (cum_lab, 'Lab', 'red')]:
        peak = cum[0]
        drawdown = []
        for v in cum:
            if v > peak:
                peak = v
            drawdown.append(v - peak)
        ax.fill_between(dates, drawdown, 0, alpha=0.3, color=color, label=label)
        ax.plot(dates, drawdown, color=color, linewidth=1)
    
    ax.set_title('Drawdown from Peak (%)', fontsize=14, fontweight='bold')
    ax.set_xlabel('Date')
    ax.set_ylabel('Drawdown (%)')
    ax.legend()
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d'))
    plt.xticks(rotation=45)
    plt.tight_layout()
    path4 = os.path.join(output_dir, "drawdown.png")
    fig.savefig(path4, dpi=150, bbox_inches='tight')
    plt.close(fig)
    generated_files.append(path4)
    
    print(f"Generated {len(generated_files)} charts in {output_dir}")
    for f in generated_files:
        print(f"  - {os.path.basename(f)}")
    
    return generated_files


def upload_charts(file_paths):
    """Upload chart files to catbox.moe and return URLs"""
    import subprocess
    urls = {}
    for path in file_paths:
        name = os.path.basename(path).replace(".png", "")
        try:
            r = subprocess.run(
                ["curl", "-s", "-m", "30", "-F", "reqtype=fileupload",
                 "-F", f"fileToUpload=@{path}",
                 "https://catbox.moe/user/api.php"],
                capture_output=True, timeout=35
            )
            url = r.stdout.decode().strip()
            if url.startswith("https://"):
                urls[name] = url
                print(f"  ✅ {name}: {url}")
            else:
                print(f"  ❌ {name}: upload failed")
        except Exception as e:
            print(f"  ❌ {name}: {e}")
    return urls


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    output = args[0] if args else None
    files = generate_charts(output)
    
    if files and "--upload" in sys.argv:
        print("\nUploading to catbox.moe...")
        urls = upload_charts(files)
        if urls:
            print("\nChart URLs:")
            for name, url in urls.items():
                print(f"  {name}: {url}")
