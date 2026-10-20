# Daily Summary（每日工作摘要生成器）

一个同时适配 **Claude Code** 与 **Codex** 的日报生成与上报插件。把**数据采集**、**内容生成**、
**审核发送**彻底解耦，适配「先生成、再调整、后发送」的真实工作习惯。

## 命令

**Claude Code：**

| 命令                           | 作用                                                      |
|:---------------------------- |:------------------------------------------------------- |
| `/daily-summary:generate`    | 采集 Git 提交与 AI 对话记录，询问隐性工作，生成日报 Markdown（**不发送**）  |
| `/daily-summary:send`        | 发送最新一份日报到私有服务器                                          |
| `/daily-summary:send <文件路径>` | 发送指定日报（补发历史日报时用）                                        |

> 本插件是 `plugin:skill` 形态，命令**没有**裸的 `/daily-summary`——这是 Claude Code 插件的固有形式，
> 冒号前是插件名、冒号后是 skill 名。

**Codex：**

| 触发方式                            | 作用                       |
|:------------------------------- |:------------------------ |
| `$daily-summary-generate` 或直接说「生成日报」 | 同 Claude Code 的 generate |
| `$daily-summary-send` 或直接说「发送日报」   | 同 Claude Code 的 send     |

## 核心特性

- **真实用量统计**：分别从 Claude Code（`~/.claude/projects`）与 Codex
  （`~/.codex/sessions`）的会话日志汇总**真实 token 用量**（输入/输出/缓存）与
  活跃时长，按项目分组并给出跨端合计，不靠 AI 估算。
- **会话维度**：新增「会话数 / 交互轮次」统计，两端分别计数并合计。
- **高频项目速选**：按历史发送自动统计项目使用频次，生成日报时先列高频清单，
  找不到再走缓存检索、最后才联网拉取。
- **隐性工作捕获**：强制询问「沟通/卡点/知识沉淀」，避免纯代码统计遗漏关键产出。
- **生成与发送分离**：生成不会自动出网；发送是独立命令，内容可先人工过目。
- **出网脱敏**：发送前对疑似密钥/令牌做保守脱敏，只发送 AI 撰写的摘要，不上传原始对话。
- **鲁棒采集**：非 Git 仓库优雅降级；时间戳统一转本地时区后再按「今天」过滤，避免跨午夜错桶。

---

## 安装

### 前置：Python 依赖

```bash
pip3 install -r requirements.txt
```

（依赖 `requests` 与 `python-dotenv`。`python-dotenv` 缺失时脚本不会崩，但读不到 `.env`。）

### 方式一：从私有 GitLab 安装（推荐给团队成员）

在 Claude Code 中执行：

```text
/plugin marketplace add <私有 GitLab 仓库地址>
/plugin install daily-summary@<marketplace>
```

安装后**重启 Claude Code**，`/daily-summary:generate` 与 `/daily-summary:send` 即出现在补全列表中。

### 方式二：本地目录安装（开发/调试）

```text
/plugin marketplace add ~/.claude/local-plugins
/plugin install daily-summary@<marketplace>
```

或启动时临时挂载：

```bash
claude --plugin-dir ~/.claude/local-plugins/plugins/daily-summary
```

### 方式三：安装到 Codex

执行安装脚本（幂等，可重复跑）：

```bash
bash scripts/install_codex.sh
```

它把 `.agents/skills/` 下的两个 skill **软链**到用户级 skill 根目录 `~/.agents/skills/`
（Codex 会自动扫描该目录；软链保证仓库更新后即时生效）。安装后重启 Codex，
输入 `/skills` 或 `$daily-summary-generate` 即可看到。

> Codex 没有插件市场概念，skill 就是其标准扩展形态（`SKILL.md` + 脚本目录）。
> 两个端的 skill 共用同一份 `scripts/`、`config.json` 与 `.env`，配置只需做一次。

---

## 配置（每人必做）

### 1. 凭证：创建 `.env`

`.env` **不在仓库里**（已 gitignore），需自行创建：

```bash
cd <插件目录>
cp .env.example .env
```

然后填入真实值：

```text
SERVER_URL=https://内部服务器/api/worklog     # 向团队负责人索取
DAILY_SUMMARY_TOKEN=你的个人Token             # 每人一份，不要共用
```

> 插件通过 marketplace 安装后位于 `~/.claude/plugins/cache/<marketplace>/daily-summary/<版本>/`，
> `.env` 要放在该目录下（与 `config.json` 同级）。
> **注意**：插件升级会换版本目录，`.env` 需要重新放置。

### 2. 个人配置：复制并编辑 `config.json`

仓库只提供示例 `config.sample.json`，**`config.json` 已 gitignore、不会入库**（含内部项目 id 与本地路径）。
首次使用请复制后修改：

```bash
cd <插件目录>
cp config.sample.json config.json
```

`config.json` **不含密钥**（用 `${SERVER_URL}` / `${DAILY_SUMMARY_TOKEN}` 占位符从 `.env` 解析），
但有几项**因人而异**，安装后请按需修改：

