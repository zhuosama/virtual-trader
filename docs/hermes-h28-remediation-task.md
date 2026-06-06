# Hermes H28 修复任务 — H27 Review Remediation

## 上下文

H27 read-only review 发现了 3 个 HIGH / 7 个 MEDIUM 级问题。本任务把可执行的修复项凝固成 spec,目标是:

- 让 `validate()` 和 `data_quality_for_period` 真正反映"全期 / 周期"内 active universe × 价格覆盖的实际情况,而不是端点抽样。
- 把 `scripts/repair_cn_price_coverage.py` 的候选合并、安全检查、报告与回放逻辑纳入单元测试。
- 让 H27 manual-replacement 门也覆盖 column-but-NaN 与中段缺口。

`data/cn_pit/prices.csv` **仍然不可修改**。所有产出仍写候选文件 (`prices_h27_candidate.csv`、`prices_h28_candidate.csv` 等)。

## Files You Own

- `scripts/ingest_cn_pit_data.py`
- `scripts/repair_cn_price_coverage.py`
- `backtest/experiments/fundamental_backtest.py`
- `tests/test_ingest_cn_pit_data.py`
- `tests/test_fundamental_pit_source.py`
- 新增: `tests/test_repair_cn_price_coverage.py`
- 新增: `reports/h28_remediation_report.md`(汇总)

不要碰: `data/cn_pit/{universe,fundamentals}.jsonl`、`data/cn_pit/prices.csv`、`data/cn_pit/universe_snapshots.jsonl`。

## 任务清单

### 1) [H1] `validate()` 全期 `price_coverage` 改成 union-over-checkpoints

**位置:** `scripts/ingest_cn_pit_data.py:1132-1160`

**问题:** 现在只在 `price_start` / `price_end` 两个端点比较 `active_universe ∩ price_cols`,中段(2021-2023)只要某一只票活跃但缺列就漏掉。

**改法(无需引入 pandas,沿用已有 universe rows):**

1. 复用/抽取一个 `_active_at(universe, date)` 与现有 `_interval_active` 同语义。
2. 在 `validate()` 里新增 helper `_checkpoint_dates(price_dates)`:返回 `price_start`, `price_end`, 以及 `price_dates` 中每年最早的一个交易日(对应 `repair_cn_price_coverage.generate_checkpoints` 的逻辑)。
3. 对每个 checkpoint 计算 `active(date) - price_cols`,若任何一个非空 → `data_quality_blockers` 追加 `price_coverage`,并把不达标 checkpoint 列入新字段 `summary["price_coverage_failed_checkpoints"]: List[Dict]`(键 `date`, `active_count`, `covered_count`, `missing_count`, 最多 20 个样例 ticker)。
4. 保留现有 `active_universe_start_count`/`active_price_coverage_start`/`_end` 字段做向后兼容(period 报告里同时存在)。

**验收:**

- 加 test `test_validate_blocks_on_middle_checkpoint_gap`:universe 含一只 2022 活跃但 2020/2026 都已退市的票,prices.csv 起止两端都满覆盖,但 2022 缺该列 → status `BLOCKED`、blockers 含 `price_coverage`、`price_coverage_failed_checkpoints` 至少一项。
- 运行 `python scripts/ingest_cn_pit_data.py --validate` 仍然 `BLOCKED`,数字必须与 H26 报告一致(2020-01-02: 75 missing)。

### 2) [H2] `_qlib_interval_covers` 改成"非空交集 + 起点严格在区间内"

**位置:** `backtest/experiments/fundamental_backtest.py:399-415`

**问题:** `any(active)` 让任何含一条 open-ended interval 的伪造 universe 都能过关。

**改法:**

```python
def _qlib_interval_covers(self, start_date: str, end_date: str) -> bool:
    qlib_rows = [r for r in self._universe_rows
                 if r.get("source_provider") == "qlib:instruments"]
    if not qlib_rows or len(qlib_rows) != len(self._universe_rows):
        return False
    # 最低规模门槛 —— 单行/玩具数据不该清 survivorship_bias
    if len(qlib_rows) < self.MIN_QLIB_UNIVERSE_ROWS:  # 设 200,留 HS300 余量
        return False
    active_at_start = [r for r in qlib_rows if self._active(r, start_date)]
    active_at_end = [r for r in qlib_rows if self._active(r, end_date)]
    if not active_at_start or not active_at_end:
        return False
    # 必须有一条 interval 完全覆盖 [start, end] 而不是仅在端点活跃
    spans_period = any(
        self._active(r, start_date) and self._active(r, end_date)
        for r in qlib_rows
    )
    return spans_period
```

