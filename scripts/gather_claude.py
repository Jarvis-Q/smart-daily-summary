#!/usr/bin/env python3
# scripts/gather_claude.py
# 职责：采集 ~/.claude/projects 下目标日的 Claude Code 对话记录，
# 产出结构化统计（真实 token、AI 机器耗时、会话数、交互轮次），并负责该分节的渲染。
# 由 gather_data.py 编排调用；耗时口径同 cc-usage（思考+编码，剔除人操作时间）。

import json
from pathlib import Path

from gather_common import MAX_GAP_S, to_local_dt

# Claude Code 会话日志根目录（按编码后的项目目录组织，每个会话一个 jsonl）
DEFAULT_PROJECTS_ROOT = Path.home() / ".claude" / "projects"


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


def discover_work_dirs(projects_root, target_date):
    """发现目标日有 Claude 活动的所有工作目录。

    遍历 projects_root 下全部会话 jsonl，取 target_date（本地时区）当天
    user/assistant 记录顶层的 cwd，去重汇总。不存在的 projects_root 视为无活动。
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
                        except ValueError:
                            continue
                        if record.get("type") not in ("user", "assistant"):
                            continue
                        cwd = record.get("cwd")
                        if not cwd:
                            continue
                        dt = to_local_dt(record.get("timestamp", ""))
                        if dt is None or dt.date() != target_date:
                            continue
                        found.add(cwd)
            except OSError:
                continue
    return found


def _project_name(jsonl_path):
    """从 ~/.claude/projects/<encoded>/xxx.jsonl 的父目录名反推可读项目名。
    目录编码将路径分隔符替换为 '-'，无法完美还原，取末段作为可读标签。"""
    encoded = jsonl_path.parent.name
    seg = encoded.rstrip("-").split("-")[-1]
    return seg or encoded


def new_bucket():
    """单个项目（按会话目录名归组）的统计容器。"""
    return {
        "requests": [], "assistant_count": 0, "user_turns": 0, "sessions": 0,
        "in": 0, "out": 0, "cache_read": 0, "cache_creation": 0,
        "think_seconds": 0.0, "tool_seconds": 0.0,
    }


def collect(target_date, projects_root=None):
    """采集目标日的 Claude Code 对话统计，返回结构化 dict：
    {"projects": {项目名: bucket}, "cwds": {当日工作目录...}}。
    projects_root 可注入（测试用），缺省取 ~/.claude/projects。"""
    root = Path(projects_root) if projects_root else DEFAULT_PROJECTS_ROOT
    projects, cwds = {}, set()
    if not root.exists():
        return {"projects": projects, "cwds": cwds}

    for jsonl_file in root.rglob("*.jsonl"):
        pname = _project_name(jsonl_file)
        # 本会话今日事件序列，用于按 cc-usage 口径计算思考/编码耗时
        session_events = []
        session_active = False  # 本会话当日是否有活动（决定 sessions 计数）
        bucket = projects.setdefault(pname, new_bucket())
        try:
            with open(jsonl_file, 'r', encoding='utf-8') as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue

                    dt = to_local_dt(record.get("timestamp", ""))
                    if dt is None or dt.date() != target_date:
                        continue

                    rtype = record.get("type")
                    if rtype not in ("user", "assistant"):
                        continue
                    session_active = True
                    if record.get("cwd"):
                        cwds.add(record["cwd"])

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
                        # 真实用户提问（非工具回传）计为一个交互轮次
                        if "tool_result" not in block_types:
                            bucket["user_turns"] += 1
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
        except Exception:
            # 单个坏文件不拖垮整体采集
            continue

        if not session_active:
            # 文件存在但当日记零条：不产生任何统计，连项目分组也不应出现
            if not bucket["requests"] and not bucket["assistant_count"]:
                projects.pop(pname, None)
            continue
        bucket["sessions"] += 1

        # 每个会话独立计算耗时再累加，避免不同会话间的时间空档被误计
        think_s, tool_s = compute_ai_durations(session_events)
        bucket["think_seconds"] += think_s
        bucket["tool_seconds"] += tool_s

    return {"projects": projects, "cwds": cwds}


def summarize(projects):
    """把按项目分组的统计汇总为总计 dict（键与 bucket 一致）。"""
    total = new_bucket()
    total["requests"] = []  # 总计不汇总请求文本
    for b in projects.values():
        for k in ("assistant_count", "user_turns", "sessions",
                  "in", "out", "cache_read", "cache_creation",
                  "think_seconds", "tool_seconds"):
            total[k] += b[k]
    return total


def token_total(b):
    """单个 bucket 的 token 合计（输入+输出+缓存读+缓存写）。"""
    return b["in"] + b["out"] + b["cache_read"] + b["cache_creation"]
