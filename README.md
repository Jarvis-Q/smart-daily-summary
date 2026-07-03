# 🚀 Daily Summary Skil(每日工作摘要生成器)

一个为 Claude Code 设计的自动化日报生成与发送工具。它能自动采集 Git 提交记录与 AI 对话日志，通过交互式问答补充“隐性工作”（如沟通、排坑），最终生成结构化 Markdown 日报，并支持本地编辑后发送或稍后发送。

## ✨ 核心特性
- **🔄 生成/发送解耦**：日报生成后保存在本地，你可以随时修改 AI 预估的耗时/Token，再决定是否发送。
- **🧠 隐性工作捕获**：强制交互询问“沟通/卡点/知识沉淀”，避免纯代码统计遗漏关键产出。
- **🛡️ 鲁棒性采集**：
    - 自动检测 Git 仓库，非仓库环境优雅降级。
    - 修复时间戳解析漏洞，兼容 ISO8601 及跨时区格式。
    - 增强正则解析，兼容 任务 1: / 任务一： 等多种 AI 生成变体。
- **🚀 管道自动化**：支持 cat file | script --send 模式，非交互环境下自动跳过确认，适配 CI/CD 或快捷指令。

## 📂 目录结构
```text
SMART-DAILY-SUMMARY/
├── skills/daily-summary/
    ├── SKILL.md                # 技能描述
    ├── README.md               # 本说明文档
    ├── config.json             # 服务器配置与 Payload 模板
    └── scripts/
        └── gather_data.py      # 核心采集、解析与发送脚本
├── .env
└── requirements.txt
```
##  安装指南

### 1. 克隆或复制文件
将本仓库中的 `.claude/skills/daily-summary` 目录完整复制到你的项目根目录下：

```bash
# 在你的项目根目录下执行
mkdir -p .claude/skills
# or 
# cd ./claude/skills

cp -r /path/to/skills/daily-summary .claude/skills/
```

### 2. 安装 Python 依赖
```bash
pip install -r requirements.txt
```

### 3. 配置环境变量
在项目根目录创建 .env 文件（务必确保 .env 已加入 .gitignore）：
```text
DAILY_SUMMARY_TOKEN=你的真实API_Token
```

### 4. 配置服务器与 Payload 模板
编辑 .claude/skills/daily-summary/config.json：
```json
{
  "server": {
    "url": "https://your-api.com/worklog",
    "method": "POST",
    "headers": {
      "${Authorization}": "${DAILY_SUMMARY_TOKEN}",
      "Content-Type": "application/json"
    }
  },
  "local_output_dir": "~/Documents/WorkLogs",
  "task_item_template": {
    "hours": "{{hours}}",
    "ai_hours": "{{ai_hours}}",
    "tokens": "{{token_consume}}",
    "detail": "{{content}}"
  },
  "payload_template": {
    "report_date": "{{date}}",
    "tasks": "{{tasks}}"
  }
}
```
**注意**: task_item_template & payload_template 字典的 `{key}` 请按照实际配置。
> 💡 提示：使用 ${ENV_VAR} 语法引用环境变量，脚本会自动从 .env 或系统环境中解析。

### 5. 配置日志路径（可选）
如果你的 Claude Code 对话日志不在默认路径 ~/.claude/projects/，请编辑 scripts/gather-data.py，修改 LOG_DIR 变量为你的实际路径。

## 🚀 快速开始
### 1. 触发 Skill
在 Claude Code 中输入以下任意指令：
> "生成日报"、"总结今天工作"、"daily summary"

### 2. 执行流程
1. 数据采集：脚本自动运行，输出今日 Git 提交与 Claude 对话摘要。
2. 灵魂三问：AI 会暂停并询问你今天的沟通、卡点、知识沉淀。
3. 生成日报：AI 结合数据生成结构化 Markdown，并保存到 ~/Documents/WorkLogs/。
4. 后续操作：
    - 选择 立即发送：读取本地最新文件，解析任务块，推送到服务器。
    - 选择 稍后发送：结束流程，稍后手动执行发送命令。

## 📝 日报格式规范
AI 生成的日报必须严格遵守以下格式，否则 --send 解析会失败：

```markdown
### 任务 1: [修复登录页 Bug] (预估总耗时: 2.5小时)
- AI 辅助耗时: 1.5 小时 (AI 估算)
- Token 消耗量: 4500 tokens (AI 估算)
- 工作详情:
  - 定位到 AuthProvider 状态丢失问题
  - 补充单元测试覆盖边缘场景

### 任务 2: [跨部门需求对齐] (预估总耗时: 1小时)
- AI 辅助耗时: 0 小时 (AI 估算)
- Token 消耗量: 0 tokens (AI 估算)
- 工作详情:
  - 与产品确认 v2.0 数据口径变更

```

## 🔧 手动发送（稍后发送模式）
如果你选择了“稍后发送”，或者想重新发送已修改的日报：
```bash
# 1. 在编辑器中修改完日报后保存
# 2. 运行以下命令（支持管道传入）
cat ~/Documents/WorkLogs/daily-summary-2026-07-03.md | \
python3 .claude/skills/daily-summary/scripts/gather_data.py --send
```
> ⚠️ 注意：通过管道（|）触发时，脚本会自动跳过 y/n 确认环节，直接发送。

## 🐛 常见问题排查
| 问题现象 | 可能原因 | 解决方案 |
| :--- | :--- | :--- |
| `❌ 发送失败：未能解析出任何任务块` | Markdown 格式被 AI 修改 | 检查标题是否为 `### 任务 X: [名称]`，括号内字段名是否完整 |
| `⚠️ 当前目录非 Git 仓库` | 在非项目目录触发 | 正常现象，脚本会跳过代码采集，仅基于对话生成 |
| `今日暂无 Claude Code 对话记录` | 时间戳格式不匹配 | 检查 `~/.claude/projects/` 下日志是否存在，脚本已兼容 ISO8601 |
| `❌ 发送失败：未配置 server.url` | 缺少配置文件 | 检查 `config.json` 是否存在且 `server.url` 字段非空 |

## 🛠️ 开发/调试
```bash
# 仅测试数据采集（不发送）
python3 ./skills/daily-summary/scripts/gather_data.py

# 测试发送（需传入内容）
echo "### 任务 1: [测试任务] (预估总耗时: 1小时)\n- AI 辅助耗时: 1 小时 (AI 估算)\n- Token 消耗量: 100 tokens (AI 估算)\n- 工作详情:\n  - 测试内容" | \
python3 .claude/skills/daily-summary/scripts/gather_data.py --send
```

## 贡献与反馈
欢迎提交 Issue 或 Pull Request 来完善这个 Skill！如果你在使用过程中发现了更好的 Prompt 或数据提取逻辑，请随时分享。

## 📄 开源协议
[MIT License](./LICENS)
