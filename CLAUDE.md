# CLAUDE.md

Guidance for Claude Code (claude.ai/code) working in this repository.

## Project

Finch is a cross-industry connection and inspiration assistant, not a content-generation tool. It finds people in other fields who keep creating and sharing first-hand experience, via `gh` (GitHub evidence) and read-only `opencli` (Twitter/X, Reddit, GitHub users, V2EX, WeChat, Xiaohongshu). It explains why a conversation is worth having, prepares a reviewable contribution, and threads real replies into relationship context. Inspiration notes capture new questions, viewpoints, and methods from observation, conversation, and practice. Public expression — forming the user's own viewpoint and writing in their voice — is an optional outlet; content production is the _result_ of connection, not the goal. Problem/workaround/usage feedback lives as conversation-thread notes, not a separate problem or payments domain. The north-star metric is how many "contextual, continuable" peer relationships are added or deepened each week. Canonical definition: `docs/product-contract.md`. Finch is fully standalone — no builderDNA dependency.

## Commands

```bash
uv sync                 # install dependencies (Python 3.12+)
uv run pytest           # full test suite
uv run ruff check .     # lint + format checks (line-length 100, py312)
uv run mypy src         # type-check
uv run finch <command>  # CLI entry point (typer)
```

Run a single test file/pattern with `uv run pytest tests/unit/test_foo.py -k name`.

CLI surface (typer sub-apps / commands): `finch connect ...` (today / daily / person / record-presented / prepare / feedback / assess / artifact-status), `finch connections ...` (today / record / follow-up), `finch peers ...` (list / show / get), `finch people shortlist` (`--today` 今日承诺面，等同 `connections today`；`--all` 全部线索), `finch conversations ...` (list / show / get / follow-up / ingest / note / commit / experiment / mark-important / defer / close), `finch dialogue ...` (save / search / show / forget), `finch ideas ...` (commit / create / choose / list / show / confirm / revise-position / skip), `finch inspirations ...` (save / list / show / note / archive), `finch community ...` (context / save / inspect / feedback / list / run / runs / run-trace), `finch practice ...` (start [--method] / diagnose / save / finish [--verdict] / show), `finch article ...` (analyze / show), `finch methods ...` (save / list / show), `finch drafts ...` (create / write / show / revise), `finch review ...` (list / show / approve / revise / skip / weekly), `finch weekly`, `finch learn <draft_id> ...` (记录发布反馈), `finch voice ...` (show / approve-example / reject-example / revoke-example / propose), `finch profile ...` (show / confirm / revoke / add / init — 用户已确认的真实实践，注入机会评估与贡献制作), `finch github reflect`, `finch twitter ...` (search / import-bookmarks / diagnose), `finch sources ...` (doctor / sync；twitter|reddit|github|v2ex|weixin|xiaohongshu), `finch repos ...` (discover / list / export — X 分享的 GitHub 仓库热度榜；与 `repository_discovery` 无关), `finch collisions ...` (generate / weekly), `finch experiments start`, `finch init`, `finch diagnose`, `finch context` (daily/pending projections).

## Architecture

Skill + domain services, not an LLM agent loop and not a graph runtime. Connection and expression skills run the default loops. Collision and experiment skills support cross-domain inspiration. Training tools never enter the default pipeline. Ordering, state, retries, and idempotency live in deterministic Python domain services. Codex (`codex exec`) is called as a subprocess only at specific "smart" steps (assess / write / critic).