| 字段                             | 说明                                          |
|:------------------------------ |:------------------------------------------- |
| `local_output_dir`             | 日报落盘目录，默认 `~/Documents/worklog/`，脚本会自动创建 |
| `task_item_template.projectId` | 上报所属项目 ID，**请替换为你自己的**，否则工时会记到别人项目下         |
| `defaults.project_id` / `defaults.project_name` | 任务缺项目时的兜底项，示例中为空，**请配置为自己的默认项目** |

`payload_template` 与 `task_item_template` 的字段名需与服务端接口一致，一般不用改。

### 工作分类与所属项目

日报中每个任务可独立指定分类与项目，由 `/daily-summary:generate` 在生成时逐任务询问：

```markdown
### 任务 1: 重构日报发送链路 (预估总耗时: 3小时)
- 工作分类: 开发
- 所属项目: example-示例项目
```

- **工作分类**取值来自 `data/categories.json`（24 项）。写中文名或 code 均可解析，
  推荐写中文名便于阅读。服务端新增分类时手工补充该文件即可。
- **所属项目**须写项目全名，与服务端项目列表精确一致。项目列表由
  `scripts/projects.py` 从服务端拉取并缓存到本地。

两个字段缺失或取值无法识别时，会回退到 `config.json` 的 `defaults` 并打印警告，
不影响发送——老日报无需改造即可继续使用。

### 项目列表管理

```bash
python3 scripts/projects.py refresh                # 拉取全量项目并写入缓存
python3 scripts/projects.py frequent               # 高频项目清单（按历史发送统计，不联网）
python3 scripts/projects.py search <关键词>         # 缓存检索（至多 5 条，高频优先）
python3 scripts/projects.py remote-search <关键词>  # 联网刷新缓存后检索（找新项目用）
python3 scripts/projects.py resolve <项目名>        # 项目名转 id
```

接口地址由 `.env` 中的 `SERVER_URL` 推导（把路径最后一段换成 `projects`），
认证复用 `DAILY_SUMMARY_TOKEN`，无需额外配置。

缓存默认落在 `~/.cache/daily-summary/projects.json`，以 `0600` 权限保存，
放在用户目录而非插件目录——插件更新会重建目录，缓存搁那儿会被冲掉。

**生成日报时的三级查找**（`/daily-summary:generate` 内置流程）：

1. `frequent`：先列按历史发送统计的高频项目（至多 8 条），纯本地、不联网，最快；
2. `search`：不合适再用关键词检索本地缓存（至多 5 条，结果按使用频次优先排序）；
3. `remote-search`：仍找不到或确认是新项目时，联网刷新缓存后匹配。

**频次统计从哪来**：每次发送成功后，`send_report.py` 自动把本次各任务实际使用的项目
累计到 `~/.cache/daily-summary/project-usage.json`（同样 0600 权限，纯本地、不联网）。
用得越多的项目，`frequent` 与 `search` 里排得越前。

**两种读取行为不同，务必区分**：

- `search` / `resolve`：缓存不存在或为空时会**自动拉取一次**并落盘，此后一律用缓存。
- **发送阶段（`send_report.py`）**：只被动读本地缓存文件，缓存不存在或为空时**不会**
  自动联网拉取，而是让所有任务回退到默认项目，并为**每个任务各打印一条 `⚠️` 警告**。
  因此**首次使用本插件前，务必先手动执行一次 `refresh` 建立缓存**，
  否则会遇到"所有任务都归到默认项目"却查不出原因。

项目有变动（新建项目搜不到）时，同样需要手动执行 `refresh` 刷新。

> 项目列表属公司内部数据，仅用于日报的项目选择与发送映射，请勿转存或外发。

### 新增配置项

| 配置项 | 说明 | 默认值 |
|---|---|---|
| `defaults.work_category` | 分类缺失时的兜底 code | `dev` |
| `defaults.project_id` | 项目缺失时的兜底 id | （示例为空，需在 `config.json` 中配置） |
| `defaults.project_name` | 选择项目时展示的默认项名称 | （示例为空，需在 `config.json` 中配置） |
| `projects_cache_path` | 项目缓存文件位置 | `~/.cache/daily-summary/projects.json` |
| `projects_usage_path` | 项目使用频次统计文件位置 | `~/.cache/daily-summary/project-usage.json` |
| `codex_sessions_dir` | Codex 会话日志根目录（日志迁移时才需改） | `~/.codex/sessions` |

---

## 使用

### 生成日报

```text
/daily-summary:generate
```

流程：脚本输出今日 Git 提交、按项目分组的对话摘要与真实用量 → AI 询问你的沟通/卡点/知识沉淀 →
生成结构化 Markdown 存到 `local_output_dir`。生成后可自行在编辑器微调各任务的耗时分摊
（总量已锚定真实值，只做重新分配）。

### 发送

```text
/daily-summary:send                                          # 发最新一份
/daily-summary:send ~/Documents/worklog/daily-summary-2026-07-01.md   # 发指定文件
```

