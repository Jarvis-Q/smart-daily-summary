# tests/test_send_report.py
# send_report.py 核心纯函数的单元测试（标准库 unittest，零外部依赖）。
import sys
import unittest
from pathlib import Path

# 让测试能 import 同仓库 scripts 目录下的 send_report
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import send_report as sr  # noqa: E402


class TestResolveCategory(unittest.TestCase):
    """分类双向匹配：中文名与 code 都认，从严比对，未命中回退默认值。"""

    CATEGORIES = {"开发": "dev", "修复BUG": "bugfix", "代码审查": "review"}

    def test_matches_chinese_name(self):
        code, matched = sr.resolve_category("开发", self.CATEGORIES, "dev")
        self.assertEqual(code, "dev")
        self.assertTrue(matched)

    def test_matches_code_directly(self):
        code, matched = sr.resolve_category("bugfix", self.CATEGORIES, "dev")
        self.assertEqual(code, "bugfix")
        self.assertTrue(matched)

    def test_code_match_is_case_insensitive(self):
        code, matched = sr.resolve_category("REVIEW", self.CATEGORIES, "dev")
        self.assertEqual(code, "review")
        self.assertTrue(matched)

    def test_strips_surrounding_whitespace(self):
        code, matched = sr.resolve_category("  开发  ", self.CATEGORIES, "dev")
        self.assertEqual(code, "dev")
        self.assertTrue(matched)

    def test_partial_name_does_not_match(self):
        # 从严匹配：「开发工作」不得命中「开发」，必须走兜底
        code, matched = sr.resolve_category("开发工作", self.CATEGORIES, "other")
        self.assertEqual(code, "other")
        self.assertFalse(matched)

    def test_empty_value_falls_back(self):
        code, matched = sr.resolve_category("", self.CATEGORIES, "dev")
        self.assertEqual(code, "dev")
        self.assertFalse(matched)

    def test_none_value_falls_back(self):
        code, matched = sr.resolve_category(None, self.CATEGORIES, "dev")
        self.assertEqual(code, "dev")
        self.assertFalse(matched)


class TestLoadCategories(unittest.TestCase):
    """分类字典从 data/categories.json 读取，覆盖全部 24 项。"""

    def test_loads_full_dictionary(self):
        categories = sr.load_categories()
        self.assertEqual(len(categories), 24)
        self.assertEqual(categories["开发"], "dev")
        self.assertEqual(categories["SQL编写和执行"], "sql_writing_execution")


