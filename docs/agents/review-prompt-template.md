# Read-Only Review Prompt Template

Use this prompt for Claude first. If Claude is unavailable, record the failure and use Hermes with the same prompt.

```text
Work in /Users/zhuosama/.hermes/virtual-trader.

READ-ONLY REVIEW ONLY.
Do not edit files.
Do not run mutating commands.

Review task: Hxx — <task name>.

Files/artifacts to review:
- <file 1>
- <file 2>
- <report>
- <run json>
- <tests>

Focus:
- correctness bugs
- JSON/report inconsistencies
- deployment-gate leaks
- future-function or survivorship-bias leaks
- missing behavior tests for new gate/search/loader/accounting logic
- stale sync documentation

Validation already run:
- <command>: <result>
- <command>: <result>

Output format:
- Findings first, ordered by severity.
- Use BLOCKER/HIGH/MEDIUM/LOW/NIT.
- Include file and line references.
- End with exactly one verdict: APPROVE or REQUEST_CHANGES.
```

