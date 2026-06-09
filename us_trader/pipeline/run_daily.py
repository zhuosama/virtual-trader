"""
每日编排入口:fetch → select → risk → simulate → notify → health。
失败走 health + 微信告警,mirror dashboard-sync 设计。
"""
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from us_trader.pipeline import fetch_prices, fetch_fundamentals, notify
from us_trader.pipeline.risk import position_exit_signals, portfolio_halted, update_watermark
from us_trader.pipeline.simulate import step
from us_trader.pipeline.universe import load_universe

logger = logging.getLogger(__name__)

# 运行时状态目录(可被 monkeypatch 覆盖用于测试)
_MODULE_DIR = Path(__file__).resolve().parent.parent  # us_trader/
STATE_DIR = str(_MODULE_DIR / "state")


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def _ensure_state_dir():
    Path(STATE_DIR).mkdir(parents=True, exist_ok=True)


def write_health(success: bool, failed_step: str = None, error: str = None):
    """写 state/health.json。"""
    _ensure_state_dir()
    health = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "success": success,
        "failed_step": failed_step,
        "error": error,
    }
    path = os.path.join(STATE_DIR, "health.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(health, f, indent=2)
    return health


def _read_health() -> Optional[dict]:
    """读上轮 health.json;不存在返回 None。"""
    path = os.path.join(STATE_DIR, "health.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _load_state(config: dict) -> dict:
    """从 state/positions.json + nav_history.json 加载账本;文件不存在则初始化。"""
    _ensure_state_dir()
    positions = {}
    nav_history = []

    pos_path = os.path.join(STATE_DIR, "positions.json")
    if os.path.exists(pos_path):
        try:
            with open(pos_path, "r", encoding="utf-8") as f:
                positions = json.load(f)
        except Exception as exc:
            logger.warning("_load_state: failed to read positions.json: %s", exc)

    nav_path = os.path.join(STATE_DIR, "nav_history.json")
    if os.path.exists(nav_path):
        try:
            with open(nav_path, "r", encoding="utf-8") as f:
                nav_history = json.load(f)
        except Exception as exc:
            logger.warning("_load_state: failed to read nav_history.json: %s", exc)

    # cash 必须显式持久化读取,不能用 nav 反推(nav 含未实现盈亏,反推会虚增 cash)。
    cash = None
    acc_path = os.path.join(STATE_DIR, "account.json")
    if os.path.exists(acc_path):
        try:
            with open(acc_path, "r", encoding="utf-8") as f:
                cash = json.load(f).get("cash")
        except Exception as exc:
            logger.warning("_load_state: failed to read account.json: %s", exc)
    if cash is None:
        cash = config["initial_capital"]

    return {
        "cash": cash,
        "positions": positions,
        "nav_history": nav_history,
    }


def _save_state(state: dict, trades: List[dict], date: str):
    """持久化 positions / nav_history / trades。"""
    _ensure_state_dir()

    # account.json:显式持久化 cash(供下一交易日重载,避免 nav 反推虚增)
    with open(os.path.join(STATE_DIR, "account.json"), "w", encoding="utf-8") as f:
        json.dump({"cash": state["cash"]}, f, indent=2)

    # positions.json
    with open(os.path.join(STATE_DIR, "positions.json"), "w", encoding="utf-8") as f:
        json.dump(state["positions"], f, indent=2)

    # nav_history.json
    with open(os.path.join(STATE_DIR, "nav_history.json"), "w", encoding="utf-8") as f:
        json.dump(state["nav_history"], f, indent=2)

    # trades/YYYY-MM.jsonl
    if trades:
        month = date[:6]  # YYYYMM
        trades_dir = os.path.join(STATE_DIR, "trades")
        Path(trades_dir).mkdir(parents=True, exist_ok=True)
        trades_path = os.path.join(trades_dir, f"{month}.jsonl")
        with open(trades_path, "a", encoding="utf-8") as f:
            for t in trades:
                f.write(json.dumps(t, ensure_ascii=False) + "\n")


def _select(panel, fundamentals, config, as_of):
    """选股管线封装(用于 monkeypatch)。"""
    from us_trader.pipeline.select import select
    return select(panel, fundamentals, config, as_of)


# ── 主编排 ─────────────────────────────────────────────────────────────────────

def run_daily(date: str = None, config_path: str = None) -> dict:
    """
    执行单日全流程:
    is_trading_day → fetch_prices → fetch_fundamentals → select →
    load_state → risk → simulate → save → notify → write_health。
    任一步失败 → write_health(failed) + 微信告警 → return。

    Args:
        date: YYYYMMDD,默认 None = 今日
        config_path: 可选外部配置路径

    Returns:
        {"skipped": True} 或 {"success": bool, ...}
    """
    from us_trader.config import load_config
    config = load_config(config_path)

    if date is None:
        date = datetime.now().strftime("%Y%m%d")

    # ── 0:判断是否交易日 ──────────────────────────────────────────────────
    try:
        is_open = fetch_prices.is_trading_day(date)
    except Exception as exc:
        logger.warning("run_daily: is_trading_day failed: %s", exc)
        is_open = True  # 默认继续,由后续步骤决定

    if not is_open:
        logger.info("run_daily: %s is not a trading day, skipping", date)
        return {"skipped": True}

    prev_health = _read_health()

    def _alert_and_fail(step_name: str, exc: Exception):
        msg = f"[us-trader] ❌ {step_name}"
        summary = f"{step_name} failed: {exc}"
        notify.send_notify(msg, summary, config)
        write_health(success=False, failed_step=step_name, error=str(exc))

    # ── 1:拉价格面板 ─────────────────────────────────────────────────────
    try:
        universe = load_universe()
        codes = [u["ts_code"] for u in universe]
        # 取最近 130 天(≥lookback_6m 126 + buffer)
        trading_days = fetch_prices.get_trading_days(date, 130)
        start = trading_days[0] if trading_days else date
        panel = fetch_prices.fetch_price_panel(codes, start, date)
    except Exception as exc:
        logger.error("run_daily: fetch_prices failed: %s", exc)
        _alert_and_fail("fetch_prices", exc)
        return {"success": False, "failed_step": "fetch_prices"}

    # ── 2:拉基本面 ───────────────────────────────────────────────────────
    try:
        fundamentals = fetch_fundamentals.fetch_fundamentals(codes)
    except Exception as exc:
        logger.error("run_daily: fetch_fundamentals failed: %s", exc)
        _alert_and_fail("fetch_fundamentals", exc)
        return {"success": False, "failed_step": "fetch_fundamentals"}

    # ── 3:选股 ───────────────────────────────────────────────────────────
    try:
        selection = _select(panel, fundamentals, config, date)
        target_codes = [r["ts_code"] for r in selection if r["passed"]][:config["holdings_n"]]
    except Exception as exc:
        logger.error("run_daily: select failed: %s", exc)
        _alert_and_fail("select", exc)
        return {"success": False, "failed_step": "select"}

    # ── 4:加载账本状态 ───────────────────────────────────────────────────
    try:
        state = _load_state(config)
    except Exception as exc:
        logger.error("run_daily: load_state failed: %s", exc)
        _alert_and_fail("load_state", exc)
        return {"success": False, "failed_step": "load_state"}

    # ── 5:风控信号 ───────────────────────────────────────────────────────
    try:
        # 构建当前价格字典
        prices = {}
        if panel is not None and not panel.empty:
            last_row = panel.iloc[-1]
            for code in panel.columns:
                v = last_row[code]
                if v == v:  # not NaN
                    prices[code] = float(v)

        exit_signals = position_exit_signals(state["positions"], prices, config)
        # 更新 watermark
        state["positions"] = update_watermark(state["positions"], prices)
    except Exception as exc:
        logger.error("run_daily: risk failed: %s", exc)
        _alert_and_fail("risk", exc)
        return {"success": False, "failed_step": "risk"}

    # ── 6:模拟撮合 ───────────────────────────────────────────────────────
    try:
        state, trades = step(state, target_codes, prices, exit_signals, config, date)
    except Exception as exc:
        logger.error("run_daily: simulate failed: %s", exc)
        _alert_and_fail("simulate", exc)
        return {"success": False, "failed_step": "simulate"}

    # ── 7:持久化 ─────────────────────────────────────────────────────────
    try:
        _save_state(state, trades, date)
    except Exception as exc:
        logger.error("run_daily: save_state failed: %s", exc)
        _alert_and_fail("save_state", exc)
        return {"success": False, "failed_step": "save_state"}

    # ── 8:发送微信日报 ────────────────────────────────────────────────────
    try:
        digest = notify.build_digest(state, trades, selection,
                                     {"success": True}, date)
        subject = f"[us-trader] 日报 {date}"
        notify.send_notify(subject, digest, config)
    except Exception as exc:
        logger.error("run_daily: notify failed: %s", exc)
        _alert_and_fail("notify", exc)
        return {"success": False, "failed_step": "notify"}

    # ── 9:写 health + recovered 告警 ──────────────────────────────────────
    health = write_health(success=True)

    # 若上轮失败 → 本轮成功,发 recovered 通知(去重)
    if prev_health and prev_health.get("success") is False:
        notify.send_notify(
            "[us-trader] ✅ recovered",
            f"[us-trader] ✅ recovered — 上轮失败步骤:{prev_health.get('failed_step')} 已恢复",
            config,
        )

    logger.info("run_daily: %s completed successfully", date)
    return {"success": True, "date": date, "trades": len(trades),
            "positions": len(state["positions"])}


if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="us_trader 每日批跑入口")
    parser.add_argument("--date", default=None, help="YYYYMMDD,默认今日")
    parser.add_argument("--config", default=None, help="可选外部配置路径")
    args = parser.parse_args()

    result = run_daily(args.date, args.config)
    print(json.dumps(result, ensure_ascii=False))
