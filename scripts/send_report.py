#!/usr/bin/env python3
# .claude/skills/daily-summary/scripts/send_report.py
# 职责：仅负责日报的解析、Payload 组装与 HTTP 发送，不涉及数据采集。
# 数据采集请见同目录 gather_data.py。
#
# 用法：
#   python3 send_report.py <文件路径>   # 发送指定日报文件
#   python3 send_report.py              # 无参 → 自动定位 local_output_dir 下最新日报
#   cat <文件> | python3 send_report.py # 管道传入（非交互，自动跳过确认）

import os, sys, json, copy, requests, time, re, glob
from datetime import date, datetime
from pathlib import Path

# 同目录的项目列表模块：负责缓存读取与项目名转 id
sys.path.insert(0, str(Path(__file__).resolve().parent))
import projects

# 自动加载 skill 根目录的 .env 文件
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except ImportError:
    pass

def load_config():
    """加载配置文件"""
    config_path = Path(__file__).parent.parent / "config.json"
    if config_path.exists():
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def load_categories():
    """加载工作分类字典（中文名 → 服务端 code）。文件缺失时返回空字典，
    调用方会因此回退到默认分类并告警，不影响发送。"""
    path = Path(__file__).parent.parent / "data" / "categories.json"
    if path.exists():
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def resolve_category(raw, categories, default_code):
    """把日报里写的分类值转成服务端 code，返回 (code, 是否命中)。

    从严匹配：strip 后先按中文名精确比对，未命中再按 code 精确比对（不区分大小写）。
    不做前缀/子串/近似匹配——「开发工作」不会命中「开发」，直接走兜底。
    这样可避免静默上报一个用户没有选过的分类。"""
    value = (raw or "").strip()
    if not value:
        return default_code, False
    if value in categories:
        return categories[value], True
    lowered = value.lower()
    for code in categories.values():
        if code.lower() == lowered:
            return code, True
    return default_code, False

def resolve_env_vars(obj):
    """递归解析配置中的环境变量占位符 ${VAR}"""
    if isinstance(obj, dict): return {k: resolve_env_vars(v) for k, v in obj.items()}
    elif isinstance(obj, list): return [resolve_env_vars(item) for item in obj]
    elif isinstance(obj, str) and obj.startswith("${") and obj.endswith("}"):
        return os.environ.get(obj[2:-1], "")
    return obj

# ================= 日报读取模块 =================
def find_latest_report(config):
    """在 local_output_dir 下定位最新的 daily-summary-*.md 文件"""
    output_dir = config.get("local_output_dir")
    if not output_dir:
        return None
    output_dir = os.path.expanduser(output_dir)
    candidates = glob.glob(os.path.join(output_dir, "daily-summary-*.md"))
    if not candidates:
        return None
    # 按文件名倒序（daily-summary-YYYY-MM-DD.md 的字典序即时间序）取最新
    return sorted(candidates)[-1]

def read_report_content(config):
    """按优先级读取日报内容：命令行文件参数 > stdin 管道 > 自动定位最新文件"""
    # 1. 命令行显式指定文件路径
    if len(sys.argv) > 1:
        file_path = os.path.expanduser(sys.argv[1])
        if not os.path.exists(file_path):
            return None, f" 错误：指定的日报文件不存在：{file_path}"
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read(), file_path

    # 2. 管道传入（非交互）
    if not sys.stdin.isatty():
        content = sys.stdin.read()
        if content.strip():
            return content, "<stdin>"

    # 3. 自动定位最新日报
    latest = find_latest_report(config)
    if not latest:
        return None, " 错误：未指定文件且 local_output_dir 下无 daily-summary-*.md 可发送。"
    with open(latest, 'r', encoding='utf-8') as f:
        return f.read(), latest

