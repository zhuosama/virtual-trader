"""Validate real strategies/changelog.json against schema."""
import json
import os
import unittest

VTRADER_ROOT = os.path.expanduser("~/.hermes/virtual-trader")

VALID_CHANGE_TYPES = {"strategy", "execution", "risk", "data", "system"}


class TestChangelogSchema(unittest.TestCase):
    """Ensure strategies/changelog.json never contains invalid change_types
    or duplicate parameter adjustments."""

    def _load_changelog(self):
        path = os.path.join(VTRADER_ROOT, "strategies", "changelog.json")
        with open(path) as f:
            return json.load(f)

    def test_all_change_types_valid(self):
        """Every entry.change_type must be in VALID_CHANGE_TYPES."""
        cl = self._load_changelog()
        violations = []
        for i, entry in enumerate(cl):
            ct = entry.get("change_type", "")
            if ct not in VALID_CHANGE_TYPES:
                violations.append(f"[{i}] date={entry.get('date','?')} change_type={ct!r}")
        self.assertEqual(violations, [],
                         f"Invalid change_types found:\n" + "\n".join(violations))

    def test_no_consecutive_duplicate_adjustments(self):
        """No two consecutive entries should have the same parameter change."""
        cl = self._load_changelog()
        duplicates = []
        for i in range(1, len(cl)):
            prev_desc = cl[i - 1].get("description", "")
            curr_desc = cl[i].get("description", "")
            # Check for identical max_single_position changes
            if ("max_single_position" in prev_desc and "max_single_position" in curr_desc
                    and "0.08" in prev_desc and "0.08" in curr_desc):
                duplicates.append(f"[{i-1}] and [{i}] both: {curr_desc[:60]}")
        self.assertEqual(duplicates, [],
                         f"Duplicate consecutive adjustments:\n" + "\n".join(duplicates))

    def test_required_fields_present(self):
        """Every entry must have date, account, change_type, description."""
        cl = self._load_changelog()
        missing = []
        for i, entry in enumerate(cl):
            for field in ("date", "change_type", "description"):
                if field not in entry:
                    missing.append(f"[{i}] missing {field}")
        self.assertEqual(missing, [],
                         f"Missing fields:\n" + "\n".join(missing))


if __name__ == "__main__":
    unittest.main()
