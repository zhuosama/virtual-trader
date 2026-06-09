#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Local record synthesis for Hermes virtual-trader.

The synthesizer turns trusted local workflow facts into append-only candidate
records. Optional agent reviewers are advisory; this module remains the only
writer.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

VTRADER_HOME = os.environ.get("VTRADER_HOME", os.path.expanduser("~/.hermes/virtual-trader"))
logger = logging.getLogger(__name__)


Reviewer = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass
class RecordCandidate:
    title: str
    body: str
    source_refs: list[str]
    confidence: str = "medium"
    visibility: str = "private"
    status: str = "candidate"


@dataclass
class SynthesisBatch:
    run_id: str
    created_at: str
    source_window: dict[str, Any]
    source_refs: list[dict[str, Any]]
    agent_reviews: list[dict[str, Any]] = field(default_factory=list)
    memory_candidates: list[RecordCandidate] = field(default_factory=list)
    career_log_candidates: list[RecordCandidate] = field(default_factory=list)
    public_story_candidates: list[RecordCandidate] = field(default_factory=list)
    risk_notes: list[RecordCandidate] = field(default_factory=list)
    followups: list[RecordCandidate] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key in (
            "memory_candidates",
            "career_log_candidates",
            "public_story_candidates",
            "risk_notes",
            "followups",
        ):
            data[key] = [asdict(item) for item in getattr(self, key)]
        return data


