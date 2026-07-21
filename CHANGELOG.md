# 变更记录

## 1.1.0 —— 2026-07-22：一次采集覆盖全天多目录 + 耗时口径升级

**起因**：一天在多个工程目录工作时，旧版 git 采集只看当前 shell 所在仓库（`Path.cwd()`），
换目录就采不到；而按日期命名的日报文件又已存在，重跑会覆盖前一份。

### 新增（`scripts/gather_data.py`，均以 TDD 落地，见 `tests/test_gather_data.py`）

| 函数 | 作用 |
| :--- | :--- |
| `discover_work_dirs(projects_root, target_date, extra_dirs)` | 从今日有 Claude 活动的会话 jsonl 顶层 `cwd` 自动发现全部工作目录，并并入 config 的 `extra_git_dirs` 兜底 |
| `resolve_repo_roots(dirs)` | 用 `git -C <dir> rev-parse --show-toplevel` 归一到仓库根并去重，跳过非 git/不存在目录 |
| `compute_ai_durations(events)` | 移植自 cc-usage 的纯机器耗时（思考+编码，单次 gap 封顶 5 分钟），剔除人操作时间 |

### 修改

- **`get_git_commits`**：从「仅当前仓库」改为「自动发现今日全部工作仓库，逐个采集」。
  **一次执行覆盖全天所有目录**，无需换目录重跑，同名日报文件被覆盖的问题随之消失。
- **`get_claude_logs`**：耗时口径从粗糙的「活跃时长（会话首末跨度之和，含人阅读/打字时间）」
  升级为 `compute_ai_durations` 的「思考+编码机器耗时」。**Token 仍用 jsonl 精确整数汇总**
  （不经 cc-usage 的 `M` 有损换算）。
- **`config.json`**：新增 `extra_git_dirs`（默认 `[]`），兜底那些没走 Claude 的工程目录。
- **`skills/generate/SKILL.md`**：同步「多目录一次采集」「AI 机器耗时」表述与补发历史章节。

### 明确不做（已与使用者确认）

日历会议、Git 未提交改动、`--author` 过滤、shell/浏览器历史、禅道反拉等其他采集源；
token 的 `M` 换算（同源且更精确的整数已在手）。

> **部署提醒**：改动在源码目录 `~/.claude/local-plugins/plugins/daily-summary`，
> 已安装的缓存副本 `~/.claude/plugins/cache/yuntai/daily-summary/1.0.0` **不会自动更新**，
> 需重装/更新插件或重启后由 marketplace 重新同步才会生效。

---

## 1.0.0 —— 2026-07-17：从个人 Skill 迁移为 Plugin

本次改造的起因：`/daily-summary:send` 不会被识别为命令，必须先打 `/daily-summary` 再空格补 `:send`。

### 根因

**带冒号的斜杠命令只有 Plugin 能产生**，个人 Skill 做不到。三点依据：

- 插件的命令形式恒为 `插件名:skill名`，由 `.claude-plugin/plugin.json` 的 `name`
  加 `skills/<名>/SKILL.md` 的目录名拼成（对照 superpowers 的真实结构验证）。
- 个人 skill（`~/.claude/skills/<name>/`）只能产生 `/<name>`，无子命令语法。
- `~/.claude/commands/` 的**子目录不会**生成 `/子目录:命令`——子目录只影响描述，不影响命令名。
  所以这条路走不通。

原先的 `:send` 从来不是命令，只是被当作**参数字符串**传给 `/daily-summary`，
补全列表里自然没有它。

### 结果：命令变更（破坏性）

| 变更前 | 变更后 |
| :--- | :--- |
| `/daily-summary` | `/daily-summary:generate` |
| `/daily-summary` + 手动补 `:send` | `/daily-summary:send` |

> **`/daily-summary` 裸命令不再存在**。插件的命令永远带冒号，没有「只有插件名」的那一个
> （superpowers 同理，只有 `/superpowers:brainstorming`，没有 `/superpowers`）。

### 新增

