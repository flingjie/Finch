# 自动关联发布（P0）设计文档

> 状态：已获用户批准（2026-09-06）。
> 目标：在保持 Finch 只读（不负责发帖）的前提下，取消「发布后手动登记 URL」——Finch 持续读取配置作者账号的
> 历史发帖，把「已批准的原创草稿」与「线上已发布的帖子」确定性匹配，自动建立 `draft → remote_post_id` 关联。

## 0. 结论与原则

- **Finch 不发布，只发现**：发布仍由用户在 Finch 外部手动完成；Finch 只读同步自己的历史，识别哪些草稿已上线。
- **稳定身份**：匹配用 `user_id`（来自 `whoami`），不用可能变化的 `handle`。
- **确定性匹配**：exact_text → similar_text（唯一候选）→ 需人工 → 等待发布，全程确定性规则，不引入 LLM 判定。
- **加性**：不删改既有 `Feedback`/`FeedbackSnapshot`/`OutcomeAssessment`/`ReviewDecision`；新增 `AuthorAccount` /
  `AuthorPost` / `PublicationIntent` / `PublicationLink` 四张新表。
- **读取不到 ≠ 0**：平台未暴露的指标（likes/replies/reposts/views）保持 `None`，绝不记成 `0`。
- **范围**：P0 只覆盖「原创草稿 → 原创/引用帖子」的匹配；互动回复（engagement）匹配、指标快照、线程分析、
  反馈回写选择信号、作者基线 均属后续 P1–P3。

## 1. 范围

**本次（P0）**：

- 配置 `author_accounts`（`platform`/`handle`/`enabled`/`history_lookback_days`）。
- `OpenCliClient.whoami()` 与 `OpenCliClient.user_posts(handle, limit)` 两个只读方法 + 字段解码。
- `AuthorAccount` 验证（`whoami` 账号与配置一致 → 存 `user_id`）。
- `AuthorPost` 增量同步（首跑 90 天基线，之后游标之后的新内容）。
- `PublicationIntent`（`accept` 时原子写入）与 `PublicationLink`（确定性匹配结果）。
- `finch author sync --json` 命令（sync + reconcile 一体）；`$finch setup`（Codex 首次确认账号）。

**范围外（明确不做）**：

- 互动（engagement）回复的匹配（`replied_to_post_id` 路径）。
- 指标快照（`MetricSnapshot`）、回复线程分析、`ConversationEvidence` 提取。
- 反馈回写下一轮的选择信号（选题去重、格式优先级）。
- 作者基线（主题/表达/互动分析）与 `AuthorExpression`。
- 修改 `GraphRuntime`/`replay`/`position_gate`；不获得任何发帖权限。

## 2. 背景与现状（已核实）

- 现状反馈是手动的：`finch review feedback <DRAFT_ID> --url <URL> --metrics '<json>'`（用户粘贴链接 + 指标）。
- opencli 的 `_ALLOWLIST` 已含只读命令 `twitter whoami`、`twitter tweets`、`twitter profile`、`twitter timeline`、
  `twitter likes`；但 `OpenCliClient` 只封装了 `search`/`thread`/`bookmarks`/`timeline`/`profile`，
  **没有 `whoami()` 或读「自己发帖」的方法**。
- `Tweet` 模型有 `id`/`author`(handle)/`text`/`likes`/`views`/`quoted_tweet`，但**缺** `replied_to_post_id`、
  `reposts`/`replies` 计数、稳定 `author_id`、`kind`（original/reply/quote）。故 `AuthorPost` 是新模型。
- `DecisionService.accept`（`review/decision.py`）已算 `approved_content_hash` 并持有 `draft.body`，是写入
  `PublicationIntent` 的天然切入点。
- 单决策点（`finch decide accept`）已把「确认立场 + 批准草稿」合并为一次原子落地；本设计在此之上**追加**
  写 `PublicationIntent`，不改变既有落地语义。

## 3. 架构

```
src/finch/
  twitter/opencli_client.py   # + whoami() / user_posts()（只读，复用 _ALLOWLIST）
  author/                     # 新包：账号 + 同步 + 匹配
    models.py                 # AuthorAccount / AuthorPost / PublicationIntent / PublicationLink
    sync.py                   # verify_account / sync_posts（增量 + 游标）
    reconcile.py              # reconcile（确定性匹配，纯函数）
    client.py                 # AuthorPost 解码（opencli JSON → AuthorPost）
  review/decision.py          # accept 追加写 PublicationIntent（加性）
  storage/repositories.py     # + AuthorAccount/AuthorPost/PublicationIntent/PublicationLink 仓储
  cli.py                      # + finch author sync --json；decide accept 透传 intent 仓储
```

## 4. 数据模型（加性）

```python
class AuthorAccount(BaseModel):
    platform: str                 # "x"
    user_id: str                  # whoami 稳定 id
    handle: str                   # 展示用
    verified_at: datetime

class AuthorPost(BaseModel):      # 幂等键 (platform, remote_post_id)
    platform: str
    remote_post_id: str
    author_account_id: str
    kind: Literal["original", "reply", "quote"]
    body: str
    url: str
    published_at: datetime
    replied_to_post_id: str | None = None
    quoted_post_id: str | None = None
    likes: int | None = None
    replies: int | None = None
    reposts: int | None = None
    views: int | None = None

class PublicationIntent(BaseModel):  # 幂等键 source_id（= draft.id）
    source_type: Literal["draft"]     # P0 只 draft；interaction 留 P1+
    source_id: str
    approved_body: str
    content_hash: str
    approved_at: datetime
    expected_kind: Literal["original"]  # P0 只原创草稿

class PublicationLink(BaseModel):     # 幂等键 source_id
    source_id: str
    remote_post_id: str
    matched_by: Literal["exact_text", "similar_text"]
    confidence: float
    linked_at: datetime

class AuthorSyncCursor(BaseModel):    # 幂等键 (platform, author_account_id)
    platform: str
    author_account_id: str
    last_seen: datetime               # 同步水位（读取游标之后的新内容）
```

