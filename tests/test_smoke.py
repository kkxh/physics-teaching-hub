"""骨架阶段的冒烟测试：配置、演示数据与仓库约定。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config_loader  # noqa: E402
import seed_demo_data  # noqa: E402


class ConfigTests(unittest.TestCase):
    def test_example_config_parses_with_expected_defaults(self):
        config = config_loader.load_config(config_loader.EXAMPLE_CONFIG_PATH)

        self.assertEqual(config.project.name, "物理教学中枢")
        self.assertEqual(config.project.stage, "high_school")
        self.assertEqual(config.project.stage_label, "高中")
        self.assertTrue(config.is_high_school)
        self.assertEqual(len(config.class_names), 2)

    def test_middle_school_stage_is_supported(self):
        config = config_loader.parse_config(
            {"project": {"stage": "middle_school"}, "classes": {"names": ["初三(1)班"]}}
        )

        self.assertEqual(config.project.stage_label, "初中")
        self.assertFalse(config.is_high_school)
        self.assertEqual(config.class_names, ("初三(1)班",))

    def test_unknown_stage_is_rejected(self):
        with self.assertRaises(config_loader.ConfigError):
            config_loader.parse_config({"project": {"stage": "kindergarten"}})

    def test_missing_config_file_reports_actionable_error(self):
        with self.assertRaises(config_loader.ConfigError) as ctx:
            config_loader.load_config("does-not-exist.toml")

        self.assertIn("config.example.toml", str(ctx.exception))


class DemoDataTests(unittest.TestCase):
    def test_demo_dataset_is_fictional_and_self_consistent(self):
        dataset = seed_demo_data.build_dataset()

        self.assertEqual(seed_demo_data.check_dataset(dataset), [])
        students = [s for k in dataset["classes"] for s in k["students"]]
        self.assertEqual(len(students), len(seed_demo_data.CLASS_NAMES) * seed_demo_data.STUDENTS_PER_CLASS)
        for student in students:
            self.assertTrue(
                seed_demo_data.FICTIONAL_NAME_PATTERN.match(student["name"]),
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