| 文件 | 作用 |
| :--- | :--- |
| `<仓库根>/.claude-plugin/marketplace.json` | marketplace 清单，`name: yuntai`；`source` 用相对路径 `./plugins/daily-summary` |
| `<仓库根>/.gitignore` | 兜底忽略 `**/.env` |
| `.claude-plugin/plugin.json` | 插件清单，`name: daily-summary` —— **它决定了命令的冒号前缀** |
| `skills/generate/SKILL.md` | 采集与生成（原 SKILL.md 分支 1） |
| `skills/send/SKILL.md` | 发送（原 SKILL.md 分支 2/3） |
| `.gitignore` | 忽略 `.env`、`__pycache__` 等 |
| `.env.example` | 凭证模板，供新成员复制为 `.env` |
| `CHANGELOG.md` | 本文件 |

### 删除

- **`SKILL.md`（单文件）** —— 拆分为 `skills/generate/SKILL.md` 与 `skills/send/SKILL.md`，
  原「分支 1/2/3」的调度说明随之移除：命令本身已经完成分流，不再需要靠文字约束模型选分支。
- **`~/.claude/skills/daily-summary/`（旧个人 skill 目录）** —— 迁移完成并验证后删除，
  否则 `/daily-summary` 与插件命令并存会造成混淆。

### 修改

**`scripts/send_report.py`** —— 修复一个会导致**整单发送失败**的解析缺陷：

`parse_tasks_from_markdown` 用前瞻正则 `(?=### 任务...)` 切分，split 的第一个分片是
「任务 1 之前的所有内容」（日报标题、项目行、分隔线）。该分片非空，被当成一个任务混入列表，
它匹配不到「预估总耗时」，`hours` 落到默认值 `0.0`，触发服务端
`工作耗时最少0.5小时` 校验，**整份日报都发不出去**。

修复：循环内增加守卫，剔除非任务分片。

```python
if not re.match(r'###\s*任务\s*[\d一二三四五六七八九十]+\s*[:：]', chunk): continue
```

**`README.md`** —— 重写为插件安装方式（marketplace / `--plugin-dir`），补充服务端约束、
排查表与已知限制。

**`config.json`、`scripts/gather_data.py`** —— 内容未改。目录层级保持
`<插件根>/scripts/*.py` + `<插件根>/config.json`，因此脚本内
`Path(__file__).parent.parent / "config.json"` 与 `parents[1] / ".env"` 的解析仍然成立。

### 文档化的服务端约束（本次实测得出）

先前只知道要填工时，不知道有区间限制。两次发送失败逐步试出完整规则：

- 单个任务 `预估总耗时` 必须落在 **`0.5 ~ 4`** 小时（含两端），越界**整单打回**：
  - `< 0.5` → `工作耗时最少0.5小时`
  - `> 4` → `工作耗时不能大于4小时`
- `AI 辅助耗时` 与 `Token 消耗量` **可以为 0**，不受此限。
- 每日任务数与总工时无上限（实测单日合计 14 小时可正常提交），**只卡单项区间**。

该约束已写入 `skills/generate/SKILL.md`（生成阶段就要算好粒度）与
`skills/send/SKILL.md`（发送前自检），避免重蹈覆辙。

### 其他写入 skill 的经验

- **`:send` 无参取的是文件名字典序最新**（`sorted(candidates)[-1]`），不是修改时间——
  刚补写的历史日报不会被选中，必须显式传路径。
- **过滤脚本输出要用白名单**。失败原因在服务端返回的 `"code"` / `"msg"` 行（以引号开头），
  用排除式过滤（如 `grep -v '^\s*"'`）会把报错一起滤掉，导致白发一轮。
- **非交互即自动确认**：脚本用 `sys.stdin.isatty()` 判断，经工具调用运行时会直接发送、不再二次确认。

---

## 迁移步骤（原使用者）

1. 安装插件（见 `README.md`），重启 Claude Code。
2. 确认 `/daily-summary:generate` 与 `/daily-summary:send` 出现在补全列表且可用。
3. 确认无误后删除旧的个人 skill 目录，避免命令混淆：
   ```bash
   rm -rf ~/.claude/skills/daily-summary
   ```
   > 该目录内有含真实凭证的 `.env`，删除前先确认插件目录下已放好新的 `.env`。
4. 改口令习惯：`/daily-summary` → `/daily-summary:generate`。
