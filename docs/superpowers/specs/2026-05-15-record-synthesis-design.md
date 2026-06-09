# Hermes Record Synthesis Design

## Goal

Build a small, auditable record-synthesis loop for Hermes virtual-trader so valuable work does not stay trapped in transient agent sessions. The system should collect operational facts, ask configured agents for suggestions, and produce candidate records for memory, career-strategy logs, public site stories, risk notes, and follow-up tasks.

## Design Choice

Recommended approach: multi-agent suggestions with one local recorder.

Alternatives considered:

- Directly let each agent write memory/site/log files. This is fast but too easy to pollute long-term records.
- Introduce Graphiti/Zep/Mem0 immediately. These are promising, but they add infrastructure before Hermes has a stable event schema.
- Start with local append-only candidates and later swap storage or retrieval. This matches the existing Hermes file-based architecture and keeps rollback simple.

## Architecture

`record_synthesizer.py` runs after important workflows or on a weekly cron. It reads recent trusted local artifacts:

- `agents/workflows/workflow_*.json`
- `strategies/audit_log.json`
- `strategies/changelog.json`
- recent `reports/daily/*.md` and `reports/weekly/*.md`
- optional append-only `records/events/*.jsonl`

It builds a compact digest, optionally sends that digest to configured agent reviewers, then writes a candidate batch to `records/synthesis/candidates.jsonl`. It does not directly edit Hermes memory, career-strategy notes, or public site pages in phase 1.

Agents can also append durable facts to `records/events/YYYY-MM-DD.jsonl`. This gives Claude, Codex, Hermes, or a future reviewer a simple handoff-neutral way to say "this work is worth preserving" without granting direct write access to memory or public pages.

## Candidate Schema

Each synthesis run emits one JSON object:

```json
{
  "run_id": "record-synthesis-20260515T071200",
  "created_at": "2026-05-15T07:12:00",
  "source_window": {"since_days": 7},
  "source_refs": [],
  "agent_reviews": [],
  "memory_candidates": [],
  "career_log_candidates": [],
  "public_story_candidates": [],
  "risk_notes": [],
  "followups": []
}
```

Candidate records include `title`, `body`, `source_refs`, `confidence`, and `visibility`. Public candidates must be aggregate-only: no proposal ids, reviewer traces, raw account data, or workflow internals.

## Agent Consultation

Agent consultation is optional and config-driven. A config file can declare reviewers as commands that receive the digest on stdin and return JSON suggestions on stdout. Reviewers are advisory only. Their output is stored under `agent_reviews` and may enrich candidate text, but cannot directly write canonical memory or public content.

Default reviewers:

- `trader`: checks trading/risk significance.
- `coder`: checks architecture and implementation significance.
- `content`: checks whether the event is useful for site/blog/career narrative.
- `critic`: checks if the event is too noisy to preserve.

## Safety Rules

- Append-only output. No deletion or rewriting of previous candidate batches.
- Public candidates are sanitized by default.
- Public sanitization removes sensitive key-value pairs and obvious private trace tokens, not just field names.
- Missing or malformed inputs degrade to warnings, not failed runs.
- Agent errors are recorded as review errors and do not block local synthesis.
- Low-confidence or private records remain candidates only.
- Run ids use UUIDs to avoid timestamp collision in repeated local runs.

## Testing

Unit tests use a temporary virtual-trader tree with synthetic workflow and audit-log files. Tests verify:

- audit-layer architecture changes produce memory, career-log, and public-story candidates;
- public stories do not leak proposal ids or reviewer traces;
- append-only candidate writing works;
- append-only `records/events/*.jsonl` facts can drive candidate generation;
- fake agent reviewers can contribute advisory notes without taking write authority.