# ================= 安全脱敏模块 =================
# 保守的凭证类脱敏：仅命中明显的密钥/令牌形态，避免误伤正常中文叙述。
_SECRET_PATTERNS = [
    # 凭证标签后一律脱敏到行尾，避免"只脱标签、值泄漏"（如 Authorization: Bearer <jwt>）
    re.compile(r'(?im)\b(bearer|authorization|token|api[_-]?key|secret|password|passwd)\b\s*[:=]\s*.+$'),
    re.compile(r'(?i)\bbearer\s+[A-Za-z0-9._\-]+'),              # 裸 Bearer <token>
    re.compile(r'\bey[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}\b'),  # JWT
    re.compile(r'\b(sk|pk|rk)-[A-Za-z0-9_\-]{16,}\b'),            # OpenAI/Stripe 等 sk-/pk-
    re.compile(r'\bgh[pousr]_[A-Za-z0-9]{16,}\b'),                # GitHub token
    re.compile(r'\bAKIA[0-9A-Z]{16}\b'),                          # AWS Access Key ID
    re.compile(r'\b[0-9a-fA-F]{32,}\b'),                          # 长十六进制（哈希/密钥）
]

def redact_secrets(text):
    """在发送前对疑似凭证做脱敏，返回 (脱敏后文本, 命中次数)。"""
    count = 0
    for pat in _SECRET_PATTERNS:
        text, n = pat.subn("[REDACTED]", text)
        count += n
    return text, count

def parse_report_date(source):
    """从来源路径的文件名 daily-summary-YYYY-MM-DD.md 解析日报日期；
    解析失败回退为今天。修复补发历史日报时 workDate 被写成今天的问题。"""
    if source:
        m = re.search(r'daily-summary-(\d{4}-\d{2}-\d{2})', os.path.basename(source))
        if m:
            return m.group(1)
    return date.today().isoformat()

# ================= 解析与模板替换模块 =================
# 任务块中的结构化字段行——这些值都有各自的 JSON 字段承载，不应混进 workDetail
_FIELD_LINE = re.compile(
    r'\s*[-*]\s*(AI\s*辅助耗时|Token\s*消耗量|工作分类|所属项目|工作详情)\s*[:：]'
)
_TASK_TITLE = re.compile(r'###\s*任务\s*[\d一二三四五六七八九十]+\s*[:：]\s*(.*)')
# 任意级别的 Markdown 标题行，以及其中属于「### 任务 N:」形态的任务标题行
_HEADING_LINE = re.compile(r'^#{1,6}\s')
_TASK_HEADING_LINE = re.compile(r'^#{1,6}\s*任务\s*[\d一二三四五六七八九十]+\s*[:：]')
# 纯 Markdown 分隔线：整行仅由 -、_、* 中的某一种重复三次及以上组成（可含空格），如 ---、***、___、- - -
_SEPARATOR_LINE = re.compile(r'^(-{3,}|_{3,}|\*{3,})$')

def _is_separator_line(stripped):
    """判断某行是否为纯分隔线而非详情条目——先去掉行内所有空格再比对。"""
    compact = re.sub(r'\s+', '', stripped)
    return bool(_SEPARATOR_LINE.match(compact))

