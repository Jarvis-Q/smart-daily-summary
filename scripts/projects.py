#!/usr/bin/env python3
# scripts/projects.py
# 职责：项目列表的拉取、本地缓存与检索。供 generate 阶段选择项目、send 阶段转换 id 用。
#
# 安全约束：项目列表属公司内部数据，仅用于「生成日报时供用户选择」与「发送日报时转 id」。
#   - 缓存文件 0600、目录 0700
#   - refresh 只打印条数，search 输出上限 5 条，绝不输出全量
#   - 异常信息不回显完整 URL、X-Api-Key 与响应体原文
#
# 用法：
#   python3 projects.py refresh               # 拉取全量并写入缓存
#   python3 projects.py search <关键词>        # 在缓存中检索，输出 JSON（至多 5 条，高频优先）
#   python3 projects.py remote-search <关键词> # 联网刷新缓存后检索（找新项目用）
#   python3 projects.py frequent              # 按历史发送统计的高频项目（至多 8 条，不联网）
#   python3 projects.py resolve <项目名>       # 项目名转 id

import json, os, sys, requests
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

# 自动加载插件根目录的 .env
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except ImportError:
    pass

# 检索结果上限：内部数据不做全量输出
SEARCH_LIMIT = 5
# 高频项目清单上限：供生成阶段快速挑选，不联网
FREQUENT_LIMIT = 8
DEFAULT_CACHE = "~/.cache/daily-summary/projects.json"
DEFAULT_USAGE = "~/.cache/daily-summary/project-usage.json"

# 响应字段名候选：服务端结构未固定，归一化时逐个尝试
_ID_KEYS = ("id", "projectId", "project_id", "value")
_NAME_KEYS = ("name", "projectName", "project_name", "label", "title")
_LIST_KEYS = ("data", "result", "rows", "list", "records", "items")


def load_config():
    """加载插件根目录的配置：优先个人 config.json（已 gitignore），
    缺失时回退仓库自带的 config.sample.json（占位符值，仅保证结构可读）。
    两者都缺失时返回空字典。"""
    base = Path(__file__).resolve().parent.parent
    for name in ("config.json", "config.sample.json"):
        path = base / name
        if path.exists():
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
    return {}


def derive_projects_url(server_url):
    """由日报上报地址推导项目列表地址：把路径最后一段换成 projects。
    形如 http://<host>/open-api/report/worklog → http://<host>/open-api/report/projects。
    这样不必新增环境变量，两个接口天然同源。"""
    parts = urlsplit((server_url or "").strip())
    if not parts.scheme or not parts.netloc:
        return ""
    base = parts.path.rstrip('/').rpartition('/')[0]
    return urlunsplit((parts.scheme, parts.netloc, f"{base}/projects", "", ""))


def _pick(item, keys):
    """按候选键顺序取第一个非空值，统一转成字符串。"""
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


# 递归定位数组时的最大嵌套深度：正常的响应包装（如 data.list）不会超过两三层，
# 设置上限防止病态的自嵌套结构（如上千层 {"data": {"data": ...}}）撑爆调用栈
_MAX_LIST_DEPTH = 5


def _extract_list(payload, _depth=0):
    """从响应中定位项目数组，兼容裸数组与 data/list 等常见包装。
    超过 _MAX_LIST_DEPTH 层仍未找到数组时直接返回空列表，而不是继续递归——
    避免恶意或病态的深层嵌套输入引发 RecursionError 导致进程崩溃。"""
    if _depth > _MAX_LIST_DEPTH:
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in _LIST_KEYS:
            value = payload.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                inner = _extract_list(value, _depth + 1)
                if inner:
                    return inner
    return []


def normalize_projects(payload):
    """把服务端响应归一化为 [{"id": str, "name": str}]。
    服务端结构若有变动，只需改这一个函数。"""
    items = []
    for raw in _extract_list(payload):
        if not isinstance(raw, dict):
            continue
        pid, name = _pick(raw, _ID_KEYS), _pick(raw, _NAME_KEYS)
        if pid and name:
            items.append({"id": pid, "name": name})
    return items


