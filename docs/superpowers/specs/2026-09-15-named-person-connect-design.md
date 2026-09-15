# 点名连接设计（Named-Person Connect）

日期：2026-09-15
状态：待评审

## 背景与结论

用户点名一个人（例如 `iFurySt`）时，当前系统只能走话题搜索发现。`finch peers` 只有
list/show/get；`finch connect create` 需要帖子 URL，且只落 `InteractionProposal`，不落
`PeerProfile`。Agent 因此会翻 `var/peers/` 与今日机会，汇报库存并让用户自己跑 CLI——这违反
`skills/_shared/agent-presentation.md`（不写翻库过程、不把 CLI 当用户下一步）。

点名即视为已选中：跳过 8–12 张浏览卡，直接落身份并准备一条互动提纲。

## 范围

**In：**

- 新命令 `finch connect with --x|--github <handle|url>`：一次完成身份解析、PeerProfile
  落库、选一条近期公开内容、准备默认提纲。
- `Platform` 增加 `github`（PeerProfile 与 ExternalPost 同步）。
- X：近期推文；GitHub：近期公开非 fork 仓库。同名不同平台是两个 PeerProfile。
- Skill / 呈现：点名路径只展示这一张准备卡，禁止库存汇报。

**Out（非目标）：**

- 不引入「当前连接对象」会话状态。
- 不把点名结果写入今日 `DiscoverySnapshot`（不挤掉 `connect today` 浏览名单）。
- 不自动合并跨平台同名身份。
- v1 不接 Reddit；不写 Twitter / GitHub。
- 不新增 `InteractionAction` 枚举；GitHub 提纲仍走现有 outline / question 路径。
- 不改北极星指标，不新建问题库。

---

## 命令与输入

```text
finch connect with --x <handle|url>
finch connect with --github <handle|url>
```

必须恰好一个来源旗标。可选：`--note`、`--from-idea`、`--draft`、`--json`（语义与
`connect create` 相同：无素材只准提问式提纲；`--draft` 才写完整草稿）。

默认准备 **1** 条提纲。不提供 `--limit`。

### 解析规则

| 输入 | 平台 | handle | 内容锚点 |
|---|---|---|---|
| `--x iFurySt` / `@iFurySt` | x | `iFurySt` | 近期推文中选 1 |
| `--x https://x.com/iFurySt` 或 `twitter.com/...` | x | 路径第一段 | 近期推文中选 1 |
| `--x https://x.com/iFurySt/status/123` | x | 作者 | 该帖 |
| `--github iFurySt` | github | `iFurySt`（小写） | 近期公开仓库中选 1 |
| `--github https://github.com/iFurySt` | github | 路径第一段（小写） | 近期公开仓库中选 1 |
| `--github https://github.com/iFurySt/repo` | github | owner（小写） | 该仓库 |

非法：两个旗标、零个旗标、无法解析的 URL。CLI 退出码 1，说明须恰好 `--x` 或 `--github`。

Skill 侧：用户只给裸 handle、未说来源时，先问 X 还是 GitHub，再调用本命令。禁止为此翻库。

---

## 领域对象

### Platform

`src/finch/peers/models.py` 与 `src/finch/engagement/models.py` 的 `Platform` 均改为
`Literal["x", "reddit", "github"]`。`profile_url_for("github", ...)` 返回
`https://github.com/{handle}`。

GitHub `author_id` 使用 login **小写**，保证 `peer_id_for("github", "IFuryst")` 与
`peer_id_for("github", "ifuryst")` 相同。X / Reddit 的 author_id 保持现状（不在本设计里改大小写）。

`peer_id_for("x", "ifuryst") != peer_id_for("github", "ifuryst")`。禁止因 handle 相同而
`merge_identity`。

### ExternalPost（GitHub 仓库映射）

一条公开仓库映射为一条 `ExternalPost`：

- `id`：`name_with_owner`（如 `iFurySt/finch`）
- `platform`：`github`
- `url`：仓库 html_url
- `author_id` / `author_name`：owner login（小写 / 原样显示名）
- `content`：仓库 description；为空则用 `name_with_owner`（事实名称，不编描述）
- `published_at`：`pushed_at`；解析失败则丢弃该条（禁止伪造时间）

不把用户自己的证据流水线（`list_user_repos` / Commit → EvidenceCard）用于对方仓库。

### Opportunity / Proposal

- 用现有 `scored_post_to_opportunity` 与 `generate_proposals`。
- `Opportunity.discovered_via` 写 `named:x` 或 `named:github`。
- 落 `PeerRepository`、`OpportunityRepository`、`InteractionRepository`。
- **不**调用 `_persist_discovery`，**不**写入 `DiscoverySnapshot`。
- `generation_key` 仍按 `peer + post + action + prompt_version`；相同 key 幂等返回已有提案。

GitHub 来源不新增 action：`choose_action` 选出的草稿类动作当作「可发送讨论提纲」；
`observe_author` / `bookmark` 允许。卡片第一行来源为 `来源：GitHub {handle}` 或
`来源：X @{handle}`。Finch 不代发 GitHub 评论。

---

## 数据流

