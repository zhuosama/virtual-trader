# H23 — Qlib CSI300 Instruments Fallback

## Goal

Add a tokenless historical universe import path using an existing Qlib `instruments/csi300.txt` file.

This is a fallback to H22 Tushare `index_weight`. It should not download or fabricate historical membership. It should only import a real Qlib instruments file if one is present.

## References

- Qlib CN index collector README:
  - https://github.com/microsoft/qlib/blob/main/scripts/data_collector/cn_index/README.md
  - Command: `python collector.py --index_name CSI300 --qlib_dir ~/.qlib/qlib_data/cn_data --method parse_instruments`
- Qlib `IndexBase.parse_instruments` writes `instruments/<index>.txt` as tab-separated rows with no header:
  - `symbol`, `start_date`, `end_date`
  - Example symbols: `SH600519`, `SZ000001`
- Qlib docs describe predefined stock pools such as `csi300` as dynamic instruments/date ranges:
  - https://qlib.readthedocs.io/en/v0.7.2/component/data.html

## Current State

- H22 Tushare historical universe code exists in `scripts/ingest_cn_pit_data.py`.
- `CN_PIT_FileSource` now requires historical evidence before clearing `survivorship_bias`.
- Current local data still has `SURVIVORSHIP_BIAS` and `research_only`.
- There is no `TUSHARE_TOKEN` and no `tushare` package in the current venv.

## Implementation Scope

Own these files:
- `scripts/ingest_cn_pit_data.py`
- `backtest/experiments/fundamental_backtest.py`
- `tests/test_fundamental_pit_source.py`
- Optional docs under `docs/`
- Generated output under `data/cn_pit/`

Do not revert unrelated changes.

## Requirements

### 1. Add Qlib import CLI

Add a CLI mode:

```bash
python scripts/ingest_cn_pit_data.py --import-qlib-universe --qlib-dir ~/.qlib/qlib_data/cn_data --market csi300
```

Defaults:
- `--qlib-dir ~/.qlib/qlib_data/cn_data`
- `--market csi300`

Expected input file:

```text
<qlib-dir>/instruments/csi300.txt
```

If the file is missing:
- Print a clear diagnostic.
- Do not overwrite existing `data/cn_pit/universe.jsonl`.
- Keep validation blockers unchanged.
- Return a non-crashing status.

### 2. Parse Qlib instruments format

Support:
- tab-separated rows with no header: `symbol start_date end_date`
- whitespace-separated rows
- optional accidental header row

Normalize symbols:
- `SH600519` → `600519.SS`
- `SZ000001` → `000001.SZ`
- `600519.SH` → `600519.SS`
- `000001.SZ` → `000001.SZ`

End-date rules:
- Qlib open end such as `2099-12-31` should become empty `end_date`.
- Real closed intervals should preserve `end_date`.

Each output `universe.jsonl` row must include:
- `ticker`
- `code`
- `effective_date`
- `end_date`
- `source_url`
- `ingested_at`
- `source_provider`: `qlib:instruments`
- `snapshot_count`: `1`
- `qlib_symbol`
- `qlib_market`

### 3. Evidence file for gate

Write `data/cn_pit/universe_snapshots.jsonl` when importing Qlib, so `CN_PIT_FileSource` has auditable historical evidence.

Each snapshot/evidence row should include:
- `ticker`
- `code`
- `trade_date`: interval `effective_date`
- `source_provider`: `qlib:instruments`
- `source_url`
- `ingested_at`
- `qlib_symbol`
- `qlib_market`

Update `CN_PIT_FileSource._has_historical_universe_evidence()` so it accepts either:
- `tushare:index_weight` with positive `snapshot_count` and nonempty snapshot rows, or
- `qlib:instruments` with positive `snapshot_count` and nonempty snapshot rows.

Do not accept unknown providers.

### 4. Validation report

Extend `validate()` to report:
- `universe_source_providers`: sorted list from universe rows
- whether `universe_snapshots.jsonl` exists
- snapshot count/range as already added in H22

If source provider is `qlib:instruments`, data quality may become clean only if:
- file exists and parsed at least one interval,
- all rows have valid dates and provider fields,
- snapshot/evidence rows exist,
- no `SURVIVORSHIP_BIAS` markers are present.

### 5. Tests

Add focused tests:
- parse Qlib tab format with `SH600519 2020-01-01 2099-12-31`
- parse whitespace format and optional header
- normalize `SH`/`SZ` prefixes correctly
- convert `2099-12-31` to empty `end_date`
- missing Qlib file does not overwrite existing universe data
- `CN_PIT_FileSource` accepts `qlib:instruments` evidence as historical
- unknown provider remains blocked

### 6. Verification

Run:

```bash
/Users/zhuosama/.hermes/hermes-agent/venv/bin/python -m py_compile backtest/experiments/fundamental_backtest.py scripts/ingest_cn_pit_data.py
/Users/zhuosama/.hermes/hermes-agent/venv/bin/python -m pytest tests/test_fundamental_pit_source.py tests/test_value_account.py
/Users/zhuosama/.hermes/hermes-agent/venv/bin/python scripts/ingest_cn_pit_data.py --validate
```

If local Qlib data exists, also run:

```bash
/Users/zhuosama/.hermes/hermes-agent/venv/bin/python scripts/ingest_cn_pit_data.py --import-qlib-universe --qlib-dir ~/.qlib/qlib_data/cn_data --market csi300
/Users/zhuosama/.hermes/hermes-agent/venv/bin/python scripts/ingest_cn_pit_data.py --validate
```

## Acceptance Criteria

Report back with:
- Whether local Qlib `csi300.txt` exists.
- If imported: interval count, snapshot evidence count, effective date range, source provider list.
- If missing: confirm no overwrite occurred.
- Validation summary.
- Backtest deploy gate status.

Deployment must remain blocked unless a real Qlib instruments file or real Tushare historical snapshots are present.