class TestBuildWorkDetail(unittest.TestCase):
    """workDetail = 任务标题（去掉预估耗时）+ 工作详情条目，不含耗时/Token/分类/项目行。"""

    FULL_CHUNK = """### 任务 2: 重构日报发送链路 (预估总耗时: 3小时)
- 工作分类: 开发
- 所属项目: example-示例项目
- AI 辅助耗时: 1.5 小时
- Token 消耗量: 82000 tokens
- 工作详情:
  - 拆分 gather_data 与 send_report 职责
  - 修复占位符结构化注入的 bug"""

    def test_title_and_details_joined_by_newline(self):
        got = sr.build_work_detail(self.FULL_CHUNK)
        self.assertEqual(
            got,
            "重构日报发送链路\n"
            "- 拆分 gather_data 与 send_report 职责\n"
            "- 修复占位符结构化注入的 bug",
        )

    def test_excludes_metric_and_field_lines(self):
        got = sr.build_work_detail(self.FULL_CHUNK)
        self.assertNotIn("AI 辅助耗时", got)
        self.assertNotIn("Token 消耗量", got)
        self.assertNotIn("工作分类", got)
        self.assertNotIn("所属项目", got)
        self.assertNotIn("预估总耗时", got)

    def test_title_only_when_no_detail_section(self):
        chunk = """### 任务 1: 参加部门例会 (预估总耗时: 1小时)
- 工作分类: 例会
- AI 辅助耗时: 0 小时
- Token 消耗量: 0 tokens"""
        self.assertEqual(sr.build_work_detail(chunk), "参加部门例会")

    def test_handles_chinese_colon_and_parentheses(self):
        chunk = """### 任务 3：修复登录超时（预估总耗时：2小时）
- 工作详情：
  - 定位到会话续期逻辑缺失"""
        self.assertEqual(
            sr.build_work_detail(chunk),
            "修复登录超时\n- 定位到会话续期逻辑缺失",
        )

    def test_normalizes_detail_bullets_without_indent(self):
        # 详情条目未缩进时同样应被收集，并统一为「- 」前缀
        chunk = """### 任务 4: 数据核对 (预估总耗时: 0.5小时)
- 工作详情:
* 核对上月报表口径"""
        self.assertEqual(sr.build_work_detail(chunk), "数据核对\n- 核对上月报表口径")

    def test_ignores_markdown_separator_line(self):
        # 纯分隔线（---）不应被当作详情条目误收集为「- --」
        chunk = """### 任务 8: 分隔符测试 (预估总耗时: 1小时)
- 工作详情:
  - 条目A

---
### 任务 9: 下一个任务
- 工作分类: 开发"""
        got = sr.build_work_detail(chunk)
        self.assertEqual(got, "分隔符测试\n- 条目A")
        self.assertNotIn("--", got)

    def test_inline_detail_text_on_field_line_is_kept(self):
        # 内联写法「- 工作详情: 一句话」：冒号后的文本必须收进详情，不能静默丢失
        chunk = """### 任务 5: 内联详情 (预估总耗时: 1小时)
- 工作分类: 开发
- 工作详情: 只写了一句话没有子条目"""
        self.assertEqual(
            sr.build_work_detail(chunk),
            "内联详情\n- 只写了一句话没有子条目",
        )

    def test_field_value_containing_detail_keyword_does_not_leak(self):
        # 其他字段的「值」里含「工作详情」四字时，不得被误判为详情段起点而泄漏进正文
        chunk = """### 任务 7: 字段值含关键字 (预估总耗时: 1小时)
- 工作分类: 工作详情整理"""
        self.assertEqual(sr.build_work_detail(chunk), "字段值含关键字")

    def test_field_value_keyword_does_not_swallow_following_items(self):
        # 误判还会让该字段行之后的普通条目被当成详情条目收集
        chunk = """### 任务 7: 字段值含关键字 (预估总耗时: 1小时)
- 所属项目: 工作详情平台
- 工作详情:
  - 条目一"""
        self.assertEqual(sr.build_work_detail(chunk), "字段值含关键字\n- 条目一")

    def test_inline_detail_text_coexists_with_sub_items(self):
        # 内联文本在前、子条目在后时，两者都应保留且顺序不变
        chunk = """### 任务 6: 混合写法 (预估总耗时: 1小时)
- 工作详情: 总述一句
  - 子条目一
  - 子条目二"""
        self.assertEqual(
            sr.build_work_detail(chunk),
            "混合写法\n- 总述一句\n- 子条目一\n- 子条目二",
        )