```
skills/
  peer-discovery/          公开内容 → 首页 0–1 首选机会 + --view browse 的 50 人分层（5/15/30，同一快照）+ PeerProfile（finch connect daily）
  repo-discovery/          X 分享的 GitHub 仓库 → 完整热度榜（finch repos discover/list/export；无 Top-N；不读 Practice）
  creator-evidence/        有限工件 → CreatorEvidence（必须引用 artifact_id；不改关系状态）
  connection-opportunity/  判断有无真实贡献（无贡献则 SKIP，不硬连）
  interaction-preparation/ 选中后深度准备（finch connect prepare --opportunity [--reaction]；无反应只出澄清问题；单次不超过 deep_prepare_limit，默认 5）
  reply-crafting/          公开回复草稿（观察 → 真实经验 → 一个问题）；不发布
  conversation-follow-up/  按真实触发恢复对话（finch conversations follow-up / ingest）
  relationship-review/     由互动记录判断阶段与是否联系；时间陈旧 alone 不触发对外联系
  idea-discovery/          Commit/PR/测试 + 用户片段 + 已验证对话 → ContentJob（finch ideas）
  idea-to-draft/           已确认观点 → Draft + CriticReport（finch drafts create）
  voice-profile/           个人表达画像，只从用户认可样本更新（finch voice）
  weekly-reflection/       关系/观点/表达复盘（finch weekly；含消息摘录与修订差异）
  collision-lab/           跨领域结构碰撞 → CollisionCard（finch collisions generate / weekly）
  micro-experiment/        CollisionCard → 一周内小实验（finch experiments start）
  # —— 独立训练工具（不进入默认流水线）——
  community-scout/         围绕问题发现并持续参与社区：三入口 + 观察/可参与分层 + 回访（finch community，验证中）
  expression-practice/     表达训练（finch practice）
  article_analysis/        文章表达 + 写作风格七维（finch article analyze；只读不写画像）
  feynman-practice/        费曼技巧（检查理解）
  sticky-message/          检查想法是否清晰易记
  topic-dialogue/          围绕话题讨论，形成或修正判断（不写关系记录）
  _shared/                 idea-contract / evidence-policy / author-position / expression-contract / publication-safety / agent-presentation / dialogue-policy
  # 交互行为：业务 Skill 完成任务后按 `skills/_shared/dialogue-policy.md` 主动延伸一个讨论点；
  topic-dialogue 是独立可唤起、且可从任务结果衔接的连续讨论入口（不进入默认流水线）。

src/finch/
  peers/          PeerProfile / Person / CreatorEvidence（platform + author_id 幂等归一化）
  conversations/  ConversationThread / Commitment / 事件跟进
  connections/    真实互动登记与机会跟进（record / follow-up）
  dialogue/       DialogueNote 讨论摘要（与 ConversationThread / ContentJob 分开；finch dialogue）
  inspirations/   轻量灵感笔记（观察 / 交流 / 实践；finch inspirations）
  collisions/     CollisionCard + MicroExperiment
  ideas/          IdeaService（ContentJob + position_revisions）、CommitService、FragmentService
  drafts/         DraftService（已确认观点 → Draft + CriticReport，幂等，不自动发布）
  practice/       PracticeService（expression-practice 会话）
 profile/        PracticeProfile（practice-profile.yaml；只有 confirmed 条目进入 prompt；finch profile）
  idea/           finch drafts 复用的纯函数：rewrite_idea / idea_checker_suite
  content/        ContentJob + AuthorPosition（作者立场状态机）、writer、critic 检查器、voice profile
  communities/    CommunityProfile / CommunityFeedback（community-scout 的薄持久化，无评分）
  article/        article_analysis：ArticleReport/StyleBlock + SourceResolver + ArticleAnalysisService
  webfetch/       通用网页正文提取器（只读 adapter，fail-closed）
  inbox/          连接 + 表达循环的只读统一投影与决策（InboxDecisionService，供 review 命令）
  learn/          Feedback 模型 + weekly 指标 + WeeklyReflectionService 定性复盘 + finch learn
  evidence/       Commit → EngineeringEvent → EvidenceCard 提取 + 安全扫描（scan_cards）
  github/         gh adapter (read-only): commit/PR/issue reading, repo discovery
  twitter/        opencli adapter (read-only): search/thread/bookmarks
  reddit/         opencli adapter (read-only)
  opportunities/  交流机会聚合（why_me/why_continue/proposal + 生命周期 + prepare）
  discovery/      每日发现（sources sync → people shortlist → 首选机会评估）
  sources/        跨平台抓取编排（opencli gateway + RawArtifact；twitter/reddit/github/v2ex/weixin/xiaohongshu）
  repos/          X 分享的 GitHub 仓库发现与热度榜（finch repos；与 repository_discovery 无关）
  engagement/     发现快照 / InteractionRecord / 推荐反馈（旧评分管线已移除）
  storage/        file workspace: Workspace + repositories (YAML/Markdown/JSONL, atomic write)
  codex/          `codex exec` 子进程（超时 + Pydantic JSON）
  llm/            OpenAI-compatible 节点（critique 等）
  settings.py     finch.yaml + env loading (Pydantic)
  projections.py  今日焦点只读投影（对话跟进 + 观点候选）
  cli.py          typer app
```

