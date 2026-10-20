# tests/test_projects.py
# projects.py 的单元测试。全部使用虚构项目数据，HTTP 请求一律打桩，绝不连真实服务器。
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import projects as pj  # noqa: E402

# 虚构固件：严禁替换为任何真实项目数据
FAKE_ITEMS = [
    {"id": "1000000000000000001", "name": "测试项目甲"},
    {"id": "1000000000000000002", "name": "测试项目乙"},
    {"id": "1000000000000000003", "name": "示例平台丙"},
]


class TestDeriveProjectsUrl(unittest.TestCase):
    """项目接口地址由日报上报地址推导，不新增环境变量。"""

    def test_replaces_last_path_segment(self):
        self.assertEqual(
            pj.derive_projects_url("http://example.internal/open-api/report/worklog"),
            "http://example.internal/open-api/report/projects",
        )

    def test_tolerates_trailing_slash(self):
        self.assertEqual(
            pj.derive_projects_url("http://example.internal/open-api/report/worklog/"),
            "http://example.internal/open-api/report/projects",
        )

    def test_drops_query_string(self):
        self.assertEqual(
            pj.derive_projects_url("http://example.internal/open-api/report/worklog?a=1"),
            "http://example.internal/open-api/report/projects",
        )

    def test_empty_input_returns_empty(self):
        self.assertEqual(pj.derive_projects_url(""), "")

    def test_malformed_url_returns_empty(self):
        self.assertEqual(pj.derive_projects_url("not-a-url"), "")


class TestNormalizeProjects(unittest.TestCase):
    """响应结构归一化为 [{id, name}]，兼容常见的嵌套包装。"""

    def test_plain_list(self):
        payload = [{"id": 1001, "name": "测试项目甲"}]
        self.assertEqual(pj.normalize_projects(payload),
                         [{"id": "1001", "name": "测试项目甲"}])

    def test_wrapped_in_data(self):
        payload = {"code": 200, "data": [{"id": 1001, "name": "测试项目甲"}]}
        self.assertEqual(pj.normalize_projects(payload),
                         [{"id": "1001", "name": "测试项目甲"}])

    def test_wrapped_in_data_list(self):
        payload = {"data": {"list": [{"projectId": 1002, "projectName": "测试项目乙"}]}}
        self.assertEqual(pj.normalize_projects(payload),
                         [{"id": "1002", "name": "测试项目乙"}])

    def test_skips_items_missing_id_or_name(self):
        payload = [{"id": 1001}, {"name": "无 id"}, {"id": 1003, "name": "示例平台丙"}]
        self.assertEqual(pj.normalize_projects(payload),
                         [{"id": "1003", "name": "示例平台丙"}])

    def test_unknown_structure_returns_empty(self):
        self.assertEqual(pj.normalize_projects({"code": 500, "msg": "错误"}), [])

    def test_deeply_nested_wrapper_does_not_crash(self):
        """病态的自嵌套 dict（约 1000 层）必须安全返回空列表，不能撑爆调用栈。"""
        payload = {"no_list_here": "x"}
        for _ in range(1000):
            payload = {"data": payload}
        self.assertEqual(pj._extract_list(payload), [])
        self.assertEqual(pj.normalize_projects(payload), [])


class TestSearchProjects(unittest.TestCase):
    """关键词检索，输出上限 5 条——绝不返回全量列表。"""

    def test_matches_by_name_substring(self):
        got = pj.search_projects("甲", FAKE_ITEMS)
        self.assertEqual(got, [FAKE_ITEMS[0]])

    def test_match_is_case_insensitive(self):
        items = [{"id": "1", "name": "Alpha 平台"}]
        self.assertEqual(pj.search_projects("alpha", items), items)

    def test_no_match_returns_empty(self):
        self.assertEqual(pj.search_projects("不存在的关键词", FAKE_ITEMS), [])

    def test_result_is_capped_at_five(self):
        many = [{"id": str(i), "name": f"测试项目{i}"} for i in range(20)]
        self.assertEqual(len(pj.search_projects("测试项目", many)), 5)

    def test_empty_keyword_also_capped(self):
        many = [{"id": str(i), "name": f"测试项目{i}"} for i in range(20)]
        self.assertEqual(len(pj.search_projects("", many)), 5)


class TestResolveProjectId(unittest.TestCase):
    """项目名转 id，从严匹配：strip 后精确比对。"""

    def test_exact_name_matches(self):
        self.assertEqual(pj.resolve_project_id("测试项目甲", FAKE_ITEMS),
                         "1000000000000000001")

    def test_strips_whitespace(self):
        self.assertEqual(pj.resolve_project_id("  测试项目甲 ", FAKE_ITEMS),
                         "1000000000000000001")

    def test_partial_name_does_not_match(self):
        self.assertEqual(pj.resolve_project_id("测试项目", FAKE_ITEMS), "")

    def test_empty_name_returns_empty(self):
        self.assertEqual(pj.resolve_project_id("", FAKE_ITEMS), "")


