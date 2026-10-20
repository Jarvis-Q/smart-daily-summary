#!/usr/bin/env python3
# scripts/gather_common.py
# 职责：采集脚本的共享工具函数。供 gather_claude.py / gather_codex.py / gather_data.py 使用。
# 拆分背景：采集逻辑按数据来源（Claude Code / Codex）拆为独立模块，共享口径集中在此，
# 避免两份实现各自漂移（如时区换算、耗时截断阈值）。

import json, os, subprocess
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


def to_local_dt(ts_str):
    """将 ISO8601（含 Z/UTC）时间戳转为本地时区的 datetime；失败返回 None。
    修复原实现用 UTC 的 .date() 与本地 today 直接比较导致的跨天错桶问题。"""
    try:
        ts = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    # astimezone() 不带参数即转换到本地系统时区
    return ts.astimezone()


def load_config():
    """加载插件根目录的配置（采集侧用于推导日报保存路径、extra_git_dirs 兜底目录
    与 Codex 会话目录覆盖项）：优先个人 config.json（已 gitignore），
    缺失时回退仓库自带的 config.sample.json，两者都缺失时返回空字典。"""
    base = Path(__file__).resolve().parent.parent
    for name in ("config.json", "config.sample.json"):
        config_path = base / name
        if config_path.exists():
            with open(config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
    return {}


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


def clamp_gap(seconds):
    """单次事件间隔按 MAX_GAP_S 截断；负值（时钟回拨等）归零。"""
    if seconds <= 0:
        return 0.0
    return min(seconds, MAX_GAP_S)