> `:send` 无参时按**文件名字典序**取最新（不是修改时间）。刚补写的历史日报不会被自动选中，
> 必须显式传路径。

---

## 日报格式规范

`send_report.py` 用正则解析，格式错了会解析失败：

```markdown
### 任务 1: 修复登录页 Bug (预估总耗时: 2.5小时)
- 工作分类: 修复BUG
- 所属项目: example-示例项目
- AI 辅助耗时: 1.5 小时
- Token 消耗量: 4500 tokens
- 工作详情:
  - 定位到 AuthProvider 状态丢失问题
  - 补充单元测试覆盖边缘场景

### 任务 2: 跨部门需求对齐 (预估总耗时: 1小时)
- 工作分类: 会议讨论
- 所属项目: example-示例项目
- AI 辅助耗时: 0 小时
- Token 消耗量: 0 tokens
- 工作详情:
  - 与产品确认 v2.0 数据口径变更
```

### 服务端硬约束（务必遵守）

- **单个任务的 `预估总耗时` 必须在 `0.5 ~ 4` 小时之间（含两端）**。服务端逐项校验，
  任一任务越界即整单打回（不是跳过该项）：
  - 小于 0.5 → `工作耗时最少0.5小时`
  - 大于 4 → `工作耗时不能大于4小时`
- 因此拆分粒度要卡在区间内：零碎工作**向上合并**，大块工作**按真实子结构向下拆分**
  （如「基础实现」/「集成收口」），不要为凑数字虚报。
- **`AI 辅助耗时` 与 `Token 消耗量` 可以为 0，不受上述区间约束**——会议、卡点等隐性工作正常填 0。
- 每日任务数与总工时无上限，只卡单项区间。

> **安全**：日报正文会发往私有服务器。医疗语境下**严禁**写入患者姓名/病历号/手机号/身份证号，
> 以及任何密钥、令牌。只写「做了什么」。发送脚本会在出网前对疑似凭证二次脱敏兜底，但那是兜底、不是许可。

---

## 手动调用脚本（不经 AI 工具）

```bash
P=~/.claude/plugins/cache/<marketplace>/daily-summary/<版本>   # 本地开发则为 ~/.claude/local-plugins/plugins/daily-summary

python3 $P/scripts/gather_data.py                       # 仅采集，不发送、不落盘
python3 $P/scripts/send_report.py                       # 发最新
python3 $P/scripts/send_report.py <文件路径>             # 发指定
cat <文件> | python3 $P/scripts/send_report.py          # 管道传入
```

> **注意**：管道传入或未连接终端时，脚本会**跳过 y/n 确认直接发送**（`sys.stdin.isatty()` 判断）。

---

## 常见问题

| 现象                                   | 原因               | 解决                                                                |
|:------------------------------------ |:---------------- |:----------------------------------------------------------------- |
| `工作耗时最少0.5小时` / `工作耗时不能大于4小时`        | 某个任务的 `预估总耗时` 越界 | 按上述约束合并或拆分任务；注意是**整单**打回                                          |
| `发送失败：未能解析出任何任务块`                    | 标题格式被改动          | 检查是否为 `### 任务 X: 名称 (预估总耗时: X小时)`                                 |
| `发送失败：未在 config.json 中配置 server.url` | `.env` 未创建或未被读到  | 确认 `.env` 与 `config.json` 同级；确认已装 `python-dotenv`                 |
| 看不到失败原因                              | 输出被过滤掉了          | 用白名单过滤：`grep -E '"code"\|"msg"\|发送成功\|发送失败'`，别用 `grep -v '^\s*"'` |
| `当前目录非 Git 仓库`                       | 在非项目目录触发         | 正常，跳过代码采集，仅基于对话生成                                                 |
| `今日暂无 Claude Code 对话记录`              | 日志路径不符           | 脚本默认读 `~/.claude/projects/`，如有变更需改 `gather_claude.py` 的 `DEFAULT_PROJECTS_ROOT` |
| `今日暂无 Codex 会话记录`                    | 日志路径不符或当天未用 Codex | 脚本默认读 `~/.codex/sessions/`，如有变更改 `config.json` 的 `codex_sessions_dir`   |
| Codex 里看不到 skill                       | 未安装或未重启         | 重跑 `bash scripts/install_codex.sh` 并重启 Codex；用 `/skills` 确认               |
| 升级插件后发送失败                            | 新版本目录下没有 `.env`  | 重新放置 `.env`                                                       |

## 已知限制

- Git 采集与 AI 对话记录采集均**已支持多目录自动发现**（Claude 会话 cwd + Codex 会话
  cwd + `config.json` 的 `extra_git_dirs` 兜底），在任意一个目录跑一次即可覆盖当天全部
  工作目录，无需逐个切换重跑。
- Codex 会话解析基于 Codex CLI 0.163 的 rollout jsonl 格式；Codex 升级若改格式，
  集中在 `scripts/gather_codex.py` 适配即可。
- 重复提交同一 `workDate` 的服务端行为未知（覆盖或新增记录不明），已发送的日报勿随意重发。
