"""P1.7 收口测试：可归档验证、隐私扫描的三种模式、跟踪文件卫生。"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SCAN = ROOT / "scripts" / "privacy_scan.sh"
LOOP_COMMANDS = (
    ("init_db.py", ("--demo",)),
    ("import_scores.py", ()),
    ("make_report.py", ()),
)

DATABASE = ROOT / "data" / "physics_teaching.db"
GENERATED_DIRS = (ROOT / "demo", ROOT / "outputs")


def run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, cwd=cwd, capture_output=True, text=True, check=False
    )


def extract_archive(target: Path) -> None:
    """把 HEAD 导出成归档再解包，用来验证「不依赖任何未跟踪文件」。"""
    archive = target.parent / "hub.tar"
    run(["git", "archive", "-o", str(archive), "HEAD"], ROOT)
    with tarfile.open(archive) as tar:
        try:
            tar.extractall(target, filter="data")
        except TypeError:  # Python 3.11 早期版本没有 filter 参数
            tar.extractall(target)


class PrivacyScanTests(unittest.TestCase):
    def run_scan(self, *args: str) -> subprocess.CompletedProcess[str]:
        return run(["bash", str(SCAN), *args], ROOT)

    def ensure_loop_artifacts(self) -> None:
        """在仓库里生成最小闭环产物；已有本机数据时不动它，只清理自己建的那份。"""
        if DATABASE.exists() or any(path.exists() for path in GENERATED_DIRS):
            return

        for script, extra in LOOP_COMMANDS:
            result = run(
                [
                    sys.executable,
                    str(ROOT / script),
                    "--config",
                    str(ROOT / "config.example.toml"),
                    *extra,
                ],
                ROOT,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)

        def cleanup() -> None:
            if DATABASE.exists():
                DATABASE.unlink()
            for path in GENERATED_DIRS:
                if path.is_dir():
                    shutil.rmtree(path)

        self.addCleanup(cleanup)

    def test_scan_passes_with_loop_artifacts_in_the_worktree(self):
        self.ensure_loop_artifacts()

        tracked = self.run_scan()
        worktree = self.run_scan("--all")

        self.assertEqual(tracked.returncode, 0, msg=tracked.stdout + tracked.stderr)
        self.assertEqual(worktree.returncode, 0, msg=worktree.stdout + worktree.stderr)

    def test_strict_scan_reports_local_artifacts(self):
        self.ensure_loop_artifacts()

        result = self.run_scan("--strict")

        self.assertEqual(result.returncode, 1)
        self.assertIn("[禁止入仓]", result.stdout)

    def test_strict_scan_flags_stray_files_under_data(self):
        stray = ROOT / "data" / "stray_scores.json"
        stray.parent.mkdir(parents=True, exist_ok=True)
        stray.write_text('{"note": "不该进仓库的临时文件"}\n', encoding="utf-8")
        self.addCleanup(lambda: stray.unlink(missing_ok=True))

        before = self.run_scan("--strict")

        self.assertIn("data/stray_scores.json", before.stdout)

        stray.unlink()
        after = self.run_scan("--strict")

        self.assertNotIn("data/stray_scores.json", after.stdout)

    def test_data_readme_is_allowed(self):
        result = self.run_scan()

        self.assertEqual(result.returncode, 0, msg=result.stdout)
        self.assertIn("data/README.md", subprocess.run(
            ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True
        ).stdout)

    def test_float_digits_do_not_trip_the_phone_pattern(self):
        """浮点数的长数字串不该被当成手机号（P2.9 的看板数据里就有）。"""
        probe = ROOT / "privacy_probe_float.txt"
        probe.write_text(
            'average = 73.61666666666666\nrate = 0.9166666666666666\n',
            encoding="utf-8",
        )
        self.addCleanup(lambda: probe.unlink(missing_ok=True))

        result = self.run_scan("--all")

        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        self.assertNotIn("privacy_probe_float.txt", result.stdout)

    def test_real_looking_phone_number_is_still_caught(self):
        probe = ROOT / "privacy_probe_phone.txt"
        # 号码在运行时拼出来：避免测试文件本身留下一个"像手机号"的字面量
        fake_phone = "1" + "38" + "1234" + "5678"
        probe.write_text(f"联系电话：{fake_phone}\n", encoding="utf-8")
        self.addCleanup(lambda: probe.unlink(missing_ok=True))

        # --strict 用 find 扫整个工作区（不依赖 git 列表），最稳；
        # --all 走 git 列表，两种都断言，出问题时日志里能分辨是哪一环
        strict = self.run_scan("--strict")
        self.assertEqual(strict.returncode, 1, msg=strict.stdout + strict.stderr)
        self.assertIn("privacy_probe_phone.txt", strict.stdout)

        worktree = self.run_scan("--all")
        self.assertEqual(worktree.returncode, 1, msg=worktree.stdout + worktree.stderr)
        self.assertIn("privacy_probe_phone.txt", worktree.stdout)


class HistoryScanTests(unittest.TestCase):
    """--history 模式：扫的是历史对象与提交信息，不只是当前工作区。

    用临时 git 仓库做端到端验证（把脚本本身复制进去，脚本按自身位置定位仓库根）。
    """

    def make_repo(self, tmp: Path, *, init: bool = True) -> Path:
        repo = tmp / "history-repo"
        repo.mkdir()
        if init:
            run(["git", "init", "-q"], repo)
        script = repo / "scripts" / "privacy_scan.sh"
        script.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(SCAN, script)
        return repo

    def commit(self, repo: Path, files: dict[str, str], message: str) -> None:
        for name, text in files.items():
            path = repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        run(["git", "add", "-A"], repo)
        run(
            [
                "git",
                "-c",
                "user.name=tester",
                "-c",
                "user.email=tester@example.com",
                "commit",
                "-qm",
                message,
            ],
            repo,
        )

    def scan(self, repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return run(["bash", "scripts/privacy_scan.sh", *args], repo)

    def test_history_mode_finds_content_that_is_gone_from_the_worktree(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.make_repo(Path(tmp))
            fake_phone = "1" + "38" + "1234" + "5678"
            self.commit(repo, {"notes.txt": f"联系电话：{fake_phone}\n"}, "加一条笔记")
            self.commit(repo, {"notes.txt": "已经清理\n"}, "清理笔记")

            current = self.scan(repo)
            history = self.scan(repo, "--history")

        self.assertEqual(current.returncode, 0, msg=current.stdout + current.stderr)
        self.assertEqual(history.returncode, 1, msg=history.stdout + history.stderr)
        self.assertIn("历史文件版本", history.stdout)

    def test_history_mode_scans_commit_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.make_repo(Path(tmp))
            fake_phone = "1" + "38" + "1234" + "5678"
            self.commit(repo, {"readme.txt": "干净内容\n"}, f"联系 {fake_phone} 确认")

            current = self.scan(repo)
            history = self.scan(repo, "--history")

        self.assertEqual(current.returncode, 0, msg=current.stdout + current.stderr)
        self.assertEqual(history.returncode, 1, msg=history.stdout + history.stderr)
        self.assertIn("提交信息", history.stdout)

    def test_history_mode_finds_a_denied_path_in_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.make_repo(Path(tmp))
            self.commit(repo, {"outputs/report.md": "演示报告\n"}, "误提交一份生成物")
            self.commit(repo, {"outputs/.keep": ""}, "把生成物清掉")

            history = self.scan(repo, "--history")

        self.assertEqual(history.returncode, 1, msg=history.stdout + history.stderr)
        self.assertIn("历史提交里出现过", history.stdout)

    def test_history_mode_needs_a_git_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.make_repo(Path(tmp), init=False)

            result = self.scan(repo, "--history")

        self.assertEqual(result.returncode, 2)
        self.assertIn("需要 git 仓库", result.stdout + result.stderr)

    def test_history_mode_passes_on_this_repository(self):
        result = run(["bash", str(SCAN), "--history"], ROOT)

        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        self.assertIn("历史文件版本", result.stdout)


class ArchiveTests(unittest.TestCase):
    def test_archive_runs_the_loop_without_untracked_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "extract"
            target.mkdir()
            extract_archive(target)

            self.assertFalse((target / "config.toml").exists())
            self.assertFalse((target / "data" / "physics_teaching.db").exists())

            for script, extra in LOOP_COMMANDS:
                result = run(
                    [sys.executable, str(target / script), *extra], target
                )
                self.assertEqual(
                    result.returncode,
                    0,
                    msg=f"{script} 失败：{result.stdout}{result.stderr}",
                )

            report = target / "outputs" / "phase1_report.md"
            database = target / "data" / "physics_teaching.db"
            self.assertTrue(database.is_file())
            self.assertTrue(report.is_file())
            self.assertIn("教学周", report.read_text(encoding="utf-8"))


class TrackedFileTests(unittest.TestCase):
    def test_tracked_files_avoid_the_deny_list(self):
        files = subprocess.run(
            ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.split()

        offenders = [
            name
            for name in files
            if name.endswith((".db", ".sqlite", ".sqlite3"))
            or name.startswith(("demo/", "outputs/", "tmp/"))
            or (name.startswith("data/") and name != "data/README.md")
        ]

        self.assertEqual(offenders, [])

    def test_archive_does_not_contain_generated_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "extract"
            target.mkdir()
            extract_archive(target)

            entries = sorted(
                str(path.relative_to(target))
                for path in target.rglob("*")
                if path.is_file()
            )

        self.assertNotIn("config.toml", entries)
        self.assertFalse([name for name in entries if name.endswith(".db")])
        self.assertFalse([name for name in entries if name.startswith("outputs/")])


class WorkflowTests(unittest.TestCase):
    """守住 CI 的两条底线：最小权限 + 不退回被弃用的 action 主版本。"""

    def setUp(self):
        self.text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    def test_workflow_requests_read_only_permissions(self):
        self.assertIn("permissions:", self.text)
        self.assertIn("contents: read", self.text)

    def test_workflow_uses_current_action_majors(self):
        for action in ("actions/checkout@v7", "actions/setup-python@v7"):
            with self.subTest(action=action):
                self.assertIn(action, self.text, msg=f"{action} 该升级了")
        for stale in ("actions/checkout@v4", "actions/setup-python@v5"):
            with self.subTest(stale=stale):
                self.assertNotIn(stale, self.text)

    def test_workflow_covers_minimum_and_latest_python(self):
        self.assertIn('python-version: ["3.11", "3.14"]', self.text)

    def test_workflow_runs_both_privacy_scan_modes(self):
        self.assertIn("bash scripts/privacy_scan.sh", self.text)
        self.assertIn("bash scripts/privacy_scan.sh --all", self.text)


if __name__ == "__main__":
    unittest.main()
