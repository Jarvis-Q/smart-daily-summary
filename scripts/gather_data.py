#!/usr/bin/env python3
# .claude/skills/daily-summary/scripts/gather_data.py
# 职责：仅负责数据采集（Git 提交 + Claude Code 对话记录），不涉及发送逻辑。
# 发送逻辑请见同目录 send_report.py。

import os, json, subprocess
from datetime import date, datetime
from pathlib import Path

def load_config():
    """加载配置文件（采集侧仅用于推导日报保存路径）"""
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
def get_git_commits():
    """采集当前 Git 仓库今日的提交记录。
    注意：git 仅覆盖当前工作目录所在仓库，Claude 对话则覆盖全部项目（见下）。"""
    print("## 代码提交（仅当前 Git 仓库）")
    if not (Path.cwd() / ".git").exists():
        print("️ 当前目录非 Git 仓库，跳过代码采集。\n")
        return
    try:
        result = subprocess.run(
            ["git", "log", "--since=midnight", "--pretty=format:- %h %s (%an, %ar)", "--no-merges"],
            capture_output=True, text=True
        )
        print(result.stdout.strip() if result.stdout.strip() else "今日暂无代码提交。")
    except Exception as e:
        print(f"获取 Git 记录失败: {e}")
    print("")

def get_claude_logs():
    """采集 ~/.claude/projects 下今日的 Claude Code 对话记录。
    按项目分组，并从 jsonl 中提取真实 token 用量与活跃时长（不再由 AI 估算）。"""
    print("## Claude Code 对话与用量（全部项目）")
    log_dir = Path.home() / ".claude" / "projects"
    if not log_dir.exists():
        print(f"未找到 Claude Code 日志目录: {log_dir}\n")
        return

    today = date.today()
    # 按项目聚合的统计容器
    projects = {}  # name -> {requests:[], assistant_count, in, out, cache_read, cache_creation, active_seconds}

    for jsonl_file in log_dir.rglob("*.jsonl"):
        pname = _project_name(jsonl_file)
        # 本文件（会话）今日消息的时间范围，用于估算活跃时长
        session_first = session_last = None
        touched = False
        bucket = projects.setdefault(pname, {
            "requests": [], "assistant_count": 0,
            "in": 0, "out": 0, "cache_read": 0, "cache_creation": 0,
            "active_seconds": 0,
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

                    touched = True
                    if session_first is None:
                        session_first = dt
                    session_last = dt

                    msg = record.get("message", {})
                    content = msg.get("content", "")
                    if isinstance(content, list):
                        content = " ".join(c.get("text", "") for c in content if c.get("type") == "text")

                    if rtype == "user":
                        # 用户消息最能表达"今天做了什么"，尽量少截断地保留
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

        if touched and session_first and session_last:
            bucket["active_seconds"] += (session_last - session_first).total_seconds()

    # 仅保留今日有活动的项目
    active = {n: b for n, b in projects.items()
              if b["requests"] or b["assistant_count"]}
    if not active:
        print("今日暂无 Claude Code 对话记录。\n")
        return

    grand = {"in": 0, "out": 0, "cache_read": 0, "cache_creation": 0, "active_seconds": 0}
    for name, b in sorted(active.items(), key=lambda kv: -kv[1]["out"]):
        total_tokens = b["in"] + b["out"] + b["cache_read"] + b["cache_creation"]
        hours = b["active_seconds"] / 3600
        print(f"\n### 项目 {name}")
        print(f"- 助手回复数: {b['assistant_count']} 条")
        print(f"- 真实 token: 输出 {b['out']:,} / 输入 {b['in']:,} / 缓存读 {b['cache_read']:,} / 缓存写 {b['cache_creation']:,}（合计 {total_tokens:,}）")
        print(f"- AI 活跃时长(近似): {hours:.2f} 小时（各会话时间跨度之和）")
        if b["requests"]:
            print("- 今日主要请求:")
            for r in b["requests"][:25]:
                oneline = " ".join(r.split())
                print(f"  - {oneline[:200]}")
        for k in ("in", "out", "cache_read", "cache_creation", "active_seconds"):
            grand[k] += b[k]

    gt = grand["in"] + grand["out"] + grand["cache_read"] + grand["cache_creation"]
    print(f"\n### 今日总计（跨全部项目）")
    print(f"- 真实 token 合计: {gt:,}（输出 {grand['out']:,} / 输入 {grand['in']:,} / 缓存读 {grand['cache_read']:,} / 缓存写 {grand['cache_creation']:,}）")
    print(f"- AI 活跃时长合计(近似): {grand['active_seconds']/3600:.2f} 小时")
    print("")

def print_output_hint():
    """输出确定的日报保存路径并预建目录，避免 AI 手写落盘时路径/命名出错，
    也保证 send_report.py 的自动定位（glob daily-summary-*.md）能命中。"""
    cfg = load_config()
    out = cfg.get("local_output_dir")
    if not out:
        return
    out = os.path.expanduser(out)
    try:
        os.makedirs(out, exist_ok=True)
    except Exception as e:
        print(f"️ 创建日报目录失败（请手动确认）：{e}")
    fname = f"daily-summary-{date.today().isoformat()}.md"
    print("## 日报保存位置（请严格使用此路径与文件名）")
    print(os.path.join(out, fname))
    print("")

# ================= 主入口 =================
def main():
    print(f"=== 今日工作数据 ===\n日期: {date.today().isoformat()}\n")
    get_git_commits()
    get_claude_logs()
    print_output_hint()

if __name__ == "__main__":
    main()
