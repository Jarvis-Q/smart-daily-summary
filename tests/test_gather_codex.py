# tests/test_gather_codex.py
# gather_codex.py 的单元测试。rollout jsonl 全部为虚构固件，不读真实 ~/.codex。
import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import gather_codex as gc  # noqa: E402

LOCAL_TZ = datetime.now().astimezone().tzinfo
TARGET = date(2026, 7, 21)
YESTERDAY = date(2026, 7, 20)


def _ts(d, h, m, s=0):
    return datetime(d.year, d.month, d.day, h, m, s, tzinfo=LOCAL_TZ).isoformat()


def _meta(cwd, ts):
    return {"timestamp": ts, "ordinal": 0, "type": "session_meta",
            "payload": {"cwd": cwd, "timestamp": ts, "session_id": "s-1"}}


def _usage(ts, inp, out, cached=0, cache_write=0):
    return {"timestamp": ts, "type": "token_usage_record",
            "payload": {"usage": {"input_tokens": inp, "output_tokens": out,
                                  "cached_input_tokens": cached,
                                  "cache_write_input_tokens": cache_write}}}


def _token_count(ts, inp, out, cached=0):
    # 旧版回退口径：event_msg/token_count 的 last_token_usage
    return {"timestamp": ts, "type": "event_msg",
            "payload": {"type": "token_count",
                        "info": {"last_token_usage": {"input_tokens": inp,
                                                      "output_tokens": out,
                                                      "cached_input_tokens": cached}}}}


def _item(ts, itype, text=None):
    item = {"type": itype, "id": "x"}
    if text is not None:
        item["content"] = [{"type": "text", "text": text}]
    return {"timestamp": ts, "type": "event_msg",
            "payload": {"type": "item_completed", "item": item}}


def _task_started(ts):
    return {"timestamp": ts, "type": "event_msg", "payload": {"type": "task_started"}}