```text
parse --x|--github
  → 解析身份（handle + 可选具体 URL）
  → 确认身份存在：具体内容 URL 抓取成功即算身份成立；
    否则 X `profile` / GitHub `user`
  → PeerService.from_author + merge_discovered → upsert PeerProfile
  → 取公开内容（具体 URL 优先，否则近期列表）
  → score_posts + rank_candidates
  → 空排名时走 connect create 同款人工合成分（点名已选中，允许提问提纲）
  → 取总分最高 1 条
  → 贡献点检查（有 --note/--from-idea 时）
  → generate_proposals（默认 outline）
  → upsert Opportunity + InteractionProposal
  → 渲染 1 张准备卡
```

逻辑放在新模块 `src/finch/engagement/named.py`（纯编排 + 解析；LLM 仍只经现有
`score_posts` / `generate_proposals`）。`cli.py` 的 `connect with` 只做参数校验与渲染。

身份确认成功即落 PeerProfile，即使后续「暂不回复」。这样点名过的人会出现在
`finch peers list`，但不会出现在今日发现快照里。

---

## 取内容

### X

1. `OpenCliClient.profile(username)`：无结果 → 抓取失败，不落假档案。
2. 具体 status URL：复用 `fetch_post_by_url`。
3. 否则：新增 `OpenCliClient.tweets(username, limit=20)`，封装已在 allowlist 的
   `opencli twitter tweets`。该调用失败再退到 `search("from:{handle}", limit=20)`。
4. 映射为 `ExternalPost`（现有 `_to_external_post`）；时间为空的推文丢弃。

### GitHub

新增只读方法（不改 `list_user_repos`）：

- `GhClient.user(login)`：`gh api users/{login}`，取 `login` / `html_url`。404 → 失败。
- `GhClient.list_public_repos(login, limit=20)`：
  `gh api users/{login}/repos?type=owner&sort=pushed&direction=desc&per_page=...`
  过滤 private / fork / archived / disabled；`pushed_at` 不可解析则跳过。

具体仓库 URL：`gh repo view owner/repo --json ...`（须公开）。私有或 404 → 失败。

---

## 失败与零结果

| 情况 | PeerProfile | Opportunity / Proposal | 退出 | 用户可见 |
|---|---|---|---|---|
| 非法参数 | 不写 | 不写 | 1 | 须恰好 `--x` 或 `--github` |
| 身份不存在 / 抓取失败 | 不写 | 不写 | 1 | 说明哪个来源失败，不编档案 |
| 身份在、无可用公开内容 | 写入，`evidence_status=pending_review` | 不写 | 0 | `暂不回复：没有可讨论的公开内容` |
| 有内容但无贡献增量（有 note/idea 时） | 已写入 | 不写 | 0 | 与 `connect create` 相同的暂不回复原因 |
| 模型未产出提纲 | 已写入 | 不写 | 0 | `暂不回复：模型未产出可用提纲…` |
| 敏感内容 gate | 已写入 | 不写 | 0 | 暂不回复（secret） |

无素材时的贡献规则与 `connect create` 一致：仅提问式提纲，禁止声称亲历。

---

## 呈现（Skill）

修改：

- `skills/interaction-preparation/SKILL.md` 与 `references/presentation.md`：CLI 增加
  `connect with`；点名后只展示这一张卡（带来源标签），下一步仍是「批准 / 改提纲 / 跳过」。
- `skills/peer-discovery/SKILL.md`：用户点名具体人时，这不是发现请求，交给
  interaction-preparation，不跑 `connect today` 凑名单。
- `skills/_shared/agent-presentation.md`：点名路径禁止报 Peer 总数、今日其他候选人、
  已有 proposals、或让用户自己跑 `finch peers` / `connect create`。

Agent 对用户说话的形状：

```text
已把 @iFurySt（X）收进连接对象，并准备了 1 条提纲：

**回复 @iFurySt：…** 〔来源：X〕
…
回复「批准」「改提纲」或「跳过」。
```

GitHub 把「回复」换成「讨论提纲」，并标明需用户自己发出。

---

## 测试

全部用假 runner / 假 opencli / 假 gh，不打真实网络。

- 解析：handle、`@handle`、主页 URL、status URL、仓库 URL；GitHub login 大小写归一。
- 互斥：两个旗标 / 零旗标 → exit 1。
- `peer_id_for("x", h) != peer_id_for("github", h)`；同平台重复 `with` 幂等（同一 peer id，
  同一 `generation_key` 不双写提案）。
- X：profile 失败不落库；有推文则落 peer + proposal。
- GitHub：user 404 不落库；有公开仓库则 `ExternalPost.platform == "github"`。
- 无公开内容：peer 在、提案不在、stdout 含「暂不回复」。
- 不写入 `DiscoverySnapshot`。
- 无 `--note` 时提纲路径不得把外部内容写成用户亲历（沿用现有 lived-claim 检查）。

---

## 架构约束

- 确定性 Python 负责解析、过滤、排名截断、幂等、落库；LLM 不输出 `total`。
- 子进程参数用数组；`gh` / `opencli` 只读。
- 不新增 Graph Runtime、不改 Workspace 存储形态。
- 批准仍不等于已发布；`guard.evaluate_execution` 不变。
