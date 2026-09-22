"""骨架阶段的冒烟测试：演示数据与仓库约定（配置用例见 test_config.py）。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import labels as labels_module  # noqa: E402
import seed_demo_data  # noqa: E402


class DemoDataTests(unittest.TestCase):
    def test_demo_dataset_is_fictional_and_self_consistent(self):
        dataset = seed_demo_data.build_dataset()
        pattern = seed_demo_data.fictional_name_pattern(
            labels_module.load_labels().get("demo.student_name_prefix")
        )

        self.assertEqual(seed_demo_data.check_dataset(dataset), [])
        students = [s for k in dataset["classes"] for s in k["students"]]
        self.assertEqual(len(students), len(seed_demo_data.CLASS_NAMES) * seed_demo_data.STUDENTS_PER_CLASS)
        for student in students:
            self.assertTrue(
                pattern.match(student["name"]),
                msg=f"演示数据里出现了非虚构姓名：{student['name']}",
            )

    def test_demo_dataset_is_reproducible(self):
        self.assertEqual(
            seed_demo_data.build_dataset(seed=7),
            seed_demo_data.build_dataset(seed=7),
        )
        self.assertNotEqual(
            seed_demo_data.build_dataset(seed=7),
            seed_demo_data.build_dataset(seed=8),
        )

    def test_seed_script_check_mode_succeeds_without_writing(self):
        self.assertEqual(seed_demo_data.main(["--check"]), 0)
        self.assertFalse(Path("demo").exists() and any(Path("demo").iterdir()))


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
