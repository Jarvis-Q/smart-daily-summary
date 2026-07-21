# tests/test_gather_data.py
# gather_data.py 核心纯函数的单元测试（标准库 unittest，零外部依赖）。
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# 让测试能 import 同仓库 scripts 目录下的 gather_data
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import gather_data as gd  # noqa: E402


def _ev(offset_s, typ, tool_use=False, tool_result=False, base=None):
    """构造单条事件。base 为基准时刻，offset_s 为相对秒偏移。"""
    base = base or datetime(2026, 7, 21, 10, 0, 0, tzinfo=timezone.utc)
    return {
        "t": base + timedelta(seconds=offset_s),
        "typ": typ,
        "tool_use": tool_use,
        "tool_result": tool_result,
    }


class TestComputeAiDurations(unittest.TestCase):
    """移植自 cc-usage 的耗时口径：思考=模型响应延迟，编码=工具执行时长。"""

    def test_splits_think_and_tool_time(self):
        # 提问 → 10s后assistant发起工具 → 30s后工具结果返回 → 5s后assistant最终回复
        events = [
            _ev(0, "user"),                                   # 用户提问
            _ev(10, "assistant", tool_use=True),              # think gap 10s
            _ev(40, "user", tool_result=True),                # tool gap 30s
            _ev(45, "assistant"),                             # think gap 5s
        ]
        think_s, tool_s = gd.compute_ai_durations(events)
        self.assertAlmostEqual(think_s, 15.0)  # 10 + 5
        self.assertAlmostEqual(tool_s, 30.0)   # 工具执行

    def test_caps_abnormal_gap_at_max(self):
        # 用户提问后 400s 才有 assistant 回复（含权限确认/离开），单次封顶 300s
        events = [
            _ev(0, "user"),
            _ev(400, "assistant"),
        ]
        think_s, tool_s = gd.compute_ai_durations(events)
        self.assertAlmostEqual(think_s, 300.0)  # 400 → 截断为 MAX_GAP_S
        self.assertAlmostEqual(tool_s, 0.0)

    def test_discards_human_reading_gap(self):
        # assistant 回复后，用户 60s 后才发下一条（人阅读/思考时间）——该间隔应丢弃
        events = [
            _ev(0, "user"),
            _ev(5, "assistant"),          # think gap 5s
            _ev(65, "user"),              # 人阅读 60s：既非 think 也非 tool，丢弃
        ]
        think_s, tool_s = gd.compute_ai_durations(events)
        self.assertAlmostEqual(think_s, 5.0)
        self.assertAlmostEqual(tool_s, 0.0)


class TestDiscoverWorkDirs(unittest.TestCase):
    """从今日有 Claude 活动的会话 jsonl 中提取所有工作目录（cwd）。"""

    def _write_jsonl(self, path, records):
        with open(path, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")

    def _rec(self, dt, cwd, typ="user"):
        # 顶层 cwd + timestamp，与真实 Claude 会话 jsonl 结构一致
        return {"type": typ, "timestamp": dt.isoformat(), "cwd": cwd,
                "message": {"role": typ, "content": "x"}}

    def test_collects_today_cwds_and_excludes_other_days(self):
        local_tz = datetime.now().astimezone().tzinfo
        target = date(2026, 7, 21)
        today_noon = datetime(2026, 7, 21, 12, 0, tzinfo=local_tz)
        yday_noon = datetime(2026, 7, 20, 12, 0, tzinfo=local_tz)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "projA").mkdir()
            (root / "projB").mkdir()
            # 会话1：今日在 /repo/a 工作（user+assistant 两条）
            self._write_jsonl(root / "projA" / "s1.jsonl", [
                self._rec(today_noon, "/repo/a", "user"),
                self._rec(today_noon + timedelta(seconds=5), "/repo/a", "assistant"),
            ])
            # 会话2：今日在 /repo/b；同文件还有一条昨日 /repo/old（应被排除）
            self._write_jsonl(root / "projB" / "s2.jsonl", [
                self._rec(yday_noon, "/repo/old", "user"),
                self._rec(today_noon, "/repo/b", "user"),
            ])

            dirs = gd.discover_work_dirs(root, target)
            self.assertEqual(dirs, {"/repo/a", "/repo/b"})

    def test_merges_extra_git_dirs(self):
        target = date(2026, 7, 21)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)  # 空 projects：无 Claude 活动
            dirs = gd.discover_work_dirs(root, target, extra_dirs=["~/manual/repo"])
            self.assertIn(str(Path("~/manual/repo").expanduser()), dirs)


@unittest.skipUnless(shutil.which("git"), "需要 git 才能测试仓库归一")
class TestResolveRepoRoots(unittest.TestCase):
    """把工作目录归一到 git 仓库根并去重，跳过非 git / 不存在的目录。"""

    def _init_repo(self, path):
        path.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q"], cwd=path, check=True)

    def test_normalizes_subdir_to_root_and_dedupes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            self._init_repo(repo)
            (repo / "src").mkdir()                 # 仓库子目录
            plain = root / "plain"; plain.mkdir()  # 非 git 目录
            missing = str(root / "gone")           # 不存在的目录

            got = gd.resolve_repo_roots([
                str(repo), str(repo / "src"),      # 同一仓库的根与子目录 → 去重为一个
                str(plain), missing,               # 应被跳过
            ])
            expected = os.path.realpath(repo)      # git show-toplevel 返回真实路径
            self.assertEqual(got, {expected})


if __name__ == "__main__":
    unittest.main()