class RecordSynthesizer:
    """Build reviewable long-term record candidates from local Hermes artifacts."""

    def __init__(
        self,
        data_dir: str | Path | None = None,
        since_days: int = 7,
        reviewers: Iterable[Reviewer] | None = None,
        reviewer_config: str | Path | None = None,
    ):
        self.data_dir = Path(data_dir or VTRADER_HOME).expanduser()
        self.since_days = since_days
        self.reviewers = list(reviewers or [])
        self.reviewer_config = Path(reviewer_config).expanduser() if reviewer_config else None

    def run(self, write: bool = False, since_days: int | None = None) -> dict[str, Any]:
        since = since_days or self.since_days
        digest = self.build_digest(since_days=since)
        batch = self._synthesize(digest, since_days=since)
        self._apply_agent_reviews(batch, digest)
        batch_dict = batch.to_dict()
        if write:
            self.write_batch(batch_dict)
        return batch_dict

    def build_digest(self, since_days: int) -> dict[str, Any]:
        workflows = self._load_recent_workflows(limit=12)
        audit_log = self._load_json(self.data_dir / "strategies" / "audit_log.json", default=[])
        changelog = self._load_json(self.data_dir / "strategies" / "changelog.json", default=[])
        reports = self._load_recent_reports(limit=8)
        events = self._load_recent_events(limit=100)

        source_refs = []
        source_refs.extend(self._refs_for("workflow", workflows))
        source_refs.extend(self._refs_for("report", reports))
        source_refs.extend(self._refs_for("event", events))
        if audit_log:
            source_refs.append({"type": "audit_log", "path": "strategies/audit_log.json"})
        if changelog:
            source_refs.append({"type": "changelog", "path": "strategies/changelog.json"})

        return {
            "source_window": {"since_days": since_days},
            "workflows": workflows,
            "audit_log": audit_log if isinstance(audit_log, list) else [],
            "changelog": changelog if isinstance(changelog, list) else [],
            "reports": reports,
            "events": events,
            "source_refs": source_refs,
        }

    def write_batch(self, batch: dict[str, Any]) -> Path:
        output = self.data_dir / "records" / "synthesis" / "candidates.jsonl"
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("a", encoding="utf-8") as f:
            f.write(json.dumps(batch, ensure_ascii=False, sort_keys=True) + "\n")
        return output

    def _synthesize(self, digest: dict[str, Any], since_days: int) -> SynthesisBatch:
        now = datetime.now().replace(microsecond=0).isoformat()
        run_id = "record-synthesis-" + str(uuid.uuid4())
        batch = SynthesisBatch(
            run_id=run_id,
            created_at=now,
            source_window={"since_days": since_days},
            source_refs=digest["source_refs"],
        )

        latest_audit = self._latest_audit_entry(digest["audit_log"])
        latest_workflow = self._latest_workflow(digest["workflows"])
        source_paths = [ref["path"] for ref in digest["source_refs"] if "path" in ref]

        for event in digest.get("events", []):
            self._add_event_candidates(batch, event, source_paths)

        if latest_audit:
            decision = latest_audit.get("decision", "UNKNOWN")
            audited_at = latest_audit.get("audited_at", "unknown time")
            batch.memory_candidates.append(
                RecordCandidate(
                    title="Hermes virtual-trader audit gate state",
                    body=(
                        "Strategy changes must go through propose() -> "
                        "audit_layer.review() -> commit_approved(). "
                        f"Latest audit decision: {decision} at {audited_at}."
                    ),
                    source_refs=source_paths,
                    confidence="high",
                    visibility="private",
                )
            )
            batch.career_log_candidates.append(
                RecordCandidate(
                    title="策略变更闸门沉淀",
                    body=(
                        "盘后策略迭代已经沉淀为可审计流程：先生成 proposal，"
                        "再由 audit_layer.review 做三审，只有通过后才允许 commit_approved 写入。"
                    ),
                    source_refs=source_paths,
                    confidence="high",
                    visibility="private",
                )
            )
            batch.public_story_candidates.append(
                RecordCandidate(
                    title="Audit Layer added to Hermes strategy iteration",
                    body=(
                        "Hermes virtual-trader now summarizes strategy changes through "
                        "an Audit Layer gate before promotion. Public output remains "
                        "aggregate-only and avoids proposal ids or reviewer traces."
                    ),
                    source_refs=self._public_source_refs(source_paths),
                    confidence="medium",
                    visibility="public",
                )
            )
            if decision in {"AUTO_REJECT", "HUMAN_REVIEW", "PENDING_RETRY", "BLOCKED"}:
                batch.risk_notes.append(
                    RecordCandidate(
                        title=f"Audit gate requires attention: {decision}",
                        body="A recent strategy proposal did not auto-merge and should be reviewed before treating it as learning signal.",
                        source_refs=source_paths,
                        confidence="high",
                        visibility="private",
                    )
                )

        if latest_workflow and latest_workflow.get("status") != "success":
            batch.risk_notes.append(
                RecordCandidate(
                    title="Virtual-trader workflow failure",
                    body=f"{latest_workflow.get('workflow_type', 'unknown')} workflow ended with status {latest_workflow.get('status')}.",
                    source_refs=source_paths,
                    confidence="high",
                    visibility="private",
                )
            )

        if not latest_audit and not latest_workflow:
            batch.warnings.append("No recent workflow or audit-log facts found.")

        return batch

    def _add_event_candidates(
        self,
        batch: SynthesisBatch,
        event: dict[str, Any],
        fallback_source_paths: list[str],
    ) -> None:
        title = str(event.get("title", "")).strip()
        body = str(event.get("body", "")).strip()
        if not title or not body:
            return

        destinations = event.get("destinations", [])
        if not isinstance(destinations, list):
            destinations = []
        source_refs = event.get("source_refs") if isinstance(event.get("source_refs"), list) else fallback_source_paths
        confidence = str(event.get("confidence", "medium"))
        visibility = str(event.get("visibility", "private"))

        candidate = RecordCandidate(
            title=title,
            body=body,
            source_refs=[str(ref) for ref in source_refs],
            confidence=confidence,
            visibility=visibility,
        )

        if "memory" in destinations:
            batch.memory_candidates.append(candidate)
        if "career_log" in destinations:
            batch.career_log_candidates.append(candidate)
        if "public_story" in destinations and visibility == "public":
            batch.public_story_candidates.append(
                RecordCandidate(
                    title=title,
                    body=self._sanitize_public_text(body),
                    source_refs=self._public_source_refs([str(ref) for ref in source_refs]),
                    confidence=confidence,
                    visibility="public",
                )
            )
        if "risk_note" in destinations:
            batch.risk_notes.append(candidate)
        if "followup" in destinations:
            batch.followups.append(
                RecordCandidate(
                    title=title,
                    body=body,
                    source_refs=[str(ref) for ref in source_refs],
                    confidence=confidence,
                    visibility=visibility,
                )
            )

    def _apply_agent_reviews(self, batch: SynthesisBatch, digest: dict[str, Any]) -> None:
        reviewers = list(self.reviewers)
        reviewers.extend(self._load_command_reviewers())

        for reviewer in reviewers:
            try:
                review = reviewer(digest)
                if not isinstance(review, dict):
                    raise ValueError("reviewer returned non-object JSON")
                review.setdefault("status", "ok")
                batch.agent_reviews.append(review)
                for followup in review.get("followups", []):
                    if isinstance(followup, dict) and followup.get("title") and followup.get("body"):
                        batch.followups.append(
                            RecordCandidate(
                                title=str(followup["title"]),
                                body=str(followup["body"]),
                                source_refs=[
                                    ref["path"]
                                    for ref in digest.get("source_refs", [])
                                    if isinstance(ref, dict) and "path" in ref
                                ],
                                confidence=str(followup.get("confidence", "medium")),
                                visibility=str(followup.get("visibility", "private")),
                            )
                        )
            except Exception as exc:
                batch.agent_reviews.append(
                    {
                        "role": getattr(reviewer, "__name__", "reviewer"),
                        "status": "error",
                        "error": str(exc),
                    }
                )

    def _load_command_reviewers(self) -> list[Reviewer]:
        if not self.reviewer_config or not self.reviewer_config.exists():
            return []
        config = self._load_json(self.reviewer_config, default={})
        reviewers = []
        for item in config.get("reviewers", []):
            if not item.get("enabled", False):
                continue
            role = item.get("role", "external")
            command = item.get("command", [])
            if not isinstance(command, list) or not command:
                continue
            reviewers.append(self._command_reviewer(role=role, command=command))
        return reviewers

    def _command_reviewer(self, role: str, command: list[str]) -> Reviewer:
        def run_command(digest: dict[str, Any]) -> dict[str, Any]:
            proc = subprocess.run(
                command,
                input=json.dumps(digest, ensure_ascii=False),
                text=True,
                capture_output=True,
                check=False,
                timeout=120,
            )
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr.strip() or f"reviewer exited {proc.returncode}")
            data = json.loads(proc.stdout)
            if isinstance(data, dict):
                data.setdefault("role", role)
            return data

        run_command.__name__ = f"{role}_command_reviewer"
        return run_command

    def _load_recent_workflows(self, limit: int) -> list[dict[str, Any]]:
        workflow_dir = self.data_dir / "agents" / "workflows"
        files = sorted(workflow_dir.glob("workflow_*.json"))[-limit:]
        workflows = []
        for path in files:
            data = self._load_json(path, default=None)
            if isinstance(data, dict):
                data["_source_path"] = self._relative_path(path)
                workflows.append(data)
        return workflows

    def _load_recent_reports(self, limit: int) -> list[dict[str, Any]]:
        report_paths = []
        for subdir in ("daily", "weekly"):
            report_paths.extend((self.data_dir / "reports" / subdir).glob("*.md"))
        reports = []
        for path in sorted(report_paths)[-limit:]:
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            reports.append(
                {
                    "_source_path": self._relative_path(path),
                    "excerpt": text[:1000],
                }
            )
        return reports

    def _load_recent_events(self, limit: int) -> list[dict[str, Any]]:
        event_dir = self.data_dir / "records" / "events"
        events = []
        for path in sorted(event_dir.glob("*.jsonl"))[-limit:]:
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line_number, line in enumerate(lines, 1):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict):
                    event["_source_path"] = f"{self._relative_path(path)}:{line_number}"
                    events.append(event)
        return events[-limit:]

    def _load_json(self, path: Path, default: Any) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return default

    def _refs_for(self, ref_type: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        refs = []
        for item in items:
            path = item.get("_source_path")
            if path:
                refs.append({"type": ref_type, "path": path})
        return refs

    def _relative_path(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.data_dir))
        except ValueError:
            return str(path)

    def _latest_audit_entry(self, entries: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not entries:
            return None
        valid = [entry for entry in entries if isinstance(entry, dict)]
        return valid[-1] if valid else None

    def _latest_workflow(self, workflows: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not workflows:
            return None
        return workflows[-1]

    def _public_source_refs(self, refs: list[str]) -> list[str]:
        return [
            ref
            for ref in refs
            if not ref.endswith("audit_log.json") and "/workflows/" not in ref and not ref.startswith("agents/workflows/")
        ]

    def _sanitize_public_text(self, text: str) -> str:
        sanitized = text
        patterns = (
            r"\bproposal_id\s*[:=]\s*[^\s,;。]+",
            r"\bproposal-secret-[^\s,;。]+",
            r"\bprivate-token-[^\s,;。]+",
            r"\breviewer trace\b[^.。]*",
            r"\bverdicts\b[^.。]*",
        )
        for pattern in patterns:
            sanitized = re.sub(pattern, "[private]", sanitized, flags=re.IGNORECASE)
        return sanitized


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Synthesize Hermes record candidates.")
    parser.add_argument("--data-dir", default=VTRADER_HOME)
    parser.add_argument("--since-days", type=int, default=7)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--reviewer-config")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s:%(name)s:%(message)s",
        stream=sys.stderr,
        force=True,
    )

    write = args.write and not args.dry_run
    synthesizer = RecordSynthesizer(
        data_dir=args.data_dir,
        since_days=args.since_days,
        reviewer_config=args.reviewer_config,
    )
    batch = synthesizer.run(write=write)
    logger.info("Built record synthesis batch %s", batch["run_id"])
    print(json.dumps(batch, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
