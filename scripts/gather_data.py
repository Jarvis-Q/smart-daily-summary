#!/usr/bin/env python3
# scripts/gather_data.py
# 职责：采集编排入口。Git 提交 + Claude Code 对话 + Codex 会话三路数据在此合并输出，
# 不涉及发送逻辑（发送见同目录 send_report.py）。
#
# 子模块拆分：
#   gather_common.py  共享口径（时区换算、day 参数、git 仓库归一、gap 截断）
#   gather_claude.py  Claude Code 会话采集（~/.claude/projects）
#   gather_codex.py   Codex 会话采集（~/.codex/sessions）
#
# 用法：
#   python3 gather_data.py           # 默认今天
#   python3 gather_data.py day=2     # 补采昨天（day=N 即回退 N-1 天）

import os, subprocess, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gather_common import (  # noqa: E402
    load_config, parse_day_arg, resolve_repo_roots, resolve_target_date,
)
import gather_claude  # noqa: E402
import gather_codex  # noqa: E402

# ---- 兼容旧引用：这两个函数原先定义在本文件，外部脚本/旧测试可能仍在 import ----
from gather_claude import compute_ai_durations, discover_work_dirs  # noqa: E402,F401


def discover_codex_work_dirs(root, target_date):
    """兼容旧接口形态的薄封装：Codex 侧目录发现已并入 gather_codex.collect，
    这里保留独立入口仅供测试与调试使用。"""
    return gather_codex.collect(target_date, root=root)["cwds"]


# ================= Git 采集 =================
def merge_work_dirs(cwds, extra_dirs=None):
    """合并 AI 会话发现的工作目录与 config.json 的 extra_git_dirs 兜底目录。
    extra_dirs 中的 ~ 会展开为绝对路径；空串条目跳过。"""
    dirs = set(cwds)
    for d in (extra_dirs or []):
        if d:
            dirs.add(str(Path(os.path.expanduser(d))))
    return dirs


def get_git_commits(target_date, cwds):
    """采集目标日全部工作过的 Git 仓库的提交记录。

    工作目录来源：Claude 会话 cwd ∪ Codex 会话 cwd ∪ config.json 的
    extra_git_dirs（兜底那些没走 AI 工具的目录）。归一到仓库根后逐个采集，
    一次执行即覆盖当天跨多个工程目录的工作，不再依赖当前 shell 所在目录。"""
    print("## 代码提交（当日全部工作仓库）")
    cfg = load_config()
    repos = resolve_repo_roots(merge_work_dirs(cwds, cfg.get("extra_git_dirs")))
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


# ================= 分节渲染（两端同构，共用一套） =================
def render_section(title, empty_hint, projects):
    """打印某一端（Claude Code / Codex）的对话用量分节，返回该端小计 bucket。"""
    print(f"## {title}（全部项目）")
    if not projects:
        print(f"{empty_hint}\n")
        return gather_claude.summarize({})

    for name, b in sorted(projects.items(), key=lambda kv: -kv[1]["out"]):
        ai_seconds = b["think_seconds"] + b["tool_seconds"]
        print(f"\n### 项目 {name}")
        print(f"- 会话数: {b['sessions']} 个 / 交互轮次: {b['user_turns']} 轮 / 助手回复数: {b['assistant_count']} 条")
        print(f"- 真实 token: 输出 {b['out']:,} / 输入 {b['in']:,} / 缓存读 {b['cache_read']:,} / 缓存写 {b['cache_creation']:,}（合计 {gather_claude.token_total(b):,}）")
        print(f"- AI 机器耗时: {ai_seconds/3600:.2f} 小时（思考 {b['think_seconds']/3600:.2f} + 编码 {b['tool_seconds']/3600:.2f}，已剔除人操作时间）")
        if b["requests"]:
            print("- 今日主要请求:")
            for r in b["requests"][:25]:
                oneline = " ".join(r.split())
                print(f"  - {oneline[:200]}")

    sub = gather_claude.summarize(projects)
    st = gather_claude.token_total(sub)
    sub_ai = sub["think_seconds"] + sub["tool_seconds"]
    print(f"\n### {title}小计")
    print(f"- 会话数: {sub['sessions']} 个 / 交互轮次: {sub['user_turns']} 轮")
    print(f"- 真实 token 小计: {st:,}（输出 {sub['out']:,} / 输入 {sub['in']:,} / 缓存读 {sub['cache_read']:,} / 缓存写 {sub['cache_creation']:,}）")
    print(f"- AI 机器耗时小计: {sub_ai/3600:.2f} 小时（思考 {sub['think_seconds']/3600:.2f} + 编码 {sub['tool_seconds']/3600:.2f}）")
    print("")
    return sub


def render_grand_total(claude_sub, codex_sub):
    """跨端合计：日报的 token 与 AI 耗时分摊以这里的「真实合计」为锚。"""
    t1, t2 = gather_claude.token_total(claude_sub), gather_claude.token_total(codex_sub)
    ai1 = (claude_sub["think_seconds"] + claude_sub["tool_seconds"]) / 3600
    ai2 = (codex_sub["think_seconds"] + codex_sub["tool_seconds"]) / 3600
    print("### 今日总计（跨端合计）")
    print(f"- 会话数合计: {claude_sub['sessions'] + codex_sub['sessions']} 个"
          f"（Claude {claude_sub['sessions']} + Codex {codex_sub['sessions']}）"
          f" / 交互轮次合计: {claude_sub['user_turns'] + codex_sub['user_turns']} 轮")
    print(f"- 真实 token 合计: {t1 + t2:,}（Claude {t1:,} + Codex {t2:,}）")
    print(f"- AI 机器耗时合计: {ai1 + ai2:.2f} 小时（Claude {ai1:.2f} + Codex {ai2:.2f}）")
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

    claude_data = gather_claude.collect(target_date)
    codex_data = gather_codex.collect(target_date)

    get_git_commits(target_date, claude_data["cwds"] | codex_data["cwds"])
    claude_sub = render_section("Claude Code 对话与用量",
                                "今日暂无 Claude Code 对话记录。", claude_data["projects"])
    codex_sub = render_section("Codex 对话与用量",
                               "今日暂无 Codex 会话记录。", codex_data["projects"])
    render_grand_total(claude_sub, codex_sub)
    print_output_hint(target_date)


if __name__ == "__main__":
    try:
        main()
    except ValueError as e:
        # day 参数非法：给出友好提示而非裸 traceback
        print(f"参数错误：{e}\n用法：python gather_data.py [day=N]（day=1 今天，day=2 昨天…，缺省为 1）")
        sys.exit(2)
