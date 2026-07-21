# Daily Summary（每日工作摘要生成器）

一个为 Claude Code 设计的日报生成与上报插件。把**数据采集**、**内容生成**、**审核发送**彻底解耦，
适配「先生成、再调整、后发送」的真实工作习惯。

## 命令

| 命令                           | 作用                                                      |
|:---------------------------- |:------------------------------------------------------- |
| `/daily-summary:generate`    | 采集 Git 提交与 Claude Code 对话，询问隐性工作，生成日报 Markdown（**不发送**） |
| `/daily-summary:send`        | 发送最新一份日报到私有服务器                                          |
| `/daily-summary:send <文件路径>` | 发送指定日报（补发历史日报时用）                                        |

> 本插件是 `plugin:skill` 形态，命令**没有**裸的 `/daily-summary`——这是 Claude Code 插件的固有形式，
> 冒号前是插件名、冒号后是 skill 名。

## 核心特性

- **真实用量统计**：直接从 Claude Code 的 jsonl 日志汇总**真实 token 用量**（输入/输出/缓存）与
  活跃时长，按项目分组，不靠 AI 估算。
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
/plugin install daily-summary@yuntai
```

安装后**重启 Claude Code**，`/daily-summary:generate` 与 `/daily-summary:send` 即出现在补全列表中。

### 方式二：本地目录安装（开发/调试）

```text
/plugin marketplace add ~/.claude/local-plugins
/plugin install daily-summary@yuntai
```

或启动时临时挂载：

```bash
claude --plugin-dir ~/.claude/local-plugins/plugins/daily-summary
```

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

> 插件通过 marketplace 安装后位于 `~/.claude/plugins/cache/yuntai/daily-summary/<版本>/`，
> `.env` 要放在该目录下（与 `config.json` 同级）。
> **注意**：插件升级会换版本目录，`.env` 需要重新放置。

### 2. 个人配置：编辑 `config.json`

仓库里的 `config.json` **不含密钥**（用 `${SERVER_URL}` / `${DAILY_SUMMARY_TOKEN}` 占位符从 `.env` 解析），
但有两项**因人而异**，安装后请按需修改：

| 字段                             | 说明                                          |
|:------------------------------ |:------------------------------------------- |
| `local_output_dir`             | 日报落盘目录，默认 `~/{your_local_dir}/worklog/`，脚本会自动创建 |
| `task_item_template.projectId` | 上报所属项目 ID，**请替换为你自己的**，否则工时会记到别人项目下         |

`payload_template` 与 `task_item_template` 的字段名需与服务端接口一致，一般不用改。

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
/daily-summary:send ~/{your_local_dir}/worklog/daily-summary-2026-07-01.md   # 发指定文件
```

> `:send` 无参时按**文件名字典序**取最新（不是修改时间）。刚补写的历史日报不会被自动选中，
> 必须显式传路径。

---

## 日报格式规范

`send_report.py` 用正则解析，格式错了会解析失败：

```markdown
### 任务 1: 修复登录页 Bug (预估总耗时: 2.5小时)
- AI 辅助耗时: 1.5 小时
- Token 消耗量: 4500 tokens
- 工作详情:
  - 定位到 AuthProvider 状态丢失问题
  - 补充单元测试覆盖边缘场景

### 任务 2: 跨部门需求对齐 (预估总耗时: 1小时)
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
- **`AI 辅助耗时` 与 `Token 消耗量` 可以为 0**，**`AI 辅助耗时`必须在 `0 ~ 4` 小时之间（含两端）**不受此约束——会议、卡点等隐性工作正常填 0。
- 每日任务数与总工时无上限，只卡单项区间。

> **安全**：日报正文会发往私有服务器。医疗语境下**严禁**写入患者姓名/病历号/手机号/身份证号，
> 以及任何密钥、令牌。只写「做了什么」。发送脚本会在出网前对疑似凭证二次脱敏兜底，但那是兜底、不是许可。

---

## 手动调用脚本（不经 Claude Code）

```bash
P=~/.claude/plugins/cache/yuntai/daily-summary/<版本>   # 本地开发则为 ~/.claude/local-plugins/plugins/daily-summary

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
| `今日暂无 Claude Code 对话记录`              | 日志路径不符           | 脚本默认读 `~/.claude/projects/`，如有变更需改 `gather_data.py` 的 `log_dir`   |
| 升级插件后发送失败                            | 新版本目录下没有 `.env`  | 重新放置 `.env`                                                       |

## 已知限制

- `gather_data.py` 的日期**写死为今天**（`date.today()` + `git log --since=midnight`），不支持指定日期。
  补发历史日报需复制脚本改日期来源，详见 `skills/generate/SKILL.md` 的「补发历史日报」章节。
- Git 采集**仅覆盖当前工作目录所在仓库**；Claude 对话记录覆盖全部项目。
- 重复提交同一 `workDate` 的服务端行为未知（覆盖或新增记录不明），已发送的日报勿随意重发。
