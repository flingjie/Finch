# Finch repo-discovery Skill 设计与实施 Plan

日期：2026-10-02  
状态：待实施，按最新需求修订  
目标：每天抓取 X / Twitter 上分享的 GitHub 项目，完整保留可获取的结果，去重后按热度排序。

> 本文未核对最新仓库。命令、字段和配置为拟议接口，实施前需映射到现有代码。本次只更新设计，不部署定时任务或执行抓取。

## 1. 已确认的调整

- 抓取时间窗口内、配置来源能返回的所有项目，不设置候选数量或推荐数量上限。
- 按热度排序，输出完整榜单，分页只用于展示，不截断数据。
- 删除证据分析、代码深读、能力验证和证据等级。
- 删除 Practice 读取、个人关联、行动建议和反应追问。
- 保留 Agent 主题标记与筛选视图，但默认总榜不因主题、热度低、资料少或不符合个人实践而丢弃项目。
- Finch 独立实现，不与 builderDNA 同步，不自动生成 ContentJob 或 Practice。

“所有”的准确范围是：指定时间窗口内，已配置查询与来源实际可获取的全部结果。平台搜索索引、分页和访问限制可能影响覆盖；系统必须记录截断和失败，不宣称覆盖全网全部推文。

## 2. Skill 定位与边界

名称：`repo-discovery`。

建议描述：抓取 X 上分享的 GitHub 项目，聚合推文互动量及 GitHub 基础指标，去重并生成完整热度榜，支持 Agent 视图、分页、筛选与导出。

触发示例：

- “抓取今天推特分享的 GitHub 项目，按热度排序。”
- “显示今天所有 Agent 项目。”
- “按 GitHub stars 排序。”
- “继续抓取上次没完成的部分。”
- “导出今天的完整项目榜单。”

Skill 只负责理解参数、调用 CLI 和呈现结果。抓取、分页、归一化、指标聚合、排序和存储使用确定性代码，不依赖 LLM 做项目价值判断。

不读取源代码或测试文件，不评估技术质量，不替用户判断项目是否值得实践，不自动执行 Star、Follow、回复等外部写操作。项目简介直接采用 GitHub description；为空则显示“暂无简介”。

## 3. 核心流程

1. 确定时间窗口及查询集合。
2. 逐查询遍历可用分页，保存推文、链接和互动指标。
3. 提取 GitHub 项目链接，按推文与仓库身份去重。
4. 获取 GitHub 基础元数据，聚合仓库对应推文的热度。
5. 全量排序，保存榜单及覆盖信息。
6. 分页展示，支持 Agent 视图、其他排序及完整导出。

没有初筛淘汰、Top-N 深读和“是否值得推荐”的分支。

## 4. 抓取策略

### 4.1 时间与覆盖

- 默认最近 24 小时，时区 Asia/Shanghai，持久化使用 UTC。
- 支持用户指定起止时间。榜单归属取决于推文发布时间，不要求仓库当天创建。
- 基础查询覆盖 GitHub 链接分享；Agent、harness、eval、memory 等主题查询作为补充召回，不替代基础查询。
- 复用现有 opencli 适配层，实施时核实真实支持的查询语法、排序方式、分页、时间过滤和展开链接能力。
- 能使用最新时间排序时优先采用，避免只抓热门结果。
- 遍历到明确的末页或时间边界才标记该查询已完成。重复游标、平台结果上限、限流或授权失败须记录为未完成。
- 平台支持时间分片时，可拆分窗口补抓被结果上限截断的查询；不能解除的上限保留为 coverage limitation。

### 4.2 无数量上限与可恢复运行

不设置 `max_tweets=200`、`max_repo_candidates=40`、`recommendation_limit=3` 等业务截断。

单次执行仍使用超时、并发和资源预算保护进程。预算用尽时保存游标、待处理队列与阶段检查点，状态设为 partial；后续继续消费同一个固定窗口，不能悄悄停止并声称完成。

分页游标失效时重新查询相应时间片，依靠稳定 ID 去重。增量抓取使用少量时间重叠，避免边界遗漏；重叠推文只计一次。

### 4.3 URL 和身份处理