Config lives in `finch.yaml` (repositories, repository_discovery, repo_discovery, opencli, sources, interests, engagement, discovery, llm, community_scout; settings also load twitter, quality_gates, paths, extraction). Prompts live in `prompts/`.

## Opportunity discovery (peer-discovery)

Daily discovery lives in `discovery/daily.py` + `opportunities/`：sources sync → CreatorEvidence → people shortlist（50 人分层：5 重点 / 15 摘要 / 30 浏览）→ 预算内顺序评估 priority 候选（`opportunity_assess_limit`，默认 5）→ 首选机会 0–1 条写入 `DiscoverySnapshot`。`finch connect daily` 首页只呈现这条首选（可为空）；`--view browse` 才展开 50 人列表。承诺面独立：`connections today` / `people shortlist --today`，不在首页再造人物池。`connect prepare --opportunity` 对已选机会制作可审阅贡献（Artifact；单次不超过 `deep_prepare_limit`，默认 5）；`--reaction "<原话>"` 记录用户对这条机会的反应（`Opportunity.reactions`，append-only），无反应时形式强制 `clarifying_question`；`connections record` 登记真实互动事实；`connections follow-up` 接续对方回应。旧 engagement 评分/提案管线已删除。

Key files: `opportunities/models.py`（Opportunity + Artifact）→ `assess.py` / `discover.py` → `prepare.py` → `service.py`（状态机）→ `repository.py`（快照 + events.jsonl + skip 缓存）。

## Invariants (do not violate)

- **Evidence first** — never generate a post directly from a commit; always Commit → EngineeringEvent → EvidenceCard → Draft.
- **No auto-publish** — `gh` and `opencli` adapters are read-only (opencli has a write-command denylist). Public replies/quotes require human approval; `guard.evaluate_execution` returns `rejected`/`unknown` (never success) unless approved and verified.
- **External ≠ evidence** — searched posts (`ExternalPost`) can never become personal evidence; only verified `ConversationEvidence` may promote, via `promote_to_personal`.
- **Confirmed practices only** — first-person experience in contributions may only cite `confirmed: true` items from `practice-profile.yaml`, by `[id]`, within their `boundaries`.
- **Deterministic totals** — weighted/summary scores are computed in code; LLM output never carries a `total`.
- **Subprocess discipline** — args as arrays (no shell string concat), per-call timeouts, JSON output validated through Pydantic.

## Conventions

- Python 3.12+; Pydantic 2 models (`StrEnum`/`Literal`/`Field`); domain models serialize to YAML/Markdown/JSONL files keyed by deterministic IDs, written via `Workspace.atomic_write` (idempotent overwrite).
- Domain services are deterministic and single-threaded — state transitions, retries, and fault isolation (try/except) live in Python; no `asyncio.gather`. Bounded `ThreadPoolExecutor` parallelism is allowed _inside_ a service step for independent I/O-bound subprocess calls (codex/git/opencli), always via `pool.map` so result order matches serial exactly.
- Bilingual (Chinese/English) docstrings are common; match the surrounding file.
- Ruff selects `E,F,I,B,UP`; line-length 100. Persistence is the file workspace (`Workspace.atomic_write`); there is no database or Alembic.