## 5. 配置

`finch.yaml` 新增 `author_accounts`（独立于 `twitter.queries`）：

```yaml
author_accounts:
  - platform: x
    handle: flingjie
    enabled: true
    history_lookback_days: 90
```

`Settings` 增加 `author_accounts: list[AuthorAccountConfig] = []`（Pydantic）。

## 6. Adapter（只读）

- `whoami() -> dict`：`opencli twitter whoami -f json`，解码 `{user_id, handle}`；失败抛 `TwitterSourceUnavailable`。
- `user_posts(handle, *, limit=200) -> list[AuthorPost]`：`opencli twitter tweets <handle> --limit N -f json`，
  解码为 `AuthorPost`。

**判断 call #1（已获批）**：`twitter tweets <handle>` 是「读某用户自己发帖」的最佳猜测（区别于 `timeline` 的
「首页时间线」）。真实输出 shape 无法在本沙盒验证，故附带 contract test（`tests/fixtures/` 捕获真实输出后回填）；
若真实命令/schema 有出入，只改 `client.py` 的字段映射，不影响上层。

## 7. 同步（增量、幂等）

`sync_posts(account, client, store)`：

1. 首跑（无游标）：读 `history_lookback_days` 天窗口；之后：读游标之后的新内容。
2. 过滤 `kind ∈ {original, reply, quote}`；按 `(platform, remote_post_id)` 幂等 upsert。
3. 更新同步游标（`AuthorSyncCursor`，按 account 存 last_seen 水位）。

## 8. 匹配（确定性，纯函数）

`reconcile(store) -> ReconcileResult`：对每个未链接的 `PublicationIntent`（`expected_kind == original`）：

1. 候选 = 该账号 `AuthorPost`（`kind ∈ {original, quote}`，`published_at >= approved_at`，未被其它 intent 链接）。
2. 正文标准化（折叠空白、去首尾、小写）后：
   - **exact_text**：有且仅有一条 `normalized(body) == normalized(approved_body)` → 链接 `confidence=1.0`。
   - **similar_text**：无 exact 时，用 `difflib.SequenceMatcher` 对标准化正文算相似度，有且仅有一条 `ratio >= 0.85`
     → 链接 `confidence=ratio`。
3. 多个候选（相似度均 ≥ 0.85 或均不唯一）→ 记入 `needs_manual`（返回候选 remote_post_id 列表，供 Codex 点选）。
4. 无候选 → 保持未链接（`awaiting`，下次 sync 后再试）。

返回 `ReconcileResult(linked: list[PublicationLink], needs_manual: list[...], awaiting: list[source_id])`。
`needs_manual` 与 `awaiting` 不落库（每次 reconcile 重算）；`linked` 落 `PublicationLink`。

## 9. 集成与 CLI

- `DecisionService.accept` 追加：`publication_intents.save(PublicationIntent(source_type="draft", source_id=draft.id,
  approved_body=draft.body, content_hash=..., approved_at=..., expected_kind="original"))`。`DecisionService` 构造器
  新增 `publication_intents: PublicationIntentRepository`（加性）。
- `finch author sync --json`：`verify_account → sync_posts → reconcile`，输出 `{synced, linked, needs_manual, awaiting}`。
- `run daily` 顶部（GitHub ingestion 之后、双轨之前）调用 `author sync`（确定性，非 Codex 编排）。
- `$finch setup`：Codex 调 `whoami` 展示当前登录账号 → 确认后写入 `author_accounts`。

## 10. 错误处理

- `whoami`/`user_posts` 的 opencli 失败（未登录/桥离线/限流）→ 抛既有 `TwitterSourceUnavailable`/`TwitterRateLimited`/
  `TwitterError`，`finch author sync` 走 `typer.Exit(1)` 干净错误。
- 账号验证不一致（`whoami` 账号 ≠ 配置 handle）→ 报错并拒绝同步（防止读到另一个浏览器账号）。
- `reconcile` 无网络（只读本地 DB）→ 纯本地，可沙盒运行。

## 11. 测试

- `whoami`/`user_posts` 解码：contract test（fixtures 捕获真实 opencli JSON）；字段缺失/类型错 → 安全降级（指标为 `None`）。
- 同步幂等：同 `remote_post_id` 重复 upsert 不产生重复行；游标推进正确。
- 匹配：exact 唯一、similar 唯一、多候选 `needs_manual`、无候选 `awaiting`、已链接不重复链接。
- `accept` 写 `PublicationIntent`：`decide accept` 后 `source_id` 与 `content_hash` 正确落库。
- CLI：`finch author sync --json` 输出形状；`--json` 只读不弹交互。

## 12. 迁移

- 加性 alembic：新增 `AuthorAccount` / `AuthorPost` / `AuthorIntent` / `PublicationLink` / `AuthorSyncCursor` 表。
- 不删改既有表；`Feedback`/`ReviewDecision` 等旧记录继续可读。

## 13. 与既有设计的关系

本设计在 [[2026-09-06-single-decision-point-design]] 之上：`decide accept` 已是唯一批准点，本设计在其中**追加**
写 `PublicationIntent`，把「批准」扩展为「批准 + 期待发布」。匹配/同步全部确定性、只读，不违反
「No auto-publish」；`user_id` 稳定性沿用 `position_fingerprint` 的确定性思路。