加常量 `MIN_QLIB_UNIVERSE_ROWS = 200`(挂在 class 上,便于测试 monkeypatch)。

**验收:**

- 修改 `test_qlib_interval_evidence_does_not_use_snapshot_max_as_coverage`(`tests/test_ingest_cn_pit_data.py:496-549`)以反映新语义:1 行 universe 现在应当 BLOCKED;并加新 `test_qlib_interval_requires_min_universe_size` 与 `test_qlib_interval_requires_continuous_span`。
- 加 `test_qlib_real_size_universe_passes`:写 ≥200 行 qlib:instruments、其中含跨越 [2025-01-01, 2026-05-18] 的 open-ended interval → PASSED。
- 跑现实数据:`validation_report_2025-01-01_2026-05-18.json` 仍应 `PASSED`(900 行 universe 满足新约束)。

### 3) [H3] 给 `scripts/repair_cn_price_coverage.py` 补单元测试

**新文件:** `tests/test_repair_cn_price_coverage.py`

最低必须覆盖:

| 测试 | 行为 |
|---|---|
| `test_generate_checkpoints_snaps_non_trading` | Jan 2 落在周末时回到下一个交易日 |
| `test_active_tickers_on_handles_open_interval` | `end_date = ""` 视为活跃至今 |
| `test_merge_candidate_preserves_original_columns_and_counts` | 合并后所有原列保留、`notna().sum()` 不减少 |
| `test_merge_candidate_warns_on_out_of_range_dates` | 见 M2,验证 warnings 列表非空且 `out_of_range_added` 字段记录被丢弃的行数 |
| `test_candidate_safety_checks_detects_dropped_column` | 删一列 → `passed=False` 且 `missing_existing_cols` 准确 |
| `test_candidate_safety_checks_detects_reduced_nonnull` | 删某列若干行 → `reduced_nonnull_existing_cols` 包含该列 |
| `test_inspect_existing_candidate_matches_safety` | 两条路径在同一对 (orig, cand) 上结论一致(M6 防回归) |
| `test_run_incremental_backfill_uses_candidate_as_base` | mock fetcher,先存 h26 候选,确认 h27 从 h26 候选起步 |
| `test_run_incremental_backfill_never_recommends_when_middle_gap` | mock 出 first/last 满覆盖但中段缺列 → `manual_replacement_recommended=False` |
| `test_analyze_coverage_target_period_obeys_cli` | 见 M4,传 `--start 2024-01-01 --end 2024-12-31` 后 `target_period.requested` 应为该窗口 |
| `test_fetch_missing_tickers_yfinance_failure_falls_through_to_akshare` | mock yfinance 抛错,验证 akshare 路径执行 |

所有 fetch 必须 mock(`monkeypatch.setattr(repair, "fetch_missing_tickers", ...)`)— 不允许真实网络。

### 4) [M1 + M5] `manual_replacement_recommended` 必须同时清掉 column-but-NaN

**位置:** `scripts/repair_cn_price_coverage.py:662-667`(`run_incremental_backfill`)与 `scripts/repair_cn_price_coverage.py:986-998`(`--analyze` 的 h27_existing 分支)。

**改法:**

```python
remaining_missing = final_report["union_missing_columns"]
remaining_column_nan = final_report["union_missing_data"]   # 已有
candidate_full_validate_ready = (
    safety["passed"]
    and all(cp["missing_col_count"] == 0 for cp in final_report["checkpoints"])
    and all(cp["missing_data_count"] == 0 for cp in final_report["checkpoints"])
)
manual_replacement_recommended = (
    candidate_full_validate_ready
    and len(remaining_missing) == 0
    and len(remaining_column_nan) == 0
)
candidate_info["remaining_column_nan"] = len(remaining_column_nan)
candidate_info["remaining_column_nan_tickers"] = remaining_column_nan
```

并把 `union_missing_data` 加入 H27 backfill 的填充队列:

```python
to_fill = sorted(set(missing_before) | set(initial_report["union_missing_data"]))
for i in range(0, len(to_fill), max(batch_size, 1)):
    batch = to_fill[i:i+max(batch_size,1)]
    ...
```