- 优先读取链接实体中的展开 URL；正文提取作为补充。
- GitHub issues、pull、tree、blob、releases 等链接归一到所属仓库，同时保留原始分享链接。
- 同一推文含多个项目时，每个项目都收录；同一仓库被多人分享时合并为一项。
- 优先用 GitHub repo ID 处理重命名；未取得 ID 时使用规范化的 owner/repo 作为临时身份。
- 个人主页、gist 和非仓库链接放入非项目记录，不混入仓库榜单。
- 无法展开的短链进入待解析队列。仓库元数据获取失败但 URL 可识别的项目仍入榜，字段置空，不因抓取失败丢弃。
- 短链展开限制跳转次数、响应大小和超时，禁止访问私网地址；外部文本不作为执行指令。

## 5. 热度排序规则

### 5.1 默认：X 分享互动热度

默认按本次窗口内分享项目的推文互动量排序，因为发现来源是 X。采用透明的初始规则：

```text
单条推文热度 = likes + 2 × reposts + 2 × quotes + replies
项目热度 = 关联的去重推文热度之和
```

这些权重是可配置的产品选择，不代表通用行业标准。展示原始指标及规则版本，使榜单可复算。

- 只聚合发布时间位于当前窗口的推文；互动数是抓取时累计值，不称为“过去 24 小时新增互动”。
- 同一 tweet ID 在不同查询、分页或重跑中只计一次，刷新指标时替换快照，不累加新旧值。
- 原生转推壳不作为新的原创分享重复累加其原文互动；引用推文有独立 ID 时可独立计入。确认适配层的 repost/quote 语义，避免同一个计数被重复纳入公式。
- 一条推文提到多个仓库时，每个仓库获得相同的推文关联热度；榜单明确它是“提及该仓库的推文互动量”，不是归因到仓库的精确贡献。
- 默认不加入时间衰减、个人相关性、质量分或模型评分。
- 同热度时依次按分享推文数、GitHub stars、规范 URL 排序，确保结果稳定。

### 5.2 缺失指标

所有原始缺失值保存为 null，不能伪装成真实零值。若适配层无法提供某项指标，该运行对全部项目统一去掉对应公式项，并展示实际公式。

只有个别推文缺失指标时，以已知项计算暂定分，标记 `metrics_partial`；全部互动指标缺失的项目热度为 null，放在已知分数之后，仍完整保留。不用 GitHub stars 暗中代替缺失的 X 热度。

保留每项指标的抓取时间，展示榜单刷新时间。互动数变化后重新计算排名。

### 5.3 其他排序视图

| 排序参数 | 含义 | 缺失处理 |
| --- | --- | --- |
| x_heat（默认） | 上述 X 互动聚合分 | 未知置后，保留项目 |
| mentions | 去重分享推文数 | 来自已抓取推文 |
| github_stars | 当前 GitHub stars 总数 | 未知置后 |
| stars_delta | 两次有效快照之间的 stars 变化 | 无基线则未知，显示实际间隔 |
| latest | 当前窗口内最近一次分享时间 | 来自推文时间 |

GitHub stars 与 X 互动量分列展示，不混成不透明总分。首版必须支持前三项和 latest；stars_delta 可在有快照后启用，不得在第一天编造增长量。

## 6. Agent 视图

用户原本特别关注 Agent，因此保留便捷筛选，但不影响全量收录和默认热度顺序。

首版采用仓库名、description、GitHub topics 和关联推文中的配置关键词做轻量标记，输出 `agent / other / unknown`。不读 README 或代码，不对能力作推断；模糊匹配仅作为标签，允许用户更正。

默认总榜显示所有项目并附主题标签；`--topic agent` 在已收录集合上生成 Agent 榜，仍按热度排序。Agent 标签不等于内容质量认证。

## 7. 输出格式

榜单头部显示时间窗口、刷新时间、实际热度公式、来源查询数、去重推文数、仓库数、抓取是否完成及缺失字段情况。

完整列表字段：

| 字段 | 内容 |
| --- | --- |
| 排名 | 当前视图中的顺序 |
| 项目 | owner/repo 与 GitHub URL |
| 简介 | GitHub description；缺失时明确标记 |
| 标签 | Agent / 其他 / 未分类 |
| X 热度 | 聚合分，缺失或部分缺失标记 |
| 分享数 | 去重推文数 |
| 互动明细 | 点赞、转推、引用、回复 |
| GitHub 指标 | stars、forks，可为空 |
| 最近分享 | 当前窗口内最后分享时间 |
| 来源 | 对应推文链接，可展开全部 |