def build_work_detail(chunk):
    """构造上报用的 workDetail：任务标题（去掉预估总耗时）+ 工作详情各条目。

    AI 辅助耗时、Token 消耗量、工作分类、所属项目均有独立 JSON 字段，此处一律剔除，
    避免重复上报。无「工作详情」段时只返回标题。"""
    lines = chunk.split('\n')
    title = ""
    m = _TASK_TITLE.match(lines[0].strip())
    if m:
        # 去掉标题尾部的「(预估总耗时: X小时)」，中英文括号与冒号都兼容
        title = re.sub(r'\s*[（(]\s*预估总耗时.*$', '', m.group(1)).strip()

    details, in_detail = [], False
    for line in lines[1:]:
        field = _FIELD_LINE.match(line)
        if field:
            # 字段行本身不进正文；「工作详情」是后续条目的起始标记
            in_detail = field.group(1) == '工作详情'
            if in_detail:
                # 兼容内联写法「- 工作详情: 一句话」：冒号之后的残余文本也是详情内容，
                # 若丢弃会导致这句话凭空消失且无任何提示
                inline = line[field.end():].strip()
                if inline and not _is_separator_line(inline):
                    bullet = re.match(r'[-*]\s*(.+)$', inline)
                    details.append(f"- {bullet.group(1).strip() if bullet else inline}")
            continue
        if not in_detail:
            continue
        stripped = line.strip()
        if not stripped:
            continue
        if _is_separator_line(stripped):
            # 分隔线（如 ---）不是详情条目，跳过，避免被误判为「- --」注入正文
            continue
        item = re.match(r'[-*]\s*(.+)$', stripped)
        if item:
            details.append(f"- {item.group(1).strip()}")

    return "\n".join([title] + details) if details else title

def truncate_task_chunk(chunk):
    """给任务分片补上右边界，返回截断后的分片。

    前瞻切分（lookahead split）只有左边界没有右边界，最后一个任务的分片会一直延伸到
    文件结尾，把「## 沟通协作」「## 明日计划」等尾部章节的条目一并吞进来，从而静默地
    把无关内容当成该任务的工作详情/耗时/分类上报。此处从首个「非任务标题」的
    Markdown 标题行处截断，只保留其之前的内容；任务自身的标题行（首行）不作为截断点。

    围栏代码块（``` 或 ~~~ 包裹）内的行一律不作为截断点：日报里贴 shell 命令很常见，
    代码块中以 # 开头的注释行不是 Markdown 标题，若据此截断会静默丢失后续内容，
    连带把 AI 辅助耗时、Token 消耗量归零后上报错误数据。"""
    lines = chunk.split('\n')
    in_fence = False  # 是否处于围栏代码块内
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        # 围栏行（可带语言标注如 ```bash，闭合可为纯 ```，也允许前导缩进）翻转围栏状态
        if stripped.startswith('```') or stripped.startswith('~~~'):
            in_fence = not in_fence
            continue
        if index == 0:
            continue
        if in_fence:
            continue
        if _HEADING_LINE.match(line) and not _TASK_HEADING_LINE.match(line):
            return '\n'.join(lines[:index]).rstrip()
    return chunk

def parse_tasks_from_markdown(markdown_content):
    """增强版正则：兼容中英文冒号、阿拉伯数字与中文数字"""
    pattern = r'(?=### 任务\s*[\d一二三四五六七八九十]+\s*[:：])'
    chunks = re.split(pattern, markdown_content)

    tasks = []
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk: continue
        # 前瞻 split 的首个分片是"任务 1 之前的内容"（日报标题/项目行/分隔线等）。
        # 它匹配不到"预估总耗时"，会以 workHours=0 混入任务列表，触发服务端
        # "工作耗时最少0.5小时"校验而整单发送失败。此处按标题特征剔除非任务分片。
        if not re.match(r'###\s*任务\s*[\d一二三四五六七八九十]+\s*[:：]', chunk): continue

        # 解析任何字段之前先收口右边界，保证详情、耗时、Token、分类、项目都基于同一块内容
        chunk = truncate_task_chunk(chunk)

        first_line = chunk.split('\n')[0]
        hours_match = re.search(r'预估总耗时\s*[:：]\s*([\d.]+)', first_line)
        ai_hours_match = re.search(r'AI 辅助耗时\s*[:：]\s*([\d.]+)', chunk)
        token_match = re.search(r'Token 消耗量\s*[:：]\s*(\d+)', chunk)
        # 新增字段：缺失时留空串，由组装阶段回退到 config 的 defaults 并告警
        # 必须锚定行首的字段行（列表符号后紧跟字段名），否则会误抓「工作详情」
        # 正文里含有同名关键字+冒号的自然语言描述（如"- 说明工作分类：xxx"）
        category_match = re.search(r'^\s*[-*]\s*工作分类\s*[:：]\s*(.+)$', chunk, re.MULTILINE)
        project_match = re.search(r'^\s*[-*]\s*所属项目\s*[:：]\s*(.+)$', chunk, re.MULTILINE)

        tasks.append({
            "hours": float(hours_match.group(1)) if hours_match else 0.0,
            "ai_hours": float(ai_hours_match.group(1)) if ai_hours_match else 0.0,
            "token_consume": int(token_match.group(1)) if token_match else 0,
            "work_category_raw": category_match.group(1).strip() if category_match else "",
            "project_name_raw": project_match.group(1).strip() if project_match else "",
            "work_detail": build_work_detail(chunk),
            "content": chunk
        })
    return tasks