class TestParseTasksFromMarkdown(unittest.TestCase):
    """解析新增的工作分类与所属项目字段，同时保持既有字段与非任务分片剔除逻辑。"""

    REPORT = """# 2026-07-29 日报

### 任务 1: 重构发送链路 (预估总耗时: 3小时)
- 工作分类: 开发
- 所属项目: 测试项目甲
- AI 辅助耗时: 1.5 小时
- Token 消耗量: 82000 tokens
- 工作详情:
  - 拆分职责

### 任务 2: 参加例会 (预估总耗时: 1小时)
- AI 辅助耗时: 0 小时
- Token 消耗量: 0 tokens
"""

    def test_extracts_category_and_project(self):
        tasks = sr.parse_tasks_from_markdown(self.REPORT)
        self.assertEqual(tasks[0]["work_category_raw"], "开发")
        self.assertEqual(tasks[0]["project_name_raw"], "测试项目甲")

    def test_missing_fields_become_empty_string(self):
        # 老格式任务块没有这两行，应为空串而非 KeyError，由调用方回退默认值
        tasks = sr.parse_tasks_from_markdown(self.REPORT)
        self.assertEqual(tasks[1]["work_category_raw"], "")
        self.assertEqual(tasks[1]["project_name_raw"], "")

    def test_keeps_existing_numeric_fields(self):
        tasks = sr.parse_tasks_from_markdown(self.REPORT)
        self.assertAlmostEqual(tasks[0]["hours"], 3.0)
        self.assertAlmostEqual(tasks[0]["ai_hours"], 1.5)
        self.assertEqual(tasks[0]["token_consume"], 82000)

    def test_attaches_work_detail(self):
        tasks = sr.parse_tasks_from_markdown(self.REPORT)
        self.assertEqual(tasks[0]["work_detail"], "重构发送链路\n- 拆分职责")

    def test_excludes_non_task_preamble(self):
        # 日报标题「# 2026-07-29 日报」不是任务，不得混入
        tasks = sr.parse_tasks_from_markdown(self.REPORT)
        self.assertEqual(len(tasks), 2)

    def test_ignores_keyword_inside_detail_text(self):
        # 「工作详情」正文里恰好含有"工作分类："/"所属项目："字样的自然语言描述，
        # 不应被误判为字段行——字段名必须紧跟在列表符号之后，中间不能有其他文字
        chunk = """### 任务 3: 补充说明 (预估总耗时: 1小时)
- 工作详情:
  - 说明工作分类：这其实是详情正文，不是字段
  - 备注所属项目：这也是详情正文
"""
        tasks = sr.parse_tasks_from_markdown(chunk)
        self.assertEqual(tasks[0]["work_category_raw"], "")
        self.assertEqual(tasks[0]["project_name_raw"], "")

    def test_field_line_tolerates_indent_fullwidth_colon_and_star_bullet(self):
        # 带前导缩进、全角冒号、星号列表符的字段行都应能正常解析
        chunk = """### 任务 4: 缩进与符号兼容 (预估总耗时: 1小时)
  - 工作分类: 开发
* 所属项目：某项目
"""
        tasks = sr.parse_tasks_from_markdown(chunk)
        self.assertEqual(tasks[0]["work_category_raw"], "开发")
        self.assertEqual(tasks[0]["project_name_raw"], "某项目")

    REPORT_WITH_TRAILING_SECTIONS = """# 2026-07-29 日报

### 任务 1: 甲工作 (预估总耗时: 2小时)
- 工作详情:
  - 条目一

### 任务 2: 乙工作 (预估总耗时: 1小时)
- AI 辅助耗时: 0.5 小时
- Token 消耗量: 1000 tokens
- 工作分类: 开发
- 所属项目: 测试项目甲
- 工作详情:
  - 条目二

## 沟通协作
- 与产品确认了 v2 口径
- 与测试对齐了用例

## 明日计划
- 收口联调
- AI 辅助耗时: 9 小时
- Token 消耗量: 99999 tokens
- 工作分类: 例会
- 所属项目: 尾部污染项目

---
> 本日报由 daily-summary 生成
"""

    def test_last_task_detail_stops_at_trailing_section(self):
        # 末位任务分片没有右边界时，尾部章节的条目会被静默当成该任务的工作详情
        tasks = sr.parse_tasks_from_markdown(self.REPORT_WITH_TRAILING_SECTIONS)
        self.assertEqual(len(tasks), 2)
        self.assertEqual(tasks[1]["work_detail"], "乙工作\n- 条目二")
        for keyword in ("沟通协作", "v2 口径", "用例", "收口联调", "日报由"):
            self.assertNotIn(keyword, tasks[1]["work_detail"])

    def test_trailing_section_does_not_pollute_task_fields(self):
        # 同根因的连带影响：耗时、Token、分类、项目也不得抓到尾部章节里的同名文本
        tasks = sr.parse_tasks_from_markdown(self.REPORT_WITH_TRAILING_SECTIONS)
        last = tasks[1]
        self.assertAlmostEqual(last["ai_hours"], 0.5)
        self.assertEqual(last["token_consume"], 1000)
        self.assertEqual(last["work_category_raw"], "开发")
        self.assertEqual(last["project_name_raw"], "测试项目甲")
        self.assertNotIn("沟通协作", last["content"])

    # 围栏代码块内的 # 注释行不是 Markdown 标题，不得作为截断点
    REPORT_WITH_FENCED_CODE = """### 任务 1: 清理缓存逻辑 (预估总耗时: 2小时)
- 工作详情:
  - 清理旧缓存
```bash
# 删除旧缓存目录
rm -rf ~/.cache/foo
```
  - 补充回归测试
- AI 辅助耗时: 1.5 小时
- Token 消耗量: 12345
"""

    # 同一份内容，代码块整体带缩进（列表下的常见写法）
    REPORT_WITH_INDENTED_FENCED_CODE = """### 任务 1: 清理缓存逻辑 (预估总耗时: 2小时)
- 工作详情:
  - 清理旧缓存
  ```bash
  # 删除旧缓存目录
  rm -rf ~/.cache/foo
  ```
  - 补充回归测试
- AI 辅助耗时: 1.5 小时
- Token 消耗量: 12345
"""

    def test_fenced_code_comment_does_not_truncate_task(self):
        # 截断发生在 ```bash 之前时，代码块之后的详情与字段会被静默丢弃，
        # ai_hours/token_consume 归零后照常上报错误数据
        for report in (self.REPORT_WITH_FENCED_CODE, self.REPORT_WITH_INDENTED_FENCED_CODE):
            with self.subTest(report=report):
                tasks = sr.parse_tasks_from_markdown(report)
                self.assertEqual(len(tasks), 1)
                task = tasks[0]
                self.assertAlmostEqual(task["ai_hours"], 1.5)
                self.assertEqual(task["token_consume"], 12345)
                self.assertIn("清理旧缓存", task["work_detail"])
                self.assertIn("补充回归测试", task["work_detail"])

    def test_trailing_section_after_fenced_code_still_truncates(self):
        # 围栏闭合之后，真正的尾部章节标题仍必须触发截断
        report = self.REPORT_WITH_FENCED_CODE + """
## 明日计划
- 收口联调
- AI 辅助耗时: 9 小时
"""
        task = sr.parse_tasks_from_markdown(report)[0]
        self.assertAlmostEqual(task["ai_hours"], 1.5)
        self.assertNotIn("收口联调", task["content"])

    def test_task_without_trailing_section_is_unaffected(self):
        # 末位任务后面没有其他章节时，行为与截断前保持一致
        tasks = sr.parse_tasks_from_markdown(self.REPORT)
        self.assertEqual(tasks[1]["work_detail"], "参加例会")
        self.assertAlmostEqual(tasks[1]["hours"], 1.0)