def fetch_projects():
    """GET 项目接口并归一化。失败时抛 RuntimeError，信息中不含地址与凭证。"""
    url = derive_projects_url(os.getenv("SERVER_URL", ""))
    if not url:
        raise RuntimeError("未能从 SERVER_URL 推导出项目接口地址，请检查 .env 配置")
    token = os.getenv("DAILY_SUMMARY_TOKEN", "")
    try:
        response = requests.get(url, headers={"X-Api-Key": token}, timeout=10)
    except Exception:
        # 异常原文可能带完整 URL，不外泄
        raise RuntimeError("项目列表请求失败：网络异常，请检查内网连通性")
    if not response.ok:
        raise RuntimeError(f"项目列表请求失败：HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError:
        raise RuntimeError("项目列表请求失败：响应不是合法 JSON")
    return normalize_projects(payload)


def cache_file(config=None):
    """缓存路径。默认在用户目录而非插件目录——插件更新会重建目录，缓存会丢。"""
    config = config if config is not None else load_config()
    return Path(config.get("projects_cache_path", DEFAULT_CACHE)).expanduser()


def _mkdir_0700(d):
    """由外到内逐级创建目录 d 及其缺失的上级目录，每一级都用 os.mkdir(d, 0o700)。
    0o700 本身不含 group/other 权限位，umask 只能移除权限位、不能添加，所以
    不论调用方进程的 umask 是什么，创建出的目录权限都稳定是 0o700——权限在
    内核创建目录的那一刻就已确定，不存在 mkdir 与 chmod 之间的时间窗口。
    （早前版本用临时收紧 umask 的方式实现同样效果，但 umask 是进程级全局状态，
    会污染同进程内其他线程此刻创建的无关目录，故弃用，改为本方案。）
    已存在的目录直接跳过：不报错，也不修改其原有权限（不降级已存在目录）。"""
    d = Path(d)
    missing = []
    cur = d
    while not cur.exists():
        missing.append(cur)
        cur = cur.parent
    for cur in reversed(missing):
        try:
            os.mkdir(cur, 0o700)
        except FileExistsError:
            # 并发场景下可能被其他进程抢先创建，视为已达成目标
            pass


def save_cache(items, path):
    """写入缓存。
    目录：由外到内逐级用 os.mkdir(d, 0o700) 创建缺失的目录（见 _mkdir_0700），
    每一级（含中间层）在诞生瞬间就是 0700，不依赖也不触碰进程级 umask；
    已存在的目录直接跳过，不报错也不改权限。
    文件：以 O_CREAT 在 os.open 时直接指定 0600，同样避免先落盘默认权限再
    chmod 的暴露窗口。"""
    path = Path(path).expanduser()
    _mkdir_0700(path.parent)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(items, f, ensure_ascii=False)
    os.chmod(path, 0o600)
    return path


def load_cache(path):
    """读取缓存。文件缺失或内容损坏时返回空列表，由调用方决定是否重新拉取。"""
    path = Path(path).expanduser()
    if not path.exists():
        return []
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (ValueError, OSError):
        return []
    return data if isinstance(data, list) else []


def load_or_fetch(config=None):
    """优先用缓存；缓存缺失或为空时自动拉取一次并落盘。
    不做定时失效——项目列表变动时由用户手动执行 refresh。"""
    path = cache_file(config)
    items = load_cache(path)
    if items:
        return items
    items = fetch_projects()
    save_cache(items, path)
    return items


# ================= 使用频次统计（高频项目清单） =================
def usage_file(config=None):
    """频次统计文件路径。与项目缓存同目录，同样不放插件目录（插件更新会重建）。"""
    config = config if config is not None else load_config()
    return Path(config.get("projects_usage_path", DEFAULT_USAGE)).expanduser()


def load_usage(path):
    """读取频次统计：{id: {"name": str, "count": int, "last_used": "YYYY-MM-DD"}}。
    文件缺失或损坏时返回空字典——统计只是优化项，丢了不影响任何功能。"""
    path = Path(path).expanduser()
    if not path.exists():
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (ValueError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def save_usage(usage, path):
    """写入频次统计，权限与项目缓存一致（目录 0700 / 文件 0600）。"""
    path = Path(path).expanduser()
    _mkdir_0700(path.parent)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(usage, f, ensure_ascii=False)
    os.chmod(path, 0o600)
    return path


def record_usage(ids, items, path=None):
    """发送成功后累计项目使用次数。ids 为本次实际使用的项目 id 列表
    （含回退到默认项目的情形——那同样是真实使用）；同名 id 重复出现会多次累计，
    符合「一天内多次任务挂同一项目 = 更高频」的直觉。
    items 为项目缓存，用于把 id 解析成可读名称快照存进统计。
    返回更新后的统计 dict；任何 IO 异常都不应阻断发送主流程，由调用方兜底。"""
    path = path if path is not None else usage_file()
    usage = load_usage(path)
    name_by_id = {it.get("id"): it.get("name", "") for it in items}
    today = date.today().isoformat()
    for pid in ids:
        pid = str(pid or "").strip()
        if not pid:
            continue
        entry = usage.setdefault(pid, {"name": "", "count": 0, "last_used": ""})
        entry["count"] = int(entry.get("count", 0) or 0) + 1
        entry["last_used"] = today
        # 缓存里能查到就用最新名称刷新快照（服务端改名后统计仍可读）
        if name_by_id.get(pid):
            entry["name"] = name_by_id[pid]
    save_usage(usage, path)
    return usage


def frequent_projects(usage, items, limit=FREQUENT_LIMIT):
    """按使用统计生成高频项目清单：count 降序、last_used 降序，至多 limit 条。
    名称优先取缓存里的最新值（缓存可能已改名），其次统计快照；两边都查不到
    名称的条目无法展示，直接跳过。"""
    name_by_id = {it.get("id"): it.get("name", "") for it in items}
    # 两次稳定排序：先按 last_used 降序，再按 count 降序——
    # count 相同条目的相对顺序由前一次排序保留，即「同频次取最近用过的」
    ranked = sorted(usage.items(),
                    key=lambda kv: kv[1].get("last_used", "") or "", reverse=True)
    ranked.sort(key=lambda kv: kv[1].get("count", 0) or 0, reverse=True)
    out = []
    for pid, entry in ranked:
        name = name_by_id.get(pid) or entry.get("name", "")
        if not name:
            continue
        out.append({"id": pid, "name": name, "count": entry.get("count", 0)})
        if len(out) >= limit:
            break
    return out


def search_projects(keyword, items, limit=SEARCH_LIMIT, usage=None):
    """按关键词检索项目名或 id，结果上限 limit 条。空关键词也受同一上限约束，
    确保任何路径都不会输出全量列表。
    传入 usage（record_usage 的统计 dict）时，命中结果按使用频次降序排列，
    让常用项目排在前面；同频次的保持缓存原有顺序（稳定排序）。"""
    kw = (keyword or "").strip().lower()
    if not kw:
        matched = items[:limit]
    else:
        matched = [it for it in items
                   if kw in it.get("name", "").lower() or kw in it.get("id", "")][:limit]
    if usage:
        matched = sorted(
            matched,
            key=lambda it: (usage.get(it.get("id"), {}) or {}).get("count", 0) or 0,
            reverse=True,
        )
    return matched


def resolve_project_id(name, items):
    """项目名转 id，strip 后精确匹配（与分类一样从严）。未命中返回空串。"""
    target = (name or "").strip()
    if not target:
        return ""
    for it in items:
        if it.get("name", "").strip() == target:
            return it.get("id", "")
    return ""


def main():
    args = sys.argv[1:]
    command = args[0] if args else ""

    if command == "refresh":
        items = fetch_projects()
        path = save_cache(items, cache_file())
        # 只报条数与路径，不打印任何项目名或 id
        print(f"已拉取 {len(items)} 个项目，缓存至 {path}")
    elif command == "search":
        keyword = args[1] if len(args) > 1 else ""
        print(json.dumps(search_projects(keyword, load_or_fetch(),
                                         usage=load_usage(usage_file())),
                         ensure_ascii=False))
    elif command == "remote-search":
        # 缓存检索不到时兜底：强制联网拉取全量（同时刷新缓存），再按关键词过滤。
        # 服务端是否支持查询参数未知，因此拉全量后本地过滤，输出仍受 SEARCH_LIMIT 约束。
        keyword = args[1] if len(args) > 1 else ""
        try:
            items = fetch_projects()
        except RuntimeError as e:
            print(f"远端检索失败：{e}", file=sys.stderr)
            sys.exit(1)
        save_cache(items, cache_file())
        print(json.dumps(search_projects(keyword, items,
                                         usage=load_usage(usage_file())),
                         ensure_ascii=False))
    elif command == "frequent":
        # 高频清单只读本地统计与缓存，绝不联网——生成阶段的快速路径
        print(json.dumps(frequent_projects(load_usage(usage_file()),
                                           load_cache(cache_file())),
                         ensure_ascii=False))
    elif command == "resolve":
        name = args[1] if len(args) > 1 else ""
        print(resolve_project_id(name, load_or_fetch()))
    else:
        print("用法：projects.py refresh | search <关键词> | remote-search <关键词> | frequent | resolve <项目名>")
        sys.exit(1)


if __name__ == "__main__":
    main()
