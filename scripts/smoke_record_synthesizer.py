import json
import os
import sys
import tempfile


WORKTREE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, WORKTREE)

from agents.record_synthesizer import RecordSynthesizer


with tempfile.TemporaryDirectory() as tmp:
    os.makedirs(os.path.join(tmp, "strategies"))
    os.makedirs(os.path.join(tmp, "agents", "workflows"))
    os.makedirs(os.path.join(tmp, "reports", "daily"))
    with open(os.path.join(tmp, "strategies", "audit_log.json"), "w", encoding="utf-8") as f:
        f.write("[]")
    with open(os.path.join(tmp, "strategies", "changelog.json"), "w", encoding="utf-8") as f:
        f.write("[]")

    syn = RecordSynthesizer(data_dir=tmp, since_days=7)
    batch = syn.run(write=False)
    print(json.dumps(batch, indent=2, default=str, ensure_ascii=False)[:2000])
    total = sum(
        len(batch.get(k, []))
        for k in (
            "memory_candidates",
            "career_log_candidates",
            "public_story_candidates",
            "risk_note_candidates",
            "followup_candidates",
        )
    )
    print(f"\ntotal candidates with empty inputs: {total}")
    assert total <= 1, "FAIL: synthesizer fabricated candidates from empty data"

print("B-R2 pass")