分页默认每页 50 条，仅影响渲染。聊天中展示“第 1 页，共 N 条”，明确后续页入口，并提供全部数据导出能力。禁止把第一页称为完整结果。

导出支持 JSON 和 CSV：JSON 保留项目及所有推文关联，CSV 为每仓库一行并包含来源 URL 集合。排序、缺失标记和运行状态与页面保持一致。

## 8. 最小数据契约与持久化

优先扩展现有 workspace/repository，不新增数据库或 Graph Runtime。

### TweetRecord

`tweet_id`、URL、作者、发布时间、正文、展开链接、转推/引用关系、互动快照、指标抓取时间、采集查询引用。唯一键为 tweet ID。

### RepoRecord

稳定 ID 或临时规范 URL、owner/repo、description、topics、stars、forks、元数据抓取时间、关联 tweet IDs、主题标签、字段获取状态。保留元数据未知的可识别仓库。

### RankingSnapshot

run_id、固定时间窗口、排序方式、热度规则版本、有效公式项、按序仓库 ID、聚合指标、缺失标记和生成时间。快照可由原始记录复算。

### DiscoveryRun

run_id、窗口、配置指纹、查询清单、各查询游标和状态、待处理链接、统计、阶段检查点、错误与续跑信息。状态为 running / completed / partial / failed。

不包含 claims、evidence_status、practice_refs、relevance_reason、boundaries、recommendation、next_action 或作者立场。无需分析提示词或个人上下文指纹。

原子保存抓取结果后再推进游标。重复运行更新已有推文和仓库，榜单重新计算；不累加旧快照。保留有过期恢复能力的运行锁，避免手动和每日调度重复写入。

## 9. CLI 与配置提案

```bash
finch repos discover --lookback-hours 24 --all --sort x_heat
finch repos list --run <run-id> --sort x_heat --page 1 --page-size 50
finch repos list --run <run-id> --topic agent --sort x_heat
finch repos list --run <run-id> --sort github_stars
finch repos discover --resume <run-id>
finch repos export --run <run-id> --format csv --output <path>
```

`--all` 表示遍历可获取结果，不绕过平台限制；默认行为亦为全量抓取，不再提供默认 Top-N 推荐。

```yaml
repo_discovery:
  enabled: true
  timezone: Asia/Shanghai
  lookback_hours: 24
  collect_all: true
  default_view: all
  default_sort: x_heat
  page_size: 50
  ranking:
    likes_weight: 1
    reposts_weight: 2
    quotes_weight: 2
    replies_weight: 1
  agent_tags:
    enabled: true
    keywords:
      - agent
      - agentic
      - harness
      - tool calling
      - multi-agent
  execution:
    resumable: true
    per_slice_budget_seconds: 600
```

复用现有 opencli profile、并发、超时及重试配置。每个执行片段的预算不构成总结果数量上限；达到预算就持久化并续跑。

每日调度调用同一 CLI，优先恢复未完成窗口，再开启新窗口，防止并发积压。具体运行时间部署时配置，本 Plan 不表示任务已创建。

## 10. 故障语义

- 部分查询、短链或元数据失败：保存所有已收录项目，输出 partial 和可续跑任务。
- 所有来源失败：failed，不能输出“今天没有项目”。
- 所有配置查询正常结束且无项目：completed，零结果。
- 查询返回平台上限：partial，并标明已知覆盖限制；能分片则分片续抓。
- 某些字段本来不受来源支持：运行可完成，但必须显示降级公式和字段不可用，不无止境重试。
- 已知抓取失败造成的指标缺失：保留项目及暂定排名，重试后刷新。
- 默认不把旧窗口缓存混进当天榜单。缓存元数据可以复用，但保留其真实抓取时间。

## 11. 实施步骤

### P0：核对现有接口

阅读 AGENTS.md、Skill 目录、CLI 注册、opencli 来源适配层和 workspace，实现文件位置以最新代码为准。确认当前 repository_discovery 是否只服务用户自己的仓库，避免覆盖原逻辑。验证来源提供的互动字段、转推语义、分页方式及查询上限。

交付：实际改动文件清单、字段映射、抓取覆盖限制和可用热度公式。

### P1：全量采集与续跑

实现查询分页、时间窗口、链接解析、推文/仓库去重、检查点和失败恢复。去掉候选数及推荐数截断。对平台截断提供分片补抓能力（来源支持时）。

