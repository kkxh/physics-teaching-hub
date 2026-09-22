"""仓库约定的冒烟测试（配置用例见 test_config.py，演示数据用例见 test_demo_data.py）。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

class RepositoryConventionTests(unittest.TestCase):
    def test_gitignore_blocks_the_default_deny_list(self):
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

        for entry in ("*.db", "outputs/", "demo/", "config.toml", ".privacy-terms.local"):
            self.assertIn(entry, gitignore, msg=f".gitignore 缺少拒绝项：{entry}")

    def test_privacy_scanner_exists(self):
        scanner = ROOT / "scripts" / "privacy_scan.sh"

        self.assertTrue(scanner.is_file())
        self.assertIn("privacy-terms.local", scanner.read_text(encoding="utf-8"))

    def test_agents_md_states_one_way_migration_rule(self):
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")

        self.assertIn("单向", agents)
        self.assertIn("永不引入真实数据", agents)


if __name__ == "__main__":
    unittest.main()