class TestBuildTaskItems(unittest.TestCase):
    """组装结构化任务：分类/项目映射、缺失兜底、sortOrder 递增。"""

    TEMPLATE = {
        "workCategory": "{{work_category}}",
        "workDetail": "{{work_detail}}",
        "workHours": "{{hours}}",
        "aiHours": "{{ai_hours}}",
        "tokenConsume": "{{token_consume}}",
        "projectId": "{{project_id}}",
        "chandaoId": "",
        "sortOrder": 1,
    }
    DEFAULTS = {
        "work_category": "dev",
        "project_id": "1000000000000000099",
        "project_name": "example-示例项目",
    }
    CATEGORIES = {"开发": "dev", "例会": "meeting"}
    PROJECTS = [{"id": "1000000000000000001", "name": "测试项目甲"}]

    def _task(self, **overrides):
        task = {
            "hours": 3.0, "ai_hours": 1.5, "token_consume": 82000,
            "work_category_raw": "开发", "project_name_raw": "测试项目甲",
            "work_detail": "重构发送链路\n- 拆分职责", "content": "",
        }
        task.update(overrides)
        return task

    def _build(self, tasks):
        return sr.build_task_items(tasks, self.TEMPLATE, self.DEFAULTS,
                                   self.CATEGORIES, self.PROJECTS)

    def test_maps_category_and_project(self):
        items, warnings = self._build([self._task()])
        self.assertEqual(items[0]["workCategory"], "dev")
        self.assertEqual(items[0]["projectId"], "1000000000000000001")
        self.assertEqual(warnings, [])

    def test_work_detail_is_injected(self):
        items, _ = self._build([self._task()])
        self.assertEqual(items[0]["workDetail"], "重构发送链路\n- 拆分职责")

    def test_sort_order_increments_and_stays_int(self):
        items, _ = self._build([self._task(), self._task(), self._task()])
        self.assertEqual([it["sortOrder"] for it in items], [1, 2, 3])
        # 必须是数字而非字符串——sortOrder 是模板里唯一的数字字段
        self.assertIsInstance(items[0]["sortOrder"], int)

    def test_missing_category_falls_back_with_warning(self):
        items, warnings = self._build([self._task(work_category_raw="")])
        self.assertEqual(items[0]["workCategory"], "dev")
        self.assertEqual(len(warnings), 1)
        self.assertIn("工作分类", warnings[0])

    def test_unknown_category_falls_back_with_warning(self):
        items, warnings = self._build([self._task(work_category_raw="不存在的分类")])
        self.assertEqual(items[0]["workCategory"], "dev")
        self.assertIn("工作分类", warnings[0])

    def test_missing_project_falls_back_with_warning(self):
        items, warnings = self._build([self._task(project_name_raw="")])
        self.assertEqual(items[0]["projectId"], "1000000000000000099")
        self.assertIn("所属项目", warnings[0])

    def test_unknown_project_falls_back_with_warning(self):
        items, warnings = self._build([self._task(project_name_raw="查无此项目")])
        self.assertEqual(items[0]["projectId"], "1000000000000000099")
        self.assertIn("所属项目", warnings[0])

    def test_legacy_task_without_new_fields_still_builds(self):
        # 老日报两个字段都缺：应产出两条警告但正常组装，不抛异常
        items, warnings = self._build([
            self._task(work_category_raw="", project_name_raw="")
        ])
        self.assertEqual(items[0]["workCategory"], "dev")
        self.assertEqual(items[0]["projectId"], "1000000000000000099")
        self.assertEqual(len(warnings), 2)

    def test_missing_defaults_warns_about_unconfigured_project(self):
        # config.json 没有 defaults 段时回退结果是空串而非默认项目，文案必须如实说明
        items, warnings = sr.build_task_items(
            [self._task(project_name_raw="查无此项目")], self.TEMPLATE, {},
            self.CATEGORIES, self.PROJECTS,
        )
        self.assertEqual(items[0]["projectId"], "")
        project_warning = next(w for w in warnings if "所属项目" in w)
        self.assertIn("未配置默认项目", project_warning)
        self.assertNotIn("已回退为默认项目", project_warning)

    def test_template_without_sort_order_is_untouched(self):
        template = {k: v for k, v in self.TEMPLATE.items() if k != "sortOrder"}
        items, _ = sr.build_task_items([self._task()], template, self.DEFAULTS,
                                       self.CATEGORIES, self.PROJECTS)
        self.assertNotIn("sortOrder", items[0])


if __name__ == "__main__":
    unittest.main()