def replace_placeholders(obj, replacements):
    """递归替换占位符 {{key}}。
    当整个字符串恰为单个占位符且其值为 list/dict 时，做结构化注入（保留类型，
    生成真正的 JSON 数组/对象），修复原实现把任务列表 str() 成 Python repr 字符串的 bug；
    标量值仍按模板保持字符串形态。"""
    if isinstance(obj, dict): return {k: replace_placeholders(v, replacements) for k, v in obj.items()}
    elif isinstance(obj, list): return [replace_placeholders(item, replacements) for item in obj]
    elif isinstance(obj, str):
        m = re.fullmatch(r'\{\{(\w+)\}\}', obj.strip())
        if m and m.group(1) in replacements:
            value = replacements[m.group(1)]
            # list/dict → 直接返回对象（结构化）；标量 → 转字符串（贴合带引号的模板）
            return value if isinstance(value, (list, dict)) else str(value)
        for key, value in replacements.items():
            obj = obj.replace(f"{{{{{key}}}}}", str(value))
        return obj
    return obj

def build_task_items(tasks, task_item_template, defaults, categories, projects_items):
    """把解析出的任务列表组装为服务端要的结构化数组，返回 (任务列表, 警告文案列表)。

    分类与项目名在此转换为 code 与 id；未填或查不到时回退 defaults 并生成警告，
    保证老格式日报仍能正常发送。sortOrder 按任务顺序递增。"""
    default_category = defaults.get("work_category", "dev")
    default_project = defaults.get("project_id", "")
    items, warnings = [], []

    for index, task in enumerate(tasks, start=1):
        category_raw = task.get("work_category_raw", "")
        code, matched = resolve_category(category_raw, categories, default_category)
        if not matched:
            reason = "未填写" if not category_raw.strip() else f"「{category_raw}」不在分类字典中"
            warnings.append(f"任务 {index} 的工作分类{reason}，已回退为默认值 {default_category}")

        project_raw = task.get("project_name_raw", "")
        project_id = projects.resolve_project_id(project_raw, projects_items)
        if not project_id:
            reason = "未填写" if not project_raw.strip() else f"「{project_raw}」不在项目缓存中"
            if default_project:
                warnings.append(f"任务 {index} 的所属项目{reason}，已回退为默认项目")
            else:
                # config.json 缺 defaults.project_id 时回退结果其实是空串，服务端大概率打回，
                # 文案必须说清真实情况，不能谎称「已回退为默认项目」
                warnings.append(
                    f"任务 {index} 的所属项目{reason}，且未配置默认项目"
                    "（config.json 的 defaults.project_id 缺失），项目 id 将为空"
                )
            project_id = default_project

        item = replace_placeholders(copy.deepcopy(task_item_template), {
            **task, "work_category": code, "project_id": project_id,
        })
        # sortOrder 不走占位符：replace_placeholders 对标量一律 str()，
        # 会把它从数字变成字符串。此处直接赋值以保持 int 类型。
        if "sortOrder" in item:
            item["sortOrder"] = index
        items.append(item)

    return items, warnings