class TestCache(unittest.TestCase):
    """缓存文件与目录必须以 0600 / 0700 权限创建——内部数据不得对其他用户可读。"""

    def test_saves_and_loads_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sub" / "projects.json"
            pj.save_cache(FAKE_ITEMS, path)
            self.assertEqual(pj.load_cache(path), FAKE_ITEMS)

    def test_file_permission_is_0600(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sub" / "projects.json"
            pj.save_cache(FAKE_ITEMS, path)
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)

    def test_dir_permission_is_0700(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sub" / "projects.json"
            pj.save_cache(FAKE_ITEMS, path)
            self.assertEqual(stat.S_IMODE(os.stat(path.parent).st_mode), 0o700)

    def test_multi_level_directories_all_created_as_0700(self):
        """多级缓存路径下，parents=True 隐式创建的中间层目录也必须是 0700，
        不能只有最终一级受管控（否则中间层会停留在系统默认权限上）。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a" / "b" / "c" / "projects.json"
            pj.save_cache(FAKE_ITEMS, path)
            for sub in ("a", "a/b", "a/b/c"):
                mode = stat.S_IMODE(os.stat(Path(tmp) / sub).st_mode)
                self.assertEqual(mode, 0o700, f"{sub} 权限应为 0700，实际为 {oct(mode)}")

    def test_existing_directory_not_downgraded(self):
        """目标目录若已存在且权限更宽松（如 0755），不得报错也不得修改其权限；
        文件本身仍必须是 0600。"""
        with tempfile.TemporaryDirectory() as tmp:
            target_dir = Path(tmp) / "existing"
            target_dir.mkdir()
            os.chmod(target_dir, 0o755)  # 显式设为精确的 0755，不受创建时 umask 影响
            path = target_dir / "projects.json"
            pj.save_cache(FAKE_ITEMS, path)
            self.assertEqual(stat.S_IMODE(os.stat(target_dir).st_mode), 0o755)
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)

    def test_process_umask_not_modified(self):
        """save_cache 不得触碰进程级 umask——那是全局状态，会影响同进程内
        其他线程此刻创建的无关目录（早前用临时收紧 umask 实现 0700 的方案
        已因此被弃用，改为逐级 os.mkdir(d, 0o700)）。"""
        original = os.umask(0o022)
        os.umask(original)  # 仅用于读取当前值，读完立即恢复
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "u" / "v" / "projects.json"
                pj.save_cache(FAKE_ITEMS, path)
            after = os.umask(0o022)
            os.umask(after)  # 同样读完立即恢复
            self.assertEqual(after, original)
        finally:
            os.umask(original)  # 无论断言是否通过，都确保恢复测试运行前的 umask

    def test_load_missing_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(pj.load_cache(Path(tmp) / "absent.json"), [])

    def test_load_corrupt_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text("{ 不是合法 JSON", encoding="utf-8")
            self.assertEqual(pj.load_cache(path), [])


class TestLoadOrFetch(unittest.TestCase):
    """缓存缺失时自动拉取一次；缓存存在时不发起任何网络请求。"""

    def test_fetches_when_cache_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "projects.json"
            with mock.patch.object(pj, "cache_file", return_value=path), \
                 mock.patch.object(pj, "fetch_projects", return_value=FAKE_ITEMS) as fetch:
                got = pj.load_or_fetch()
            fetch.assert_called_once()
            self.assertEqual(got, FAKE_ITEMS)
            self.assertEqual(pj.load_cache(path), FAKE_ITEMS)  # 已落盘

    def test_uses_cache_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "projects.json"
            pj.save_cache(FAKE_ITEMS, path)
            with mock.patch.object(pj, "cache_file", return_value=path), \
                 mock.patch.object(pj, "fetch_projects") as fetch:
                got = pj.load_or_fetch()
            fetch.assert_not_called()
            self.assertEqual(got, FAKE_ITEMS)


class TestFetchProjects(unittest.TestCase):
    """HTTP 请求打桩：校验地址推导、认证头与响应归一化。"""

    def test_sends_api_key_header_and_normalizes(self):
        response = mock.Mock(ok=True)
        response.json.return_value = {"data": [{"id": 1001, "name": "测试项目甲"}]}
        env = {"SERVER_URL": "http://example.internal/open-api/report/worklog",
               "DAILY_SUMMARY_TOKEN": "fake-token"}
        with mock.patch.dict(os.environ, env, clear=False), \
             mock.patch.object(pj.requests, "get", return_value=response) as get:
            got = pj.fetch_projects()
        get.assert_called_once()
        self.assertEqual(get.call_args.args[0],
                         "http://example.internal/open-api/report/projects")
        self.assertEqual(get.call_args.kwargs["headers"]["X-Api-Key"], "fake-token")
        self.assertEqual(got, [{"id": "1001", "name": "测试项目甲"}])

    def test_http_failure_raises_without_leaking_url(self):
        response = mock.Mock(ok=False, status_code=403)
        env = {"SERVER_URL": "http://example.internal/open-api/report/worklog",
               "DAILY_SUMMARY_TOKEN": "fake-token"}
        with mock.patch.dict(os.environ, env, clear=False), \
             mock.patch.object(pj.requests, "get", return_value=response):
            with self.assertRaises(RuntimeError) as ctx:
                pj.fetch_projects()
        message = str(ctx.exception)
        self.assertIn("403", message)
        # 报错信息不得回显完整地址与 token
        self.assertNotIn("example.internal", message)
        self.assertNotIn("fake-token", message)


if __name__ == "__main__":
    unittest.main()
