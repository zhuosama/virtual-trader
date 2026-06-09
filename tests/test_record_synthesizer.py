import json
import tempfile
import unittest
import uuid
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from agents.record_synthesizer import RecordSynthesizer, main


class RecordSynthesizerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)
        (self.data_dir / "agents" / "workflows").mkdir(parents=True)
        (self.data_dir / "strategies").mkdir(parents=True)
        (self.data_dir / "reports" / "daily").mkdir(parents=True)

        self._write_json(
            self.data_dir / "agents" / "workflows" / "workflow_post_market_20260515_093000.json",
            {
                "timestamp": "2026-05-15T09:30:00",
                "workflow_type": "post_market",
                "status": "success",
                "final_output": "盘后复盘完成：策略调整通过 audit_layer.review 后进入 commit_approved。",
                "steps": [
                    {"agent": "review_agent", "status": "success"},
                    {"agent": "strategy_maintainer", "status": "success"},
                ],
            },
        )
        self._write_json(
            self.data_dir / "strategies" / "audit_log.json",
            [
                {
                    "proposal_id": "proposal-secret-001",
                    "audited_at": "2026-05-15T09:31:00",
                    "decision": "AUTO_MERGE",
                    "reason": "3/3 unanimous approve",
                    "verdicts": [
                        {"auditor": "risk", "reasoning": "private risk trace"},
                    ],
                }
            ],
        )
        self._write_json(
            self.data_dir / "strategies" / "changelog.json",
            [
                {
                    "timestamp": "2026-05-15T09:32:00",
                    "parameter": "max_single_position",
                    "old_value": 0.1,
                    "new_value": 0.08,
                    "reason": "防止仓位超限",
                }
            ],
        )
        (self.data_dir / "reports" / "daily" / "2026-05-15.md").write_text(
            "# 2026-05-15\n\nAudit gate updated and ready for site summary.\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _write_json(self, path, data):
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def test_generates_candidates_from_recent_audit_and_workflow_facts(self):
        batch = RecordSynthesizer(self.data_dir).run(write=False)

        memory_text = json.dumps(batch["memory_candidates"], ensure_ascii=False)
        career_text = json.dumps(batch["career_log_candidates"], ensure_ascii=False)
        public_text = json.dumps(batch["public_story_candidates"], ensure_ascii=False)

        self.assertIn("audit_layer.review", memory_text)
        self.assertIn("策略变更闸门", career_text)
        self.assertIn("Audit Layer", public_text)
        self.assertNotIn("proposal-secret-001", public_text)
        self.assertNotIn("verdicts", public_text)

    def test_write_appends_candidate_batches(self):
        synthesizer = RecordSynthesizer(self.data_dir)

        first = synthesizer.run(write=True)
        second = synthesizer.run(write=True)

        path = self.data_dir / "records" / "synthesis" / "candidates.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(2, len(lines))
        self.assertEqual(first["run_id"], json.loads(lines[0])["run_id"])
        self.assertEqual(second["run_id"], json.loads(lines[1])["run_id"])

    def test_agent_reviewers_are_advisory_and_can_add_followups(self):
        def fake_reviewer(digest):
            return {
                "role": "critic",
                "notes": ["too noisy"],
                "followups": [
                    {
                        "title": "Check site update",
                        "body": "Confirm public page reflects audit gate.",
                    }
                ],
            }

        batch = RecordSynthesizer(self.data_dir, reviewers=[fake_reviewer]).run(write=False)

        self.assertEqual("critic", batch["agent_reviews"][0]["role"])
        self.assertEqual("Check site update", batch["followups"][-1]["title"])
        self.assertEqual("candidate", batch["followups"][-1]["status"])

    def test_append_only_events_can_drive_record_candidates(self):
        events_dir = self.data_dir / "records" / "events"
        events_dir.mkdir(parents=True)
        event = {
            "created_at": "2026-05-15T10:00:00",
            "title": "Record synthesis loop implemented",
            "body": "Codex added records/events JSONL input so future agents can preserve valuable work.",
            "destinations": ["memory", "career_log", "public_story", "followup"],
            "visibility": "public",
            "confidence": "high",
            "source_refs": ["docs/superpowers/specs/2026-05-15-record-synthesis-design.md"],
        }
        (events_dir / "2026-05-15.jsonl").write_text(
            json.dumps(event, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        batch = RecordSynthesizer(self.data_dir).run(write=False)

        self.assertIn("Record synthesis loop implemented", json.dumps(batch["memory_candidates"], ensure_ascii=False))
        self.assertIn("Record synthesis loop implemented", json.dumps(batch["career_log_candidates"], ensure_ascii=False))
        self.assertIn("Record synthesis loop implemented", json.dumps(batch["public_story_candidates"], ensure_ascii=False))
        self.assertIn("Record synthesis loop implemented", json.dumps(batch["followups"], ensure_ascii=False))

    def test_public_event_sanitization_removes_sensitive_values(self):
        events_dir = self.data_dir / "records" / "events"
        events_dir.mkdir(parents=True)
        event = {
            "created_at": "2026-05-15T10:30:00",
            "title": "Sensitive public story",
            "body": "proposal_id=proposal-secret-999 reviewer trace says private-token-abc should not leak.",
            "destinations": ["public_story"],
            "visibility": "public",
            "confidence": "high",
            "source_refs": ["records/events/2026-05-15.jsonl"],
        }
        (events_dir / "2026-05-15.jsonl").write_text(
            json.dumps(event, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        batch = RecordSynthesizer(self.data_dir).run(write=False)
        public_text = json.dumps(batch["public_story_candidates"], ensure_ascii=False)

        self.assertNotIn("proposal-secret-999", public_text)
        self.assertNotIn("private-token-abc", public_text)
        self.assertIn("[private]", public_text)

    def test_run_id_uses_uuid_suffix(self):
        batch = RecordSynthesizer(self.data_dir).run(write=False)
        prefix = "record-synthesis-"

        self.assertTrue(batch["run_id"].startswith(prefix))
        uuid.UUID(batch["run_id"][len(prefix):])

    def test_verbose_cli_logs_to_stderr(self):
        stdout = StringIO()
        stderr = StringIO()

        with redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = main(["--data-dir", str(self.data_dir), "--dry-run", "--verbose"])

        self.assertEqual(0, exit_code)
        self.assertIn('"run_id"', stdout.getvalue())
        self.assertIn("Built record synthesis batch", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
