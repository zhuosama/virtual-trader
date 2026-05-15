# Record Synthesis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a local Hermes record-synthesis loop that converts virtual-trader workflow facts into append-only, reviewable long-term record candidates.

**Architecture:** Implement a focused `RecordSynthesizer` module under `agents/` with pure functions for loading sources, building digests, consulting optional reviewers, sanitizing public content, and appending candidate batches. Provide a CLI entry point so Hermes cron can run it without giving external agents write authority.

**Tech Stack:** Python standard library, `unittest`, existing virtual-trader file layout.

---

### Task 1: Core Synthesis Module

**Files:**
- Create: `agents/record_synthesizer.py`
- Test: `tests/test_record_synthesizer.py`

- [ ] **Step 1: Write failing tests**

Create tests that build a temporary data tree with:

```python
agents/workflows/workflow_post_market_20260515_093000.json
strategies/audit_log.json
strategies/changelog.json
reports/daily/2026-05-15.md
```

Assert that `RecordSynthesizer(...).run(write=False)` returns:

- one memory candidate mentioning `audit_layer.review`;
- one career-log candidate;
- one public story candidate;
- no public candidate body containing `proposal-secret-001` or `verdicts`.

- [ ] **Step 2: Verify RED**

Run:

```bash
python3 -m unittest tests.test_record_synthesizer
```

Expected: import failure for `agents.record_synthesizer`.

- [ ] **Step 3: Implement minimal module**

Add:

- `RecordSynthesizer`
- `RecordCandidate`
- `SynthesisBatch`
- source loading helpers
- deterministic candidate generation from recent audit/workflow data
- public sanitization

- [ ] **Step 4: Verify GREEN**

Run:

```bash
python3 -m unittest tests.test_record_synthesizer
```

Expected: all tests pass.

### Task 2: Append-Only Candidate Writing

**Files:**
- Modify: `agents/record_synthesizer.py`
- Modify: `tests/test_record_synthesizer.py`

- [ ] **Step 1: Write failing test**

Assert that `run(write=True)` creates `records/synthesis/candidates.jsonl`, appends exactly one JSON object, and preserves existing lines on a second run.

- [ ] **Step 2: Verify RED**

Run:

```bash
python3 -m unittest tests.test_record_synthesizer
```

Expected: write behavior missing.

- [ ] **Step 3: Implement append-only writer**

Create parent directories and append one UTF-8 JSON line per run.

- [ ] **Step 4: Verify GREEN**

Run:

```bash
python3 -m unittest tests.test_record_synthesizer
```

Expected: all tests pass.

### Task 3: Optional Agent Reviewers

**Files:**
- Modify: `agents/record_synthesizer.py`
- Modify: `tests/test_record_synthesizer.py`

- [ ] **Step 1: Write failing test**

Pass a fake reviewer callable returning:

```json
{"role": "critic", "notes": ["too noisy"], "followups": [{"title": "Check site update", "body": "Confirm public page reflects audit gate."}]}
```

Assert the review is stored under `agent_reviews` and its follow-up is merged into `followups`.

- [ ] **Step 2: Verify RED**

Run:

```bash
python3 -m unittest tests.test_record_synthesizer
```

Expected: reviewer output ignored.

- [ ] **Step 3: Implement reviewer hook**

Accept optional in-process reviewers for tests and optional command reviewers for CLI use. Reviewer failures become `agent_reviews` entries with `status: error`.

- [ ] **Step 4: Verify GREEN**

Run:

```bash
python3 -m unittest tests.test_record_synthesizer
```

Expected: all tests pass.

### Task 4: CLI and Dry Run

**Files:**
- Modify: `agents/record_synthesizer.py`

- [ ] **Step 1: Write CLI smoke test or direct command expectation**

Run:

```bash
python3 agents/record_synthesizer.py --data-dir "$PWD" --dry-run --since-days 7
```

Expected: JSON batch printed to stdout and no `records/synthesis/candidates.jsonl` created by dry run.

- [ ] **Step 2: Implement argparse entry point**

Flags:

- `--data-dir`
- `--since-days`
- `--dry-run`
- `--write`
- `--reviewer-config`

- [ ] **Step 3: Verify CLI**

Run:

```bash
python3 -m py_compile agents/record_synthesizer.py tests/test_record_synthesizer.py
python3 agents/record_synthesizer.py --data-dir "$PWD" --dry-run --since-days 7
```

Expected: compile succeeds and CLI prints valid JSON.

### Task 5: Append-Only Event Input

**Files:**
- Modify: `agents/record_synthesizer.py`
- Modify: `tests/test_record_synthesizer.py`
- Create: `records/events/2026-05-15.jsonl`

- [ ] **Step 1: Write failing test**

Create a `records/events/YYYY-MM-DD.jsonl` line with `title`, `body`, `destinations`, `visibility`, `confidence`, and `source_refs`. Assert the event becomes memory, career-log, public-story, and follow-up candidates according to `destinations`.

- [ ] **Step 2: Verify RED**

Run:

```bash
python3 -m unittest tests.test_record_synthesizer
```

Expected: event candidates missing.

- [ ] **Step 3: Implement event loading**

Read JSONL files from `records/events/`, attach `_source_path`, and map `destinations` to candidate lists. Public-story candidates require `visibility: public`.

- [ ] **Step 4: Verify GREEN**

Run:

```bash
python3 -m unittest tests.test_record_synthesizer
```

Expected: all tests pass.

### Task 6: Hermes Cron Deployment Notes

**Files:**
- Create: `docs/record-synthesis-cron.md`

- [ ] **Step 1: Document safe cron command**

Use:

```bash
cd ~/.hermes/virtual-trader && python3 agents/record_synthesizer.py --write --since-days 7
```

- [ ] **Step 2: Document reviewer config**

Show a disabled-by-default `records/reviewer_config.example.json` with advisory reviewers only.

- [ ] **Step 3: Final verification**

Run:

```bash
python3 -m unittest tests.test_record_synthesizer
python3 -m py_compile agents/record_synthesizer.py tests/test_record_synthesizer.py
```

Expected: all pass.
