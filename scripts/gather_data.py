#!/usr/bin/env python3
# scripts/gather_data.py
# 职责：仅负责数据采集（Git 提交 + Claude Code 对话记录），不涉及发送逻辑。
# 发送逻辑请见同目录 send_report.py。

import os, sys, json, subprocess
from datetime import date, datetime, timedelta
from pathlib import Path

# 单次事件间隔上限（秒）：超过视为人工/异常等待（超时重试、网络重连、权限确认），截断。
# 与 cc-usage 口径保持一致。
MAX_GAP_S = 300.0

def resolve_target_date(day):
    """把 day 参数换算成目标采集日期（本地时区）。
    约定：day=1 今天，day=2 昨天，day=N 即今天回退 N-1 天。day 必须 >= 1。"""
    if day < 1:
        raise ValueError(f"day 必须 >= 1（1=今天，2=昨天…），收到 {day}")
    return date.today() - timedelta(days=day - 1)

def parse_day_arg(argv):
    """从命令行参数中解析 day=N（N 为正整数）。缺省返回 1（今天）；
    出现 day= 但值非整数时抛 ValueError，避免拼写错误被静默当成今天。"""
    for token in argv[1:]:
        if token.startswith("day="):
            return int(token[len("day="):])  # 非整数由 int() 抛 ValueError
    return 1

def compute_ai_durations(events):
    """移植自 cc-usage 的纯机器耗时口径，剔除人的阅读/输入时间。

    输入：单个会话内、已按时序排列的今日事件列表，每项为
        {"t": datetime, "typ": "user"|"assistant", "tool_use": bool, "tool_result": bool}
    返回：(思考耗时秒, 编码耗时秒)
        - 思考耗时 = Σ(assistant 响应 − 前一条 user/tool_result)，即模型响应延迟
        - 编码耗时 = Σ(tool_result − 对应 tool_use assistant)，即工具执行时长
    单次 gap 超过 MAX_GAP_S 截断；assistant 输出后到用户下一条之间的间隔（人思考）直接丢弃。
    """
    think_s = tool_s = 0.0
    for prev, cur in zip(events, events[1:]):
        gap = (cur["t"] - prev["t"]).total_seconds()
        if gap <= 0:
            continue
        if gap > MAX_GAP_S:
            gap = MAX_GAP_S
        if prev["typ"] == "assistant" and prev["tool_use"] and cur["typ"] == "user" and cur["tool_result"]:
            tool_s += gap
        elif cur["typ"] == "assistant":
            think_s += gap
    return think_s, tool_s