def _write(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _touch_mtime(path, when):
    ts = when.timestamp()
    os.utime(path, (ts, ts))


class TestCollect(unittest.TestCase):
    """按 cwd 末段分组聚合；token/轮次/会话数/请求摘要/跨日过滤。"""

    def test_aggregates_by_cwd_basename(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "2026/07/21/rollout-a.jsonl", [
                _meta("/repo/alpha", _ts(TARGET, 9, 0)),
                _task_started(_ts(TARGET, 9, 0, 1)),
                _item(_ts(TARGET, 9, 0, 2), "UserMessage", "帮我修登录页"),
                _item(_ts(TARGET, 9, 0, 12), "AgentMessage", "好的"),
                _usage(_ts(TARGET, 9, 0, 13), inp=1000, out=200, cached=400),
            ])
            _write(root / "2026/07/21/rollout-b.jsonl", [
                _meta("/repo/alpha", _ts(TARGET, 10, 0)),
                _task_started(_ts(TARGET, 10, 0, 1)),
                _usage(_ts(TARGET, 10, 0, 2), inp=500, out=100),
            ])
            data = gc.collect(TARGET, root=root)
            self.assertEqual(set(data["projects"]), {"alpha"})
            b = data["projects"]["alpha"]
            self.assertEqual(b["sessions"], 2)
            self.assertEqual(b["user_turns"], 2)
            self.assertEqual(b["assistant_count"], 1)
            # 输入 1000 含缓存 400 → in=600、cache_read=400；另加第二会话 500/100
            self.assertEqual(b["in"], 1100)
            self.assertEqual(b["out"], 300)
            self.assertEqual(b["cache_read"], 400)
            self.assertEqual(b["requests"], ["帮我修登录页"])
            self.assertEqual(data["cwds"], {"/repo/alpha"})

    def test_excludes_other_days(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "rollout-a.jsonl", [
                _meta("/repo/alpha", _ts(YESTERDAY, 9, 0)),
                _usage(_ts(YESTERDAY, 9, 1), inp=100, out=50),
                _usage(_ts(TARGET, 9, 2), inp=200, out=60),  # 跨日文件里的当日部分
            ])
            data = gc.collect(TARGET, root=root)
            b = data["projects"]["alpha"]
            self.assertEqual(b["in"], 200)
            self.assertEqual(b["out"], 60)

    def test_falls_back_to_token_count_when_no_usage_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "rollout-old.jsonl", [
                _meta("/repo/alpha", _ts(TARGET, 9, 0)),
                _token_count(_ts(TARGET, 9, 1), inp=800, out=120, cached=300),
                _token_count(_ts(TARGET, 9, 2), inp=900, out=130, cached=100),
            ])
            data = gc.collect(TARGET, root=root)
            b = data["projects"]["alpha"]
            # last_token_usage 逐条累加：(800-300)+(900-100)=1300；cache_read=400
            self.assertEqual(b["in"], 1300)
            self.assertEqual(b["out"], 250)
            self.assertEqual(b["cache_read"], 400)

    def test_usage_record_wins_over_token_count(self):
        """同一文件两种口径并存时只用 token_usage_record，避免双计。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "rollout-a.jsonl", [
                _meta("/repo/alpha", _ts(TARGET, 9, 0)),
                _usage(_ts(TARGET, 9, 1), inp=1000, out=200),
                _token_count(_ts(TARGET, 9, 1), inp=1000, out=200),
            ])
            data = gc.collect(TARGET, root=root)
            self.assertEqual(data["projects"]["alpha"]["in"], 1000)

    def test_skips_system_wrapped_and_empty_user_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "rollout-a.jsonl", [
                _meta("/repo/alpha", _ts(TARGET, 9, 0)),
                _item(_ts(TARGET, 9, 0, 1), "UserMessage", "<skills_instructions>注入</skills_instructions>"),
                _item(_ts(TARGET, 9, 0, 2), "UserMessage", "  "),
                _item(_ts(TARGET, 9, 0, 3), "UserMessage", "真实问题"),
            ])
            data = gc.collect(TARGET, root=root)
            self.assertEqual(data["projects"]["alpha"]["requests"], ["真实问题"])

    def test_request_count_matches_real_user_questions(self):
        """连接数（AI 请求数）：两条真实提问 + 一条系统包裹，应只计 2。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "2026/07/21/rollout-a.jsonl", [
                _meta("/repo/alpha", _ts(TARGET, 9, 0)),
                _item(_ts(TARGET, 9, 0, 1), "UserMessage", "第一个问题"),
                _item(_ts(TARGET, 9, 0, 2), "AgentMessage", "答"),
                _item(_ts(TARGET, 9, 0, 3), "UserMessage", "<系统包裹>"),
                _item(_ts(TARGET, 9, 0, 4), "UserMessage", "第二个问题"),
            ])
            _write(root / "2026/07/21/rollout-b.jsonl", [
                _meta("/repo/alpha", _ts(TARGET, 10, 0)),
                _item(_ts(TARGET, 10, 0, 1), "UserMessage", "另一会话的提问"),
            ])
            data = gc.collect(TARGET, root=root)
            b = data["projects"]["alpha"]
            self.assertEqual(len(b["requests"]), 3)
            self.assertEqual(b["request_count"], 3)

    def test_old_mtime_file_skipped_fast_path(self):
        """mtime 早于目标日 00:00 的文件不解析——里面不可能有当日活动。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            f = root / "rollout-a.jsonl"
            _write(f, [
                _meta("/repo/alpha", _ts(TARGET, 9, 0)),
                _usage(_ts(TARGET, 9, 1), inp=100, out=50),
            ])
            day_start = datetime(TARGET.year, TARGET.month, TARGET.day)
            _touch_mtime(f, day_start - timedelta(days=1))
            data = gc.collect(TARGET, root=root)
            self.assertEqual(data["projects"], {})

    def test_missing_root_returns_empty(self):
        data = gc.collect(TARGET, root="/nonexistent/codex/sessions")
        self.assertEqual(data, {"projects": {}, "cwds": set()})

    def test_missing_cwd_falls_back_to_unknown_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "rollout-a.jsonl", [
                _meta("", _ts(TARGET, 9, 0)),
                _usage(_ts(TARGET, 9, 1), inp=100, out=50),
            ])
            data = gc.collect(TARGET, root=root)
            self.assertIn("未知目录", data["projects"])


class TestComputeCodexDurations(unittest.TestCase):
    """耗时口径：agent 前的等待=思考，tool 前的等待=编码，user 前=人的时间丢弃。"""

    def _ev(self, offset_s, kind, base=None):
        base = base or datetime(2026, 7, 21, 10, 0, 0, tzinfo=LOCAL_TZ)
        return {"t": base + timedelta(seconds=offset_s), "kind": kind}

    def test_splits_think_and_tool(self):
        # 提问 → 10s 后模型回复（思考 10s）→ 30s 后工具完成（编码 30s）
        # → 5s 后模型总结（思考 5s）→ 60s 后用户再问（人的时间，丢弃）
        events = [
            self._ev(0, "user"),
            self._ev(10, "agent"),
            self._ev(40, "tool"),
            self._ev(45, "agent"),
            self._ev(105, "user"),
        ]
        think_s, tool_s = gc.compute_codex_durations(events)
        self.assertAlmostEqual(think_s, 15.0)
        self.assertAlmostEqual(tool_s, 30.0)

    def test_caps_abnormal_gap(self):
        events = [self._ev(0, "user"), self._ev(400, "agent")]
        think_s, tool_s = gc.compute_codex_durations(events)
        self.assertAlmostEqual(think_s, 300.0)  # 封顶 MAX_GAP_S
        self.assertAlmostEqual(tool_s, 0.0)

    def test_negative_gap_zeroed(self):
        events = [self._ev(10, "agent"), self._ev(0, "tool")]
        think_s, tool_s = gc.compute_codex_durations(events)
        self.assertAlmostEqual(tool_s, 0.0)


if __name__ == "__main__":
    unittest.main()