# ================= 发送模块 =================
def send_to_server(markdown_content, report_date=None):
    """将结构化任务列表打包到 Payload 中，一次性发送到服务器。
    report_date 为日报日期（YYYY-MM-DD），缺省用今天。"""
    config = resolve_env_vars(load_config())
    server_cfg = config.get("server", {})
    # url = server_cfg.get("url")
    server_cfg = replace_placeholders(copy.deepcopy(server_cfg), {
        "url": os.getenv("SERVER_URL")
    })
    server_cfg["headers"] = replace_placeholders(copy.deepcopy(server_cfg.get("headers", {})), {
        "X-Api-Key": os.getenv("DAILY_SUMMARY_TOKEN"),
    })

    if not server_cfg.get("url"):
        return " 发送失败：未在 config.json 中配置 server.url"

    # 0. 发送前脱敏：仅把 AI 撰写的摘要发往服务器，且对疑似凭证做保守脱敏
    markdown_content, redacted = redact_secrets(markdown_content)
    if redacted:
        print(f"️ 已对 {redacted} 处疑似凭证/密钥做脱敏后再发送。")

    # 1. 解析任务列表
    tasks = parse_tasks_from_markdown(markdown_content)
    if not tasks:
        return " 发送失败：未能从日报中解析出任何任务块，请检查 Markdown 格式是否符合规范。"

    # 2. 组装结构化 List<Object>：分类转 code、项目名转 id、sortOrder 按顺序递增
    task_item_template = config.get("task_item_template", {"content": "{{content}}"})
    defaults = config.get("defaults", {})
    projects_items = projects.load_cache(projects.cache_file(config))
    structured_tasks, warnings = build_task_items(
        tasks, task_item_template, defaults, load_categories(), projects_items
    )
    for warning in warnings:
        print(f"⚠️ {warning}")

    # 3. 组装主 Payload
    main_template = config.get("payload_template", {"tasks": "{{tasks}}"})
    final_payload = replace_placeholders(copy.deepcopy(main_template), {
        "tasks": structured_tasks,
        "date": report_date or date.today().isoformat()
    })
    # 4. 发送前确认（预览）
    print(f" 共解析到 {len(tasks)} 个独立任务，已打包至 Payload。")
    print("️ 即将发送以下 Payload 到服务器:")
    print(json.dumps(final_payload, indent=2, ensure_ascii=False))

    # 管道传入（非交互模式）默认直接发送，避免阻塞
    if not sys.stdin.isatty():
        confirm = 'y'
    else:
        confirm = input("\n确认发送吗？(y/n): ").strip().lower()

    if confirm != 'y':
        return " 发送已取消。"

    # 5. 执行发送（带自动重试）
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            print(f" 正在发送 (第 {attempt} 次尝试)...")
            response = requests.request(
                method=server_cfg.get("method", "POST"), url=server_cfg.get("url"),
                headers=server_cfg.get("headers", {}), json=final_payload, timeout=10
            )
            if response.ok and response.json().get("code") == 200:
                return f" 发送成功！状态码: {response.status_code}"
            else:
                print(f"️ 服务器返回异常: {json.dumps(response.json(), indent=2, ensure_ascii=False)}")
        except Exception as e:
            print(f"️ 网络异常: {str(e)}")

        if attempt < max_retries:
            print(" 3秒后自动重试...")
            time.sleep(3)

    return " 发送失败：已达到最大重试次数，请检查网络或服务器配置。"

# ================= 主入口 =================
def main():
    config = resolve_env_vars(load_config())
    content, source = read_report_content(config)
    if content is None:
        # source 此时为错误信息
        print(source)
        sys.exit(1)
    report_date = parse_report_date(source)
    print(f" 待发送日报来源: {source}（日报日期: {report_date}）\n")
    print(send_to_server(content, report_date))

if __name__ == "__main__":
    main()