def discover_work_dirs(projects_root, target_date, extra_dirs=None):
    """发现某日真正工作过的所有目录。

    遍历 projects_root 下全部会话 jsonl，取 target_date（本地时区）当天
    user/assistant 记录顶层的 cwd，去重汇总；再并入 extra_dirs（展开 ~）。
    返回绝对路径字符串集合。不存在的 projects_root 视为无活动。
    """
    found = set()
    projects_root = Path(projects_root)
    if projects_root.exists():
        for jsonl_file in projects_root.rglob("*.jsonl"):
            try:
                with open(jsonl_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        if not line.strip():
                            continue
                        try:
                            record = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if record.get("type") not in ("user", "assistant"):
                            continue
                        cwd = record.get("cwd")
                        if not cwd:
                            continue
                        dt = _to_local_dt(record.get("timestamp", ""))
                        if dt is None or dt.date() != target_date:
                            continue
                        found.add(cwd)
            except OSError:
                continue
    for d in (extra_dirs or []):
        if d:
            found.add(str(Path(os.path.expanduser(d))))
    return found

def resolve_repo_roots(dirs):
    """把目录集合归一到各自的 git 仓库根并去重。

    对每个目录跑 `git -C <dir> rev-parse --show-toplevel`：
    - 成功：加入仓库根（同一仓库的多个子目录自然去重为一个）
    - 失败（非 git 目录 / 目录不存在）：静默跳过
    返回仓库根绝对路径字符串集合。
    """
    roots = set()
    for d in dirs:
        try:
            result = subprocess.run(
                ["git", "-C", d, "rev-parse", "--show-toplevel"],
                capture_output=True, text=True
            )
        except OSError:
            continue
        top = result.stdout.strip()
        if result.returncode == 0 and top:
            roots.add(top)
    return roots

def load_config():
    """加载配置文件（采集侧用于推导日报保存路径与 extra_git_dirs 兜底目录）"""
    config_path = Path(__file__).parent.parent / "config.json"
    if config_path.exists():
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def _to_local_dt(ts_str):
    """将 ISO8601（含 Z/UTC）时间戳转为本地时区的 datetime；失败返回 None。
    修复原实现用 UTC 的 .date() 与本地 today 直接比较导致的跨天错桶问题。"""
    try:
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    # astimezone() 不带参数即转换到本地系统时区
    return ts.astimezone()

def _project_name(jsonl_path):
    """从 ~/.claude/projects/<encoded>/xxx.jsonl 的父目录名反推可读项目名。
    目录编码将路径分隔符替换为 '-'，无法完美还原，取末段作为可读标签。"""
    encoded = jsonl_path.parent.name
    seg = encoded.rstrip("-").split("-")[-1]
    return seg or encoded

# ================= 数据采集模块 =================
def get_git_commits(target_date):
    """采集目标日全部工作过的 Git 仓库的提交记录。

    工作目录来源：目标日 Claude 会话记录里的 cwd（自动发现）+ config.json 的
    extra_git_dirs（兜底那些没走 Claude 的目录）。归一到仓库根后逐个采集，
    一次执行即覆盖当天跨多个工程目录的工作，不再依赖当前 shell 所在目录。"""
    print("## 代码提交（当日全部工作仓库）")
    cfg = load_config()
    projects_root = Path.home() / ".claude" / "projects"
    dirs = discover_work_dirs(projects_root, target_date, cfg.get("extra_git_dirs"))
    repos = resolve_repo_roots(dirs)
    if not repos:
        print("当日未发现任何 Git 仓库工作目录。\n")
        return
    # 用目标日的 00:00:00 ~ 23:59:59 精确框定，支持采集历史某天（不再只能 --since=midnight）
    since = f"{target_date.isoformat()} 00:00:00"
    until = f"{target_date.isoformat()} 23:59:59"
    any_commit = False
    for repo in sorted(repos):
        try:
            result = subprocess.run(
                ["git", "-C", repo, "log", f"--since={since}", f"--until={until}",
                 "--pretty=format:- %h %s (%an, %ar)", "--no-merges"],
                capture_output=True, text=True
            )
        except Exception as e:
            print(f"\n### 仓库 {repo}\n获取 Git 记录失败: {e}")
            continue
        out = result.stdout.strip()
        if out:
            any_commit = True
            print(f"\n### 仓库 {repo}")
            print(out)
    if not any_commit:
        print("当日发现的仓库均无代码提交。")
    print("")

def get_claude_logs(target_date):
    """采集 ~/.claude/projects 下目标日的 Claude Code 对话记录。
    按项目分组，从 jsonl 提取真实 token 用量与 AI 机器耗时（思考+编码，
    口径同 cc-usage，已剔除人的阅读/输入时间，不再由 AI 估算）。"""
    print("## Claude Code 对话与用量（全部项目）")
    log_dir = Path.home() / ".claude" / "projects"
    if not log_dir.exists():
        print(f"未找到 Claude Code 日志目录: {log_dir}\n")
        return

    today = target_date
    # 按项目聚合的统计容器
    projects = {}  # name -> {requests, assistant_count, in, out, cache_read, cache_creation, think_seconds, tool_seconds}

    for jsonl_file in log_dir.rglob("*.jsonl"):
        pname = _project_name(jsonl_file)
        # 本会话今日事件序列，用于按 cc-usage 口径计算思考/编码耗时
        session_events = []
        bucket = projects.setdefault(pname, {
            "requests": [], "assistant_count": 0,
            "in": 0, "out": 0, "cache_read": 0, "cache_creation": 0,
            "think_seconds": 0.0, "tool_seconds": 0.0,
        })
        try:
            with open(jsonl_file, 'r', encoding='utf-8') as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    dt = _to_local_dt(record.get("timestamp", ""))
                    if dt is None or dt.date() != today:
                        continue

                    rtype = record.get("type")
                    if rtype not in ("user", "assistant"):
                        continue

                    msg = record.get("message", {})
                    content = msg.get("content", "")
                    # 先按原始 block 结构判定 tool_use/tool_result（拍平成文本前）
                    blocks = content if isinstance(content, list) else []
                    block_types = {b.get("type") for b in blocks if isinstance(b, dict)}
                    session_events.append({
                        "t": dt, "typ": rtype,
                        "tool_use": "tool_use" in block_types,
                        "tool_result": "tool_result" in block_types,
                    })

                    if isinstance(content, list):
                        content = " ".join(b.get("text", "") for b in blocks
                                           if isinstance(b, dict) and b.get("type") == "text")

                    if rtype == "user":
                        # 用户消息最能表达“今天做了什么”，尽量少截断地保留
                        text = (content or "").strip()
                        if text and not text.startswith("<"):  # 跳过工具回传/系统包裹
                            bucket["requests"].append(text[:500])
                    else:  # assistant：累加真实 token 用量
                        bucket["assistant_count"] += 1
                        usage = msg.get("usage") or {}
                        bucket["in"] += usage.get("input_tokens", 0) or 0
                        bucket["out"] += usage.get("output_tokens", 0) or 0
                        bucket["cache_read"] += usage.get("cache_read_input_tokens", 0) or 0
                        bucket["cache_creation"] += usage.get("cache_creation_input_tokens", 0) or 0
        except Exception as e:
            print(f"读取文件 {jsonl_file} 失败: {e}")
            continue

        # 每个会话独立计算耗时再累加，避免不同会话间的时间空档被误计
        think_s, tool_s = compute_ai_durations(session_events)
        bucket["think_seconds"] += think_s
        bucket["tool_seconds"] += tool_s

    # 仅保留今日有活动的项目
    active = {n: b for n, b in projects.items()
              if b["requests"] or b["assistant_count"]}
    if not active:
        print("今日暂无 Claude Code 对话记录。\n")
        return

    grand = {"in": 0, "out": 0, "cache_read": 0, "cache_creation": 0,
             "think_seconds": 0.0, "tool_seconds": 0.0}
    for name, b in sorted(active.items(), key=lambda kv: -kv[1]["out"]):
        total_tokens = b["in"] + b["out"] + b["cache_read"] + b["cache_creation"]
        ai_seconds = b["think_seconds"] + b["tool_seconds"]
        print(f"\n### 项目 {name}")
        print(f"- 助手回复数: {b['assistant_count']} 条")
        print(f"- 真实 token: 输出 {b['out']:,} / 输入 {b['in']:,} / 缓存读 {b['cache_read']:,} / 缓存写 {b['cache_creation']:,}（合计 {total_tokens:,}）")
        print(f"- AI 机器耗时: {ai_seconds/3600:.2f} 小时（思考 {b['think_seconds']/3600:.2f} + 编码 {b['tool_seconds']/3600:.2f}，已剔除人操作时间）")
        if b["requests"]:
            print("- 今日主要请求:")
            for r in b["requests"][:25]:
                oneline = " ".join(r.split())
                print(f"  - {oneline[:200]}")
        for k in ("in", "out", "cache_read", "cache_creation", "think_seconds", "tool_seconds"):
            grand[k] += b[k]

    gt = grand["in"] + grand["out"] + grand["cache_read"] + grand["cache_creation"]
    grand_ai = grand["think_seconds"] + grand["tool_seconds"]
    print(f"\n### 今日总计（跨全部项目）")
    print(f"- 真实 token 合计: {gt:,}（输出 {grand['out']:,} / 输入 {grand['in']:,} / 缓存读 {grand['cache_read']:,} / 缓存写 {grand['cache_creation']:,}）")
    print(f"- AI 机器耗时合计: {grand_ai/3600:.2f} 小时（思考 {grand['think_seconds']/3600:.2f} + 编码 {grand['tool_seconds']/3600:.2f}）")
    print("")

def print_output_hint(target_date):
    """输出确定的日报保存路径并预建目录，避免 AI 手写落盘时路径/命名出错，
    也保证 send_report.py 的自动定位（glob daily-summary-*.md）能命中。
    文件名用目标日期，补发历史某天时不会与今天的日报重名。"""
    cfg = load_config()
    out = cfg.get("local_output_dir")
    if not out:
        return
    out = os.path.expanduser(out)
    try:
        os.makedirs(out, exist_ok=True)
    except Exception as e:
        print(f"️ 创建日报目录失败（请手动确认）：{e}")
    fname = f"daily-summary-{target_date.isoformat()}.md"
    print("## 日报保存位置（请严格使用此路径与文件名）")
    print(os.path.join(out, fname))
    print("")

# ================= 主入口 =================
def main(argv=None):
    argv = sys.argv if argv is None else argv
    day = parse_day_arg(argv)
    target_date = resolve_target_date(day)
    label = {1: "今天", 2: "昨天"}.get(day, f"{day - 1} 天前")
    print(f"=== 工作数据采集 ===\n采集日: {target_date.isoformat()}（day={day}，{label}）\n")
    get_git_commits(target_date)
    get_claude_logs(target_date)
    print_output_hint(target_date)

if __name__ == "__main__":
    try:
        main()
    except ValueError as e:
        # day 参数非法：给出友好提示而非裸 traceback
        print(f"参数错误：{e}\n用法：python gather_data.py [day=N]（day=1 今天，day=2 昨天…，缺省为 1）")
        sys.exit(2)
