"""
微信日报组装 + 发送。
通道:notify_target。默认 local:写本地 Markdown 文件,不调用 hermes send。
  local 输出: $VTRADER_HOME/logs/notify/us_trader/YYYY-MM-DD_HH-MM-SS.md
  (VTRADER_HOME 默认 ~/.hermes/virtual-trader)
显式配置 notify_target=wecom(或其他 hermes send 目标)时仍走 hermes send --to <target>。
优先级:环境变量 VTRADER_NOTIFY_TARGET > config["notify_target"] > "local"。
"""
import logging
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)

_HERMES_BIN = str(Path("~/.local/bin/hermes").expanduser())
LOCAL_TARGET = "local"


def resolve_target(config: dict) -> str:
    return (os.environ.get("VTRADER_NOTIFY_TARGET") or config.get("notify_target")
            or LOCAL_TARGET).strip()


def local_notify_dir() -> Path:
    home = os.environ.get("VTRADER_HOME", str(Path("~/.hermes/virtual-trader").expanduser()))
    return Path(home) / "logs" / "notify" / "us_trader"


def write_local(subject: str, body: str) -> Path:
    d = local_notify_dir()
    d.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    path = d / f"{now:%Y-%m-%d_%H-%M-%S}.md"
    n = 1
    while path.exists():
        path = d / f"{now:%Y-%m-%d_%H-%M-%S}_{n}.md"
        n += 1
    path.write_text(f"# {subject}\n\n**Time:** {now:%Y-%m-%d %H:%M:%S}\n\n---\n\n{body}\n",
                    encoding="utf-8")
    return path


def build_digest(
    state: dict,
    trades: List[dict],
    selection: List[dict],
    health: dict,
    date: str,
) -> str:
    """
    组装每日 Markdown 微信日报(设计 §6 五段)。

    Args:
        state: 当前账本状态(cash, positions, nav_history)
        trades: 今日成交记录列表
        selection: 选股管线输出列表
        health: health dict(success, failed_step, error)
        date: YYYYMMDD

    Returns:
        Markdown 字符串
    """
    lines = []

    # ── 段1:昨夜复盘 ──────────────────────────────────────────────────────
    lines.append(f"## 📊 昨夜复盘 — {date}")
    nav_hist = state.get("nav_history", [])
    if nav_hist:
        last = nav_hist[-1]
        nav = last.get("nav", 0)
        ret = last.get("ret", 0)
        cum_ret = last.get("cum_ret", 0)
        drawdown = last.get("drawdown", 0)
        lines.append(f"- 组合净值: ${nav:,.2f}")
        lines.append(f"- 日涨跌: {ret*100:+.2f}%")
        lines.append(f"- 累计收益: {cum_ret*100:+.2f}%")
        lines.append(f"- 最大回撤: {drawdown*100:.2f}%")
    else:
        lines.append("- 暂无净值数据")
    lines.append("")

    # ── 段2:当前持仓 ──────────────────────────────────────────────────────
    lines.append("## 📋 当前持仓")
    positions = state.get("positions", {})
    if positions:
        for code, pos in positions.items():
            shares = pos.get("shares", 0)
            cost = pos.get("cost", 0)
            hw = pos.get("high_watermark", cost)
            lines.append(f"- **{code}**: {shares}股 @ 成本${cost:.2f} | 高水位${hw:.2f}")
    else:
        lines.append("- 当前无持仓")
    lines.append(f"- 现金: ${state.get('cash', 0):,.2f}")
    lines.append("")

    # ── 段3:今日变动 ──────────────────────────────────────────────────────
    lines.append("## 🔄 今日变动")
    if trades:
        buys = [t for t in trades if t.get("side") == "buy"]
        sells = [t for t in trades if t.get("side") == "sell"]
        if buys:
            lines.append("**买入:**")
            for t in buys:
                lines.append(f"  - {t['code']} ×{t['shares']}股 @ ${t['price']:.2f}(原因:{t.get('reason','')})")
        if sells:
            lines.append("**卖出:**")
            for t in sells:
                pnl = t.get("realized_pnl", 0)
                lines.append(f"  - {t['code']} ×{t['shares']}股 @ ${t['price']:.2f} | 实现盈亏:${pnl:+.2f}(原因:{t.get('reason','')})")
    else:
        lines.append("- 今日无交易")
    lines.append("")

    # ── 段4:今日 top N 选股 ────────────────────────────────────────────────
    lines.append("## 🎯 今日选股")
    passed = [r for r in selection if r.get("passed")]
    if passed:
        for i, r in enumerate(passed[:10], 1):
            score = r.get("momentum_score")
            score_str = f"{score:.4f}" if score is not None else "N/A"
            lines.append(f"{i}. **{r['ts_code']}** — 动量分:{score_str}")
    else:
        lines.append("- 今日无入选票")
    lines.append("")

    # ── 段5:风险提示 ─────────────────────────────────────────────────────
    lines.append("## ⚠️ 风险提示")
    if not health.get("success", True):
        failed_step = health.get("failed_step", "unknown")
        error = health.get("error", "")
        lines.append(f"- ❌ **数据 DEGRADED**:步骤 `{failed_step}` 失败 — {error}")
    else:
        lines.append("- 系统运行正常")
    # 熔断状态
    from us_trader.pipeline.risk import portfolio_halted
    from us_trader.config import load_config
    try:
        cfg = load_config()
        if portfolio_halted(state.get("nav_history", []), cfg):
            lines.append("- 🔴 **组合熔断触发**:当前回撤超阈值,禁止新开仓")
    except Exception:
        pass
    lines.append("")
    lines.append("_以上均为本地模拟,零真实资金。_")

    return "\n".join(lines)


def send_notify(subject: str, body: str, config: dict) -> bool:
    """
    按 resolve_target() 投递:local 写本地文件;其他目标调用 hermes send --to <target>。
    检查 returncode,非 0 时记 warning 并返回 False(不吞错误)。
    异常不抛,返回 False。

    Args:
        subject: 消息标题
        body: 消息正文(Markdown)
        config: 配置,使用 config["notify_target"]

    Returns:
        True 表示发送成功,False 表示失败
    """
    target = resolve_target(config)
    if target == LOCAL_TARGET:
        try:
            path = write_local(subject, body)
            logger.info("send_notify: wrote local %s", path)
            return True
        except Exception as exc:
            logger.warning("send_notify: local write failed: %s", exc)
            return False
    try:
        result = subprocess.run(
            [_HERMES_BIN, "send", "--to", target, "--subject", subject],
            input=body,
            text=True,
            capture_output=True,
        )
        if result.returncode != 0:
            logger.warning(
                "send_notify: hermes returned code %d, stderr=%s",
                result.returncode,
                result.stderr,
            )
            return False
        return True
    except Exception as exc:
        logger.warning("send_notify: exception: %s", exc)
        return False