对 column-but-NaN 的合并要 reindex 到 `candidate.index` 后只填 NaN 位(不要覆盖已有值)— 用 `candidate[ticker] = candidate[ticker].combine_first(series.reindex(candidate.index))`。

**验收:**

- 加 `test_manual_replacement_blocked_by_column_nan`:fixture 让所有列都在但 2025-01-02 有 1 个 NaN → `manual_replacement_recommended=False`。
- 加 `test_backfill_queues_column_nan_tickers`:`missing_before=[]` 但 `union_missing_data=["X.SZ"]`,确认 `fetch_missing_tickers` 被调用且收到 `["X.SZ"]`。

### 5) [M2] `merge_candidate` 改成 reindex,显式回报丢弃行

**位置:** `scripts/repair_cn_price_coverage.py:377-397`

```python
def merge_candidate(original, new_data, tickers_to_add) -> Tuple[pd.DataFrame, Dict]:
    candidate = original.copy()
    stats = {"added": [], "out_of_range_rows": {}, "in_range_nonnull_rows": {}}
    for ticker in tickers_to_add:
        if ticker not in new_data.columns:
            continue
        series = new_data[ticker]
        aligned = series.reindex(candidate.index)
        # 仅在该 ticker 当前缺值时填入,既支持新列也支持 NaN 修补
        if ticker in candidate.columns:
            candidate[ticker] = candidate[ticker].combine_first(aligned)
        else:
            candidate[ticker] = aligned
        stats["added"].append(ticker)
        stats["in_range_nonnull_rows"][ticker] = int(aligned.notna().sum())
        stats["out_of_range_rows"][ticker] = int(
            series.notna().sum() - aligned.notna().sum()
        )
    return candidate, stats
```

`run_incremental_backfill` 与 `--fetch-missing` 都改成接收 `(candidate, stats)` 元组;把 `stats["out_of_range_rows"]` 写入 `price_coverage_h27.json` 与 H27 报告的新段 "Fetched but out of candidate range"。

**验收:** 见 H3 的 `test_merge_candidate_warns_on_out_of_range_dates`。

### 6) [M3] `validate()` 测试至少有一条走真实 `CN_PIT_FileSource`

**位置:** `tests/test_ingest_cn_pit_data.py`

新增 `TestValidateWithRealSource`,**不 mock** `fundamental_backtest`:

- `test_real_source_passed_with_qlib_universe`:写 ≥200 行合法 qlib universe + 配套 snapshots + fundamentals(无 unsafe 字段) + prices.csv,期望 PASSED。
- `test_real_source_blocked_by_survivorship_marker`:`data_quality_note` 含 `SURVIVORSHIP_BIAS` → BLOCKED, blockers 含 `survivorship_bias`。
- `test_real_source_blocked_by_unsafe_fields`:fundamentals 含 `pe_ratio` → BLOCKED, blockers 含 `future_function` 或 `ungated_fundamentals`。

保留现有 mock 测试,但在 docstring 标注它们验证的是 wiring,不是检测逻辑。

### 7) [M4] `analyze_coverage` 的 target period 从 CLI 读取

**位置:** `scripts/repair_cn_price_coverage.py:225-226, 843-897`

- `analyze_coverage(universe, prices, checkpoints, target_start=None, target_end=None)`,默认仍为 2025-01-01 / 2026-05-18 但接收覆盖。
- CLI 在 `--analyze` 分支也传入 `args.start` / `args.end`:`report = analyze_coverage(universe, prices, checkpoints, args.start, args.end)`。
- `generate_report` 表头照打实际窗口。

**验收:** `tests/test_repair_cn_price_coverage.py::test_analyze_coverage_target_period_obeys_cli`。

### 8) [M6] 统一 `inspect_existing_candidate` 与 `candidate_safety_checks`

**位置:** `scripts/repair_cn_price_coverage.py:400-426, 512-555`

让 `inspect_existing_candidate` 内部直接复用 `candidate_safety_checks`:

```python
def inspect_existing_candidate(original, candidate_path=None):
    if candidate_path is None: candidate_path = CANDIDATE_CSV
    if not candidate_path.exists(): return None
    candidate = load_prices(candidate_path)
    safety = candidate_safety_checks(original, candidate)
    new_tickers = sorted(set(candidate.columns) - set(original.columns))
    return {
        "status": "existing",
        "actually_added": len(new_tickers),
        "new_tickers": new_tickers,
        "improvement": bool(new_tickers),
        "safety_passed": safety["passed"],
        "safety_issues": safety["issues"],
        "missing_existing_cols": safety["missing_existing_cols"],
        "reduced_nonnull_existing_cols": safety["reduced_nonnull_existing_cols"],
    }
```

