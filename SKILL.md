---
name: daily-summary
description: 当用户要求"生成日报"、"总结今天工作"、"daily summary"时触发。自动收集 Git 记录和 Claude Code 对话，并主动询问沟通/卡点/知识沉淀，生成全方位结构化摘要。
---

# 每日工作摘要生成器

## 触发方式与执行逻辑
本 Skill 严格根据用户输入的斜杠命令执行对应分支，**禁止混淆执行**：
 
### 分支 1: `/daily-summary` (仅采集与生成)
1. **数据采集**：运行 `python3 ~/.claude/skills/daily-summary/scripts/gather_data.py` 获取 Git 提交与 Claude Code 对话记录。若脚本缺失或非 Git 仓库，优雅降级。
2. **隐性工作补充（强制交互）**：向用户发送以下消息并等待回复：
   > " 基础数据已收集。请快速补充（没有可回复'无'）：
   > 1. ️ **沟通/会议**：重要决议、跨部门沟通或邮件指示？
   > 2.  **卡点/排坑**：技术障碍、环境问题或 Bug 及解决过程？
   > 3.  **知识/资源**：新概念、新工具或关键数据口径？"
3. **生成与保存**：结合数据与用户回复，生成标准化 Markdown 日报，保存至 ``@config.json` 下 `local_output_dir` 生成`daily-summary-YYYY-MM-DD.md`。
4. **结束提示**：告知用户日报已保存，提示可通过 `/daily-summary:send` 发送，或在编辑器中修改后再发送。

### 分支 2: `/daily-summary:send` (发送最新日报)
1. 自动定位 `@config.json` 下 `local_output_dir` 目录下最新生成的 `daily-summary-YYYY-MM-DD.md` 文件。
2. 执行管道命令：`cat <最新文件路径> | python3 ~/.claude/skills/daily-summary/scripts/gather_data.py --send`。
3. 向用户反馈发送结果（成功/失败/解析错误）。

### 分支 3: `/daily-summary:send <文件路径>` (发送指定文件)
1. 接收用户提供的具体文件路径参数。
2. 校验文件是否存在，若不存在则报错提示。
3. 执行管道命令：`cat <指定文件路径> | python3 ~/.claude/skills/daily-summary/scripts/gather_data.py --send`。
4. 向用户反馈发送结果。

## 输出格式规范（仅分支 1 适用）
** 严禁修改标题结构和括号内的字段名，否则会导致 `--send` 解析失败：**
```markdown
### 任务 X: [任务名称] (预估总耗时: X小时)
- AI 辅助耗时: X 小时 (AI 估算)
- Token 消耗量: X tokens (AI 估算)
- 工作详情:
  - 具体工作内容 1
  - 具体工作内容 2
```
(注：AI 辅助耗时和 Token 消耗仅为基于代码提交和对话量的粗略估算，必须加上 (AI 估算) 后缀。用户补充的隐性工作可归纳为独立任务。)