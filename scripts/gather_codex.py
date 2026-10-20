#!/usr/bin/env python3
# scripts/gather_codex.py
# 职责：采集 ~/.codex/sessions 下目标日的 Codex 会话记录（rollout-*.jsonl），
# 产出与 gather_claude 同构的统计（真实 token、AI 机器耗时、会话数、交互轮次）。
# 由 gather_data.py 编排调用。
#
# rollout jsonl 记录结构（依据 Codex CLI 0.163 实测，可能随版本漂移，集中在
# 本文件适配）：
#   {"timestamp", "ordinal", "type", "payload"}
#   - type=session_meta        payload.cwd / payload.timestamp（会话起始，含工作目录）
#   - type=token_usage_record  payload.usage（单次响应的 token 增量，精确口径，优先采用）
#   - type=event_msg + payload.type=token_count
#       payload.info.last_token_usage（单次请求增量；旧版本无 token_usage_record 时回退用）
#   - type=event_msg + payload.type=item_completed
#       payload.item.type ∈ UserMessage / AgentMessage / Reasoning / CommandExecution / ...
#   - type=event_msg + payload.type=task_started（一轮交互的起点，用于计轮次）

import json
from datetime import datetime, time
from pathlib import Path

from gather_common import clamp_gap, load_config, to_local_dt

# Codex 会话日志根目录（按 年/月/日 分层的 rollout-*.jsonl）
DEFAULT_SESSIONS_ROOT = Path.home() / ".codex" / "sessions"

# item_completed 中 item.type 的归类：用户输入 / 模型输出 / 工具执行
_ITEM_USER = {"UserMessage"}
_ITEM_AGENT = {"AgentMessage", "Reasoning"}


def sessions_root(config=None):
    """Codex 会话根目录。可用 config.json 的 codex_sessions_dir 覆盖（日志迁移时用）。"""
    config = config if config is not None else load_config()
    override = config.get("codex_sessions_dir")
    if override:
        return Path(override).expanduser()
    return DEFAULT_SESSIONS_ROOT


def compute_codex_durations(events):
    """Codex 侧 AI 机器耗时口径，与 Claude 侧（cc-usage）同思路：
    - 思考耗时 = 模型输出（AgentMessage/Reasoning）到达前的等待，即模型响应延迟
    - 编码耗时 = 工具类条目（CommandExecution 等）完成前的等待，即工具执行时长
    - 用户输入前的间隔 = 人阅读/打字时间，直接丢弃
    单次 gap 由 clamp_gap 封顶 MAX_GAP_S，剔除人离开/权限确认等异常空档。

    输入：单会话内按序排列的 {"t": datetime, "kind": "user"|"agent"|"tool"} 事件。
    """
    think_s = tool_s = 0.0
    for prev, cur in zip(events, events[1:]):
        gap = clamp_gap((cur["t"] - prev["t"]).total_seconds())
        if cur["kind"] == "agent":
            think_s += gap
        elif cur["kind"] == "tool":
            tool_s += gap
    return think_s, tool_s


def _usage_delta(usage):
    """把 Codex usage 结构映射到与 Claude 一致的四个分量。
    Codex 的 input_tokens 包含缓存命中部分，这里拆出 cache_read，
    in 只计真正新算力的输入，保证两侧「合计 = 四项之和」的口径一致。"""
    cached = usage.get("cached_input_tokens", 0) or 0
    return {
        "in": max((usage.get("input_tokens", 0) or 0) - cached, 0),
        "out": usage.get("output_tokens", 0) or 0,
        "cache_read": cached,
        "cache_creation": usage.get("cache_write_input_tokens", 0) or 0,
    }


def _item_text(item):
    """从 item_completed 的 item.content 提取纯文本（UserMessage 用 text、
    AgentMessage 用 Text，两种大小写都兼容）。"""
    parts = []
    for block in item.get("content") or []:
        if isinstance(block, dict) and str(block.get("type", "")).lower() == "text":
            parts.append(block.get("text", ""))
    return " ".join(p for p in parts if p).strip()


def new_bucket():
    """与 gather_claude.new_bucket 同构，保证汇总/渲染逻辑可复用。"""
    return {
        "requests": [], "assistant_count": 0, "user_turns": 0, "sessions": 0,
        "in": 0, "out": 0, "cache_read": 0, "cache_creation": 0,
        "think_seconds": 0.0, "tool_seconds": 0.0,
    }


def _parse_session_file(path, target_date):
    """解析单个 rollout 文件，返回 (项目标签, cwd, bucket|None)。
    bucket 为 None 表示该会话在目标日无活动。"""
    cwd = ""
    events = []            # 当日 item_completed 事件流（算耗时）
    bucket = new_bucket()
    has_usage_record = False   # 当日内是否出现过 token_usage_record
    pending_last_usage = []    # 回退口径：token_count 的 last_token_usage 先暂存
    active = False

    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue

            rtype = record.get("type")
            if rtype == "session_meta":
                # session_meta 只有一条，cwd 不受日期过滤影响
                cwd = (record.get("payload") or {}).get("cwd") or ""
                continue

            dt = to_local_dt(record.get("timestamp", ""))
            if dt is None or dt.date() != target_date:
                continue

            payload = record.get("payload") or {}
            if rtype == "token_usage_record":
                has_usage_record = True
                active = True
                delta = _usage_delta(payload.get("usage") or {})
                for k, v in delta.items():
                    bucket[k] += v
            elif rtype == "event_msg":
                ptype = payload.get("type")
                if ptype == "token_count":
                    # 旧版本回退口径：是否采用取决于全文件有无 token_usage_record，
                    # 先暂存，遍历结束后统一决定，避免两种来源双计
                    info = payload.get("info") or {}
                    pending_last_usage.append(info.get("last_token_usage") or {})
                    active = True
                elif ptype == "task_started":
                    bucket["user_turns"] += 1
                    active = True
                elif ptype == "item_completed":
                    item = payload.get("item") or {}
                    itype = item.get("type") or ""
                    active = True
                    if itype in _ITEM_USER:
                        kind = "user"
                    elif itype in _ITEM_AGENT:
                        kind = "agent"
                    else:
                        kind = "tool"
                    events.append({"t": dt, "kind": kind})
                    if kind == "user":
                        # 轮次以 task_started 为准，UserMessage 不重复计数
                        text = _item_text(item)
                        if text and not text.startswith("<"):  # 跳过系统包裹注入
                            bucket["requests"].append(text[:500])
                    elif kind == "agent" and itype == "AgentMessage":
                        bucket["assistant_count"] += 1

    if not active:
        return None, cwd, None

    # token 口径二选一：新版 token_usage_record 优先；没有则回退 last_token_usage 累加
    if not has_usage_record:
        for usage in pending_last_usage:
            delta = _usage_delta(usage)
            for k, v in delta.items():
                bucket[k] += v

    think_s, tool_s = compute_codex_durations(events)
    bucket["think_seconds"] += think_s
    bucket["tool_seconds"] += tool_s
    bucket["sessions"] = 1
    label = Path(cwd).name if cwd else "未知目录"
    return label, cwd, bucket


def collect(target_date, root=None, config=None):
    """采集目标日的 Codex 会话统计，返回 {"projects": {标签: bucket}, "cwds": {...}}。
    root 可注入（测试用），缺省取 sessions_root(config)。"""
    root = Path(root) if root else sessions_root(config)
    projects, cwds = {}, set()
    if not root.exists():
        return {"projects": projects, "cwds": cwds}

    # 快速路径：mtime 早于目标日 00:00 的文件不可能有当日活动，整文件跳过
    day_start = datetime.combine(target_date, time.min).timestamp()
    for jsonl_file in root.rglob("*.jsonl"):
        try:
            if jsonl_file.stat().st_mtime < day_start:
                continue
        except OSError:
            continue
        try:
            label, cwd, bucket = _parse_session_file(jsonl_file, target_date)
        except Exception:
            # 单个坏文件不拖垮整体采集
            continue
        if bucket is None:
            continue
        if cwd:
            cwds.add(cwd)
        merged = projects.setdefault(label, new_bucket())
        merged["sessions"] += 1
        for k in ("user_turns", "assistant_count",
                  "in", "out", "cache_read", "cache_creation",
                  "think_seconds", "tool_seconds"):
            merged[k] += bucket[k]
        merged["requests"].extend(bucket["requests"])

    return {"projects": projects, "cwds": cwds}
