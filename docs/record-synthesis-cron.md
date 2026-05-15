# Hermes Record Synthesis Cron

`agents/record_synthesizer.py` is safe to run from Hermes cron because it writes only append-only candidate batches:

```bash
cd ~/.hermes/virtual-trader && python3 agents/record_synthesizer.py --write --since-days 7
```

Dry run:

```bash
cd ~/.hermes/virtual-trader && python3 agents/record_synthesizer.py --dry-run --since-days 7
```

Verbose dry run for debugging:

```bash
cd ~/.hermes/virtual-trader && python3 agents/record_synthesizer.py --dry-run --since-days 7 --verbose
```

Recommended schedule:

- weekday post-market: optional, after `虚拟盘-盘后交易复盘`
- weekly: recommended, after `虚拟盘-周末深度复盘`

The first stable deployment should use weekly cadence for 2-3 weeks. Daily runs can be enabled later if the candidate stream proves high signal.

After this branch is merged into `~/.hermes/virtual-trader`, create the weekly Hermes job with:

```bash
hermes cron create "30 3 * * 6" \
  --name "Hermes-record-synthesis" \
  --deliver local \
  --workdir "$HOME/.hermes/virtual-trader" \
  "执行 python3 agents/record_synthesizer.py --write --since-days 7。读取输出 JSON，若 warnings 非空或 risk_notes 非空，简短报告；否则保持本地记录即可。不要直接修改 memory、Obsidian 或 public site。"
```

The job should run after the weekly review job. Keep it local until candidate quality is proven.

## Reviewer Config

Reviewer commands are advisory. They receive the digest JSON on stdin and should return JSON on stdout:

```json
{
  "role": "critic",
  "notes": ["The audit gate update is worth preserving."],
  "followups": [
    {
      "title": "Update public strategy page",
      "body": "Confirm the public Hermes page reflects aggregate audit-layer status.",
      "visibility": "private",
      "confidence": "medium"
    }
  ]
}
```

All reviewer outputs are stored under `agent_reviews`. They cannot write memory, career-strategy logs, or public pages directly.

Use an explicit config path when reviewers are ready:

```bash
cd ~/.hermes/virtual-trader && python3 agents/record_synthesizer.py --write --since-days 7 --reviewer-config records/reviewer_config.json
```

## Promotion Path

Candidate batches land in:

```text
records/synthesis/candidates.jsonl
```

Agents can also add durable facts before synthesis by appending JSON lines to:

```text
records/events/YYYY-MM-DD.jsonl
```

Event example:

```json
{
  "created_at": "2026-05-15T10:00:00",
  "title": "Record synthesis loop implemented",
  "body": "Codex added records/events JSONL input so future agents can preserve valuable work.",
  "destinations": ["memory", "career_log", "public_story", "followup"],
  "visibility": "public",
  "confidence": "high",
  "source_refs": ["docs/superpowers/specs/2026-05-15-record-synthesis-design.md"]
}
```

A later recorder step can promote selected candidates to:

- `~/.hermes/memories/MEMORY.md`
- `~/.hermes/profiles/trader/memories/MEMORY.md`
- `obsidian-wiki/projects/career-strategy/log.md`
- public site snapshot/story pages

Promotion should stay separate from synthesis so failed or noisy reviewer output never becomes durable memory automatically.

Recommended next-stage structure:

```text
agents/
  record_synthesizer.py
  candidate_selector.py
  record_promoter.py
records/
  events/*.jsonl
  synthesis/
    candidates.jsonl
    selected.jsonl
    promotion_log.jsonl
```

`candidate_selector.py` should remain read-only over candidates and append only to `selected.jsonl`. `record_promoter.py` should be idempotent, use `run_id + title` as a dedupe key, and write a promotion log even when one target writer fails.