交付：能遍历到来源末页、保存全部项目并续跑的 CLI。

### P2：指标聚合与排序

获取 GitHub 基础指标，聚合 X 互动快照，完成缺失值、原生转推去重、稳定排序及公式版本管理。实现 Agent 标签与筛选视图。

交付：可复算的总榜和 Agent 榜；不调用实践或证据分析服务。

### P3：Skill、导出与每日运行

编写 Skill 触发、参数映射、分页和故障输出。实现 JSON/CSV 完整导出，在用户配置运行时间后接入现有调度器。删除旧设计中的项目深读、推荐追问和内容/实践转化入口。

交付：Skill、CLI、配置示例、真实运行报告、使用说明及必要的回归测试。

## 12. 验收标准

| 验收场景 | 预期结果 |
| --- | --- |
| 固定来源有 300 条推文、80 个仓库 | 全部遍历、80 个仓库全部收录，非只取 200/40/3 |
| 同一推文被多个查询命中 | 指标只计算一次 |
| 同一仓库被多条推文分享 | 合并仓库，保留所有去重来源 |
| 原生转推重复返回原文指标 | 不把原文互动重复累加 |
| 一个推文包含多个仓库 | 全部收录，关联热度口径明确 |
| 低热度、非 Agent、简介为空 | 仍在总榜中 |
| 互动字段部分缺失 | null 与零区分，显示有效公式和暂定分 |
| 仓库元数据获取失败 | 保留可识别项目，未知值置空 |
| 重跑或刷新互动指标 | 替换快照后重算，不累加旧值 |
| 时间窗口与榜单排序 | 仅聚合窗口内推文，相同输入产生相同顺序 |
| 页大小为 50、结果超过 50 | 所有项目可翻页、完整导出 |
| 预算耗尽或平台截断 | partial，检查点可恢复，不能误报完成 |
| 无用户 Practice 配置 | 正常运行，无实践读取依赖 |
| 热度榜生成 | 无证据分析、代码深读或 LLM 价值评分调用 |

评估只关注抓取完成情况、可识别项目保留率、去重准确性、排序复算一致性、耗时与失败恢复。全网召回率无法从有限搜索结果直接估算；有界测试数据中的仓库保留率和排序一致性应达到 100%。

---

## Appendix A — Code mapping (2026-10-02 P0)

核对基线：`main` HEAD at implementation start. `repository_discovery` 仅经 `gh api user/repos` 发现**当前登录用户**自己的仓库，与本功能无关，不得复用或覆盖。

### Adapter probe (`opencli twitter search --help`)

| 能力 | 结果 |
|---|---|
| 输出列 | `id, author, bio, text, created_at, likes, views, url, has_media, media_urls, media_posters, card, quoted_tweet` |
| reposts / quotes / replies 计数字段 | **不在输出 schema**（`--top-by-engagement` 内部用到 retweets/replies/bookmarks，但不返回给调用方） |
| 分页游标 | **无**；仅 `--limit`（默认 15） |
| 时间过滤 | 查询字符串支持 X operators：`since:YYYY-MM-DD`、`until:` 等 |
| Latest 排序 | `--product live`（Latest tab） |
| 链接过滤 | `--has links`；排除原生转推 `--exclude retweets` |

### Heat formula v1（已确认）

```text
formula_version: x_heat_v1_likes_only
effective_formula: 单条推文热度 = likes；项目热度 = 去重推文 likes 之和
unavailable: reposts, quotes, replies  （适配层不返回）
display_only: views  （可展示，不进入热度）
```

权重配置仍保留 `reposts_weight` 等，以便适配层日后暴露字段时升到 `x_heat_v2` 无需改契约。当前运行一律写入 `effective_formula` 与 `formula_version`。

### 实现落点（与现有代码隔离）

| 组件 | 路径 |
|---|---|
| 领域 | `src/finch/repos/`（新包；不写入 `DiscoverySnapshot`） |
| CLI | `finch repos discover\|list\|export` |
| 配置 | `settings.repo_discovery`（**不是** `repository_discovery`） |
| GitHub 元数据 | 扩展 `PublicRepo` + `gh api repos/{owner}/{repo}` |
| 存储 | `var/repos/{run_id}/` |
| Skill | `skills/repo-discovery/` |

