import ast
import os
import unittest

VTRADER_ROOT = os.path.expanduser("~/.hermes/virtual-trader")
PRIVATE_TARGETS = {"_save_strategies", "_save_changelog", "_apply_parameter_adjustment", "_apply_rule_adjustment"}
WHITELIST_FILES = {
    "agents/strategy_maintainer.py",  # owner: 内部可以调
    "agents/audit_layer.py",          # audit_layer 是合法的 commit_approved 触发者
}


class TestNoBypass(unittest.TestCase):
    def _find_py_files(self):
        for dirpath, dirnames, filenames in os.walk(VTRADER_ROOT):
            # skip .git, __pycache__, backups, tests
            dirnames[:] = [d for d in dirnames if d not in {".git", "__pycache__", "backups", "tests", "node_modules"}]
            for fn in filenames:
                if fn.endswith(".py"):
                    full = os.path.join(dirpath, fn)
                    rel = os.path.relpath(full, VTRADER_ROOT)
                    yield rel, full

    def test_no_external_call_to_private_writers(self):
        offenders = []
        for rel, full in self._find_py_files():
            if rel in WHITELIST_FILES:
                continue
            with open(full) as f:
                try:
                    tree = ast.parse(f.read(), filename=full)
                except SyntaxError:
                    continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr in PRIVATE_TARGETS:
                    offenders.append(f"{rel}:{node.lineno} accesses .{node.attr}")
                elif isinstance(node, ast.Name) and node.id in PRIVATE_TARGETS:
                    offenders.append(f"{rel}:{node.lineno} references {node.id}")
        self.assertEqual(offenders, [], "External bypass call sites found:\n" + "\n".join(offenders))


if __name__ == "__main__":
    unittest.main()