并在 `--analyze` h27 分支把 `safety_issues` 拷进 `candidate_info`,`generate_report` 在 "Candidate CSV" 段输出 `Safety issues:` 列表。

### 9) [M7] 把 `failures` 写进 Markdown 报告

**位置:** `scripts/repair_cn_price_coverage.py:701-802`(`generate_report`)

在 "Candidate CSV" 段后追加:

```markdown
## Backfill Failures ({len(failures)})

| Ticker | Reason |
|--------|--------|
| 000413.SZ | yfinance: empty; akshare: skipped (slow_fallbacks disabled) |
...
```

最多列 50 行,其余 `... and N more`。

### 10) [L 杂项] 顺手清理

- `output_paths`(`scripts/repair_cn_price_coverage.py:567-571`):接收的 prefix 不在 `{"h26", "h27"}` 时 `raise ValueError`。
- `run_incremental_backfill`:写 `prices_h27_candidate.csv` 前若已存在,先 `prices_h27_candidate.csv.bak`。
- `validate()`:在 summary 增加 `prices_csv_sha256`(读 8KB 流式)与 `prices_csv_mtime`,把它写进 `validation_report.json`。该字段不进 status 计算,仅做 provenance。

## 验收命令

按顺序在 `/Users/zhuosama/.hermes/virtual-trader` 下跑;全部退出码 0 才算通过。

```bash
/Users/zhuosama/.hermes/hermes-agent/venv/bin/python -m py_compile \
    scripts/ingest_cn_pit_data.py \
    scripts/repair_cn_price_coverage.py \
    backtest/experiments/fundamental_backtest.py

/Users/zhuosama/.hermes/hermes-agent/venv/bin/python -m pytest \
    tests/test_ingest_cn_pit_data.py \
    tests/test_fundamental_pit_source.py \
    tests/test_repair_cn_price_coverage.py \
    tests/test_value_account.py -v

/Users/zhuosama/.hermes/hermes-agent/venv/bin/python scripts/ingest_cn_pit_data.py --validate
# 期望: status BLOCKED, data_quality_blockers 含 price_coverage,
# 新字段 price_coverage_failed_checkpoints 至少 1 项。

/Users/zhuosama/.hermes/hermes-agent/venv/bin/python scripts/ingest_cn_pit_data.py \
    --validate --period-start 2025-01-01 --period-end 2026-05-18
# 期望: status PASSED, validation_scope=period。

/Users/zhuosama/.hermes/hermes-agent/venv/bin/python scripts/repair_cn_price_coverage.py \
    --analyze --prices-file data/cn_pit/prices_h27_candidate.csv --output-prefix h27 \
    --start 2025-01-01 --end 2026-05-18
# 期望: 报告 target_period.requested = 2025-01-01 → 2026-05-18,
# candidate_full_validate_ready=False(仍有 8 missing 列),
# manual_replacement_recommended=False。
```

## 完成时请汇报

1. 各文件 diff 行数。
2. `pytest` 通过用例数 / 总数,新增用例编号。
3. 全量 `validate()` 的 status 是否仍为 BLOCKED,以及新的 `price_coverage_failed_checkpoints` 数量。
4. Period validation 是否仍 PASSED,`prices_csv_sha256` 字段是否写入。
5. `prices.csv`、`universe.jsonl`、`fundamentals.jsonl`、`universe_snapshots.jsonl` 的 mtime/sha256 与本任务开始前一致。
6. `prices_h27_candidate.csv` 是否未被覆盖(应保留 H27 写入的版本;若需更新,先 `.bak`)。
7. 一个 1–2 段的 `reports/h28_remediation_report.md`,逐项标注每个 finding 的修复状态(Fixed / Partially fixed / Deferred + 理由)。

## 不在本任务范围

- 真的去拉缺失列(BaoStock / Tushare 网络请求)。本任务只把队列、合并、报告打通,不要求实际把 8 列填上。
- 修改 `universe.jsonl` 的 max_effective_date staleness 问题(H27 review 中提到的 2021-12-13 截止) — 留给后续 H29 universe 刷新任务。
- 把 `validate()` 与 `data_quality_for_period` 合并成单入口 — 改动面太大,先维持双路径。
