#!/bin/bash
# scripts/install_codex.sh
# 职责：把本插件的 Codex 版 skill 软链安装到用户级 skill 根目录 ~/.agents/skills/。
# Codex 会自动扫描该目录（见 https://learn.chatgpt.com/docs/build-skills）。
# 软链而非复制：仓库更新后 skill 内容即时生效，无需重复安装。
#
# 用法：bash scripts/install_codex.sh
# 幂等：已存在且指向正确位置的软链会跳过；同名但指向别处/是普通目录时报错提示。

set -euo pipefail

# 插件根 = 本脚本所在目录的上一级（先解析脚本自身可能的软链）
SCRIPT_SRC="${BASH_SOURCE[0]}"
while [ -h "$SCRIPT_SRC" ]; do
  SCRIPT_SRC="$(readlink "$SCRIPT_SRC")"
done
PLUGIN_ROOT="$(cd "$(dirname "$SCRIPT_SRC")/.." && pwd)"

SKILL_ROOT="${CODEX_SKILL_ROOT:-$HOME/.agents/skills}"
SKILLS=(daily-summary-generate daily-summary-send)

mkdir -p "$SKILL_ROOT"

for name in "${SKILLS[@]}"; do
  src="$PLUGIN_ROOT/.agents/skills/$name"
  dst="$SKILL_ROOT/$name"
  if [ ! -f "$src/SKILL.md" ]; then
    echo "✗ 源 skill 不存在：$src（插件目录结构异常）" >&2
    exit 1
  fi
  if [ -L "$dst" ]; then
    if [ "$(readlink "$dst")" = "$src" ]; then
      echo "✓ $name 已安装，跳过"
      continue
    fi
    echo "✗ $dst 已存在但指向 $(readlink "$dst")，请手动处理后重试" >&2
    exit 1
  fi
  if [ -e "$dst" ]; then
    echo "✗ $dst 已存在且不是软链，请手动处理后重试" >&2
    exit 1
  fi
  ln -s "$src" "$dst"
  echo "✓ 已安装 $name → $src"
done

echo
echo "安装完成。在 Codex 中输入 /skills 或 \$daily-summary-generate 即可看到；"
echo "如未生效请重启 Codex。首次使用前记得："
echo "  1. 在插件根放置 .env（cp .env.example .env 后填真实值）"
echo "  2. python3 $PLUGIN_ROOT/scripts/projects.py refresh"
