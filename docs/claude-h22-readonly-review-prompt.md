# Claude Code Read-Only Review Prompt — H20-H23

Run from `/Users/zhuosama/.hermes/virtual-trader` if you want Claude Code to perform the independent review:

```bash
/Users/zhuosama/.local/bin/claude -p --permission-mode dontAsk --tools Read,Grep,Glob,LS --max-budget-usd 2 --model sonnet "$(cat docs/claude-h22-readonly-review-prompt.md | sed '1,/^---$/d')"
```

---

You are Claude Code, acting as a read-only code reviewer. Do not edit files.

Review these files:
- `backtest/experiments/fundamental_backtest.py`
- `scripts/ingest_cn_pit_data.py`
- `tests/test_fundamental_pit_source.py`
- `docs/hermes-h22-historical-universe-task.md`

Context:
- The project is building a point-in-time A-share value-investing backtest data source.
- The main risk is mistakenly allowing deployment when the universe still has survivorship bias or fundamentals still leak future information.
- H20-H22 added `CN_PIT_FileSource`, local PIT files, Tushare `index_weight` historical universe ingestion, `universe_snapshots.jsonl`, and a stricter deployment gate.
- Current machine has no Tushare token and no `tushare` package, so current data should remain `research_only` and `can_deploy=false`.

Please output findings first, ordered by severity:
- `BLOCKER`
- `HIGH`
- `MEDIUM`
- `LOW`
- `NIT`

For each actionable finding include file and line number.

Focus questions:
1. Can `survivorship_bias` still be falsely cleared without true historical membership evidence?
2. Is the `universe_snapshots.jsonl` evidence check sufficient, or can bogus/incomplete snapshots pass?
3. Can `validate()` or `validation_report.json` mislead a user into thinking deployment is allowed?
4. Does `snapshots_to_intervals()` handle disappear/reappear, final open interval, duplicate snapshots, unsorted dates, malformed dates, and non-monthly gaps correctly?
5. Do tests cover the most important false-clean and false-deploy failure modes?
6. Are there any future-function leaks from current yfinance/AKShare fields into backtests?

End with:
- `verdict: APPROVE` if no blocking or high-confidence actionable issues remain.
- `verdict: REQUEST_CHANGES` otherwise.
