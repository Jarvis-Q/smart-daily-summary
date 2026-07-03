# 🚀 Daily Summary Skill(每日工作摘要生成器)
一个为 Claude Code 设计的自动化日报生成与发送工具。它将**数据采集**、**内容生成**、**审核发送**彻底解耦，支持通过斜杠指令精准控制流程，完美适配“先生成、再调整、后发送”的真实工作习惯。

## ✨ 核心特性
- **🎯 指令级解耦**：
    - /daily-summary：专注采集与生成，不阻塞、不自动发送。
    - /daily-summary:send：一键发送最新日报，或指定历史文件发送。
- **📊 真实用量统计**：直接从 Claude Code 的 jsonl 日志汇总**真实 token 用量**（输入/输出/缓存）与**活跃时长**，按项目分组，不再靠 AI 估算。
- **🧠 隐性工作捕获**：强制交互询问“沟通/卡点/知识沉淀”，避免纯代码统计遗漏关键产出。
- **🛡️ 鲁棒性采集**：
    - 自动检测 Git 仓库，非仓库环境优雅降级。
    - 时间戳统一转本地时区后再按"今天"过滤，避免跨午夜错桶。
    - 增强正则解析，兼容 任务 1: / 任务一： 等多种 AI 生成变体。
- **🔒 出网脱敏**：发送前对疑似密钥/令牌做保守脱敏，仅发送 AI 撰写的摘要，不上传原始对话。
- **🚀 管道自动化**：支持 `cat file | send_report.py` 模式，非交互环境下自动跳过确认，适配 CI/CD 或快捷指令。

## 📂 目录结构
> 仓库根目录本身即 skill 目录，安装时整体复制为 `.claude/skills/daily-summary`。
```text
smart-daily-summary/            # 仓库根 = skill 目录
├── SKILL.md                    # 技能描述与执行分支
├── README.md                   # 本说明文档
├── config.json                 # 服务器配置与 Payload 模板
├── .env                        # 私有密钥（需自建，已 gitignore）
├── requirements.txt            # Python 依赖
└── scripts/
    ├── gather_data.py          # 仅数据采集（Git 提交 + Claude 对话/用量）
    └── send_report.py          # 仅解析日报、组装 Payload 与 HTTP 发送
```
##  安装指南

### 1. 克隆或复制文件
将本仓库整体复制为你项目（或用户目录）下的 `.claude/skills/daily-summary`：

```bash
# 在目标项目根目录下执行
mkdir -p .claude/skills
cp -r /path/to/smart-daily-summary .claude/skills/daily-summary
```

### 2. 安装 Python 依赖
```bash
pip install -r requirements.txt
```

### 3. 配置环境变量
在daily-summary目录创建 .env 文件（务必确保 .env 已加入 .gitignore）：
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
脚本默认从 `~/.claude/projects/` 读取 Claude Code 对话日志。若你的日志不在该默认路径，请编辑 `scripts/gather_data.py` 中 `get_claude_logs()` 内的 `log_dir` 变量为实际路径。

## 🚀 快速开始
### 1. 生成日报（仅采集与生成）
在 Claude Code 中输入：
```text
/daily-summary
```
#### 执行流程：
1. 脚本自动运行，输出今日 Git 提交、按项目分组的 Claude 对话摘要，以及**从 jsonl 汇总的真实 token 用量与活跃时长**。
2. AI 暂停并询问你今天的沟通、卡点、知识沉淀。
3. 结合数据生成结构化 Markdown，保存到脚本输出的"日报保存位置"路径（形如 `<local_output_dir>/daily-summary-YYYY-MM-DD.md`，脚本已预建目录）。
4. 流程结束，你可以随时在编辑器中微调各任务的耗时/Token 分摊（总量已锚定真实值）。

### 2. 发送最新日报
确认内容无误后，输入：
在 Claude Code 中输入：
```text
/daily-summary:send
```
#### 执行流程：
1. 自动定位 ~/Documents/WorkLogs/ 下最新的日报文件。
2. 解析任务块，组装 Payload，推送到服务器。
3. 反馈发送结果（成功/失败/解析错误）。

### 3. 发送指定历史日报
如果需要补发或重发某天的日报：
```text
/daily-summary:send ~/Documents/WorkLogs/daily-summary-2026-07-01.md
```

## 📝 日报格式规范
AI 生成的日报必须严格遵守以下格式，否则 `send_report.py` 解析会失败。
`AI 辅助耗时` 与 `Token 消耗量` 取自脚本汇总的真实数据，多任务时按投入比例分摊、各任务之和等于当日真实合计：

```markdown
### 任务 1: [修复登录页 Bug] (预估总耗时: 2.5小时)
- AI 辅助耗时: 1.5 小时
- Token 消耗量: 4500 tokens
- 工作详情:
  - 定位到 AuthProvider 状态丢失问题
  - 补充单元测试覆盖边缘场景

### 任务 2: [跨部门需求对齐] (预估总耗时: 1小时)
- AI 辅助耗时: 0 小时
- Token 消耗量: 0 tokens
- 工作详情:
  - 与产品确认 v2.0 数据口径变更

```

> 🔒 安全：日报正文会发往私有服务器。医疗语境下**严禁写入患者标识/密钥/令牌**，只写"做了什么"。发送脚本会在出网前对疑似凭证二次脱敏兜底。

## 🔧 手动发送（稍后发送模式）
如果你选择了“稍后发送”，或者想重新发送已修改的日报：
```bash
# 发送最新日报（无参 → 自动定位 local_output_dir 下最新日报）
python3 .claude/skills/daily-summary/scripts/send_report.py

# 发送指定文件
python3 .claude/skills/daily-summary/scripts/send_report.py ~/Documents/WorkLogs/daily-summary-2026-07-01.md

# 仍兼容管道传入
cat ~/Documents/WorkLogs/daily-summary-2026-07-01.md | \
python3 .claude/skills/daily-summary/scripts/send_report.py
```
> ⚠️ 注意：通过管道（|）或未连接终端触发时，脚本会自动跳过 y/n 确认环节，直接发送。

## 🐛 常见问题排查
| 问题现象 | 可能原因 | 解决方案 |
| :--- | :--- | :--- |
| `❌ 发送失败：未能解析出任何任务块` | Markdown 格式被 AI 修改 | 检查标题是否为 `### 任务 X: [名称]`，括号内字段名是否完整 |
| `⚠️ 当前目录非 Git 仓库` | 在非项目目录触发 | 正常现象，脚本会跳过代码采集，仅基于对话生成 |
| `今日暂无 Claude Code 对话记录` | 时间戳格式不匹配 | 检查 `~/.claude/projects/` 下日志是否存在，脚本已兼容 ISO8601 |
| `❌ 发送失败：未配置 server.url` | 缺少配置文件 | 检查 `config.json` 是否存在且 `server.url` 字段非空 |

## 🛠️ 开发/调试
```bash
# 仅测试数据采集（不发送，不生成文件）
python3 .claude/skills/daily-summary/scripts/gather_data.py

# 测试发送（通过管道传入内容）
echo "### 任务 1: [测试任务] (预估总耗时: 1小时)
- AI 辅助耗时: 1 小时
- Token 消耗量: 100 tokens
- 工作详情:
  - 测试内容" | python3 .claude/skills/daily-summary/scripts/send_report.py
```

## 贡献与反馈
欢迎提交 Issue 或 Pull Request 来完善这个 Skill！如果你在使用过程中发现了更好的 Prompt 或数据提取逻辑，请随时分享。

## 📄 开源协议
[MIT License](./LICENSE)
