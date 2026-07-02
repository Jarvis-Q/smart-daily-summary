# smart-daily-summary
Turn your AI coding sessions and git commits into actionable daily reports. A customizable skill for Claude Code to track insights, blockers, and knowledge.

#  Claude Code Daily Digest

一个专为 Claude Code 打造的自动化工作流 Skill。它能自动提取你的 Git 提交记录与 AI 对话洞察，并结合手动补充的沟通记录与排坑经验，一键生成结构化的每日工作摘要（Daily Summary）。

##  核心特性

-  **AI 对话回溯**：自动扫描 Claude Code 对话日志，提炼核心 Prompt 与关键技术 Insight。
-  **Git 提交追踪**：自动汇总当天的代码提交记录与变更统计。
-  **全方位复盘**：主动引导记录会议沟通、技术卡点（Blockers）与知识沉淀。
-  **灵活配置**：支持自定义日报保存路径，文件名自动附加日期后缀。
- ️ **即插即用**：基于 Claude Code Skill 机制，复制到项目即可使用。

##  安装指南

### 1. 克隆或复制文件
将本仓库中的 `.claude/skills/daily-summary` 目录完整复制到你的项目根目录下：

```bash
# 在你的项目根目录下执行
mkdir -p .claude/skills
# or 
# cd ./claude/skills

cp -r /path/to/claude-code-daily-digest/daily-summary .claude/skills/
```

### 2. 赋予脚本执行权限

```bash
chmod +x .claude/skills/daily-summary/scripts/gather-data.py
```

### 3. 配置日志路径（可选）
如果你的 Claude Code 对话日志不在默认路径 ~/.claude/projects/，请编辑 scripts/gather-data.py，修改 LOG_DIR 变量为你的实际路径。

## ️使用方式
在 Claude Code 的交互界面中，输入以下任意指令即可触发：
```text
/daily-summary
```
或
```text
帮我生成今天的日报
```
### 交互流程：
1. AI 自动运行脚本，收集 Git 和对话数据。
2. AI 弹出确认框，询问你是否有需要补充的沟通记录、卡点或知识沉淀。
3. 你回复补充信息后，AI 询问你希望将日报保存在哪个目录。
4. AI 生成 daily-summary-YYYY-MM-DD.md 并写入指定目录。


## 自定义配置
### 修改输出模板
你可以直接编辑 .claude/skills/daily-summary/SKILL.md 中的 ## 输出模板 部分，来调整生成日报的格式和字段。

### 扩展数据采集
如果需要抓取更多维度的数据（如 Jira 任务、本地数据库变更等），可以修改 scripts/gather-data.py 脚本，在输出中追加相应的信息块。

## 贡献与反馈
欢迎提交 Issue 或 Pull Request 来完善这个 Skill！如果你在使用过程中发现了更好的 Prompt 或数据提取逻辑，请随时分享。