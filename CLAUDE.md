# CLAUDE.md

Guidance for Claude Code (claude.ai/code) working in this repository.

## Project

Finch is a peer-connection and personal-expression system, not a content-generation tool. It discovers peers and real tool users worth long-term conversation via `gh` (GitHub evidence) and `opencli` (Twitter/X), understands the situations they're in, prepares valuable interactions, and threads those into ongoing relationship context — then forms the user's own viewpoints from practice and conversation and writes content that sounds like them. Problem/workaround/usage feedback lives as conversation-thread notes, not a separate problem or payments domain. Content production is the _result_ of connection, not the goal. The north-star metric is how many "contextual, continuable" peer relationships are added or deepened each week. Canonical definition: `docs/product-contract.md`. Finch is fully standalone — no builderDNA dependency.

## Commands

```bash
uv sync                 # install dependencies (Python 3.12+)
uv run pytest           # full test suite
uv run ruff check .     # lint + format checks (line-length 100, py312)
uv run mypy src         # type-check
uv run finch <command>  # CLI entry point (typer)
```

Run a single test file/pattern with `uv run pytest tests/unit/test_foo.py -k name`.

CLI surface (typer sub-apps / commands): `finch connect ...` (refresh / today / daily / more / expand / prepare / feedback / approve / reject / edit / record / record-presented / create / with), `finch peers ...` (list / show / get), `finch conversations ...` (list / show / get / follow-up / ingest / defer / close), `finch dialogue ...` (save / search / show / forget), `finch ideas ...` (commit / create / list / show / confirm / revise-position / skip), `finch inspirations ...` (save / list / show / note / archive), `finch community ...` (discover / save / inspect / feedback / list), `finch practice ...` (start / diagnose / save / finish / show), `finch style ...` (analyze), `finch drafts ...` (create / show / revise), `finch review ...` (list / show / approve / revise / skip), `finch weekly`, `finch learn <draft_id> ...` (记录发布反馈), `finch voice ...` (show / approve-example / reject-example / revoke-example / propose), `finch github reflect`, `finch twitter ...` (search / import-bookmarks / diagnose), `finch init`, `finch diagnose`, `finch context` (daily/pending projections).

## Architecture

Skill + domain services, not an LLM agent loop and not a graph runtime. Seven core-loop skills cover the connection + expression tasks (peer discovery / interaction preparation / conversation follow-up / idea discovery / drafting / voice / weekly reflection), plus five independent training tools (expression practice / writing-style analysis / feynman / sticky-message / topic-dialogue) that never enter the default pipeline. Ordering, state, retries, and idempotency live in deterministic Python domain services. Codex (`codex exec`) is called as a subprocess only at specific "smart" steps (assess / write / critic).

```
skills/
  peer-discovery/         公开内容 → 首页 3 重点 + 50 人浏览（同一快照投影）+ PeerProfile（finch connect daily）
  interaction-preparation/ 选中后深度准备（finch connect prepare --opportunity，默认 ≤3）
  conversation-follow-up/  按真实触发恢复对话（finch conversations follow-up / ingest）
  idea-discovery/         Commit/PR/测试 + 用户片段 + 已验证对话 → ContentJob（finch ideas）
  idea-to-draft/          已确认观点 → Draft + CriticReport（finch drafts create）
  voice-profile/          个人表达画像，只从用户认可样本更新（finch voice）
  weekly-reflection/      关系/观点/表达复盘（finch weekly；含消息摘录与修订差异）
  # —— 独立训练工具（不进入默认流水线）——
  community-scout/        每周 3 个可进入的社区，社区行动卡（finch community，验证中）
  expression-practice/    表达训练（finch practice）
  writing-style-analysis/ 分析他人写作风格，只读不写画像（finch style analyze）
  feynman-practice/       费曼技巧（检查理解）
  sticky-message/         检查想法是否清晰易记
  topic-dialogue/         围绕话题讨论，形成或修正判断（不写关系记录）
  _shared/                idea-contract / evidence-policy / author-position / expression-contract / publication-safety
  # 交互行为：业务 Skill 完成任务后按 `skills/_shared/dialogue-policy.md` 主动延伸一个讨论点；
  topic-dialogue 是独立可唤起、且可从任务结果衔接的连续讨论入口（不进入默认流水线）。

src/finch/
  peers/         PeerProfile 关系领域（platform + author_id 幂等归一化）
  conversations/ ConversationThread / Commitment / 事件跟进
  dialogue/      DialogueNote 讨论摘要（与 ConversationThread / ContentJob 分开；finch dialogue）
  ideas/         IdeaService（ContentJob + position_revisions）、CommitService、FragmentService
  drafts/        DraftService（已确认观点 → Draft + CriticReport，幂等，不自动发布）
  practice/      PracticeService（expression-practice 会话）
  idea/          finch drafts 复用的纯函数：rewrite_idea / idea_checker_suite
  content/       ContentJob + AuthorPosition（作者立场状态机）、writer、critic 检查器、voice profile
  communities/   CommunityProfile / CommunityFeedback（community-scout 的薄持久化，无评分）
  style/         writing-style-analysis：StyleReport/StyleComparison + SourceResolver
  webfetch/      通用网页正文提取器（只读 adapter，fail-closed）
  inbox/         连接 + 表达循环的只读统一投影与决策（InboxDecisionService，供 review 命令）
  learn/         Feedback 模型 + weekly 指标 + WeeklyReflectionService 定性复盘 + finch learn
  evidence/      Commit → EngineeringEvent → EvidenceCard 提取 + 安全扫描（scan_cards）
  github/        gh adapter (read-only): commit/PR/issue reading, repo discovery
  twitter/       opencli adapter (read-only): search/thread/bookmarks
  engagement/    Opportunity 发现 / scoring / proposals / guard / evidence_upgrade / metrics
  storage/       file workspace: Workspace + repositories (YAML/Markdown/JSONL, atomic write)
  settings.py    finch.yaml + env loading (Pydantic)
  cli.py         typer app (connect/peers/conversations/dialogue/ideas/practice/style/drafts/review/weekly/voice/github/twitter/init/diagnose)
```

Config lives in `finch.yaml` (repositories, repository_discovery, twitter, quality_gates, paths, engagement, interests, llm, extraction). Prompts live in `prompts/`.

## Engagement track (peer-discovery library)

The engagement module (`engagement/`) is the peer-discovery library: search → prefilter → peer aggregation → coarse relationship ranking → semantic opportunity assessment (LLM dims, no `total`) → `select_opportunity_set` → snapshot. Drafts via `generate_proposals` run only on `connect prepare` for selected opportunities. `InteractionProposal` / `ConversationEvidence` feed the inbox (`finch review`); opportunities and relationships are surfaced via `finch connect` / `finch peers` / `finch conversations`.

Pipeline files: `models.py` (Opportunity + proposals) → `search.py` → `peer_aggregation.py` → `relationship.py` → `scoring.py` (`weighted_total` only) → `opportunity.py` (selection) → `proposals.py` (prepare path) → `guard.py` → `evidence_upgrade.py` → `metrics.py`.

## Invariants (do not violate)

- **Evidence first** — never generate a post directly from a commit; always Commit → EngineeringEvent → EvidenceCard → Draft.
- **No auto-publish** — `gh` and `opencli` adapters are read-only (opencli has a write-command denylist). Public replies/quotes require human approval; `guard.evaluate_execution` returns `rejected`/`unknown` (never success) unless approved and verified.
- **External ≠ evidence** — searched posts (`ExternalPost`) can never become personal evidence; only verified `ConversationEvidence` may promote, via `promote_to_personal`.
- **Deterministic totals** — weighted/summary scores are computed in code; LLM output never carries a `total`.
- **Subprocess discipline** — args as arrays (no shell string concat), per-call timeouts, JSON output validated through Pydantic.

## Conventions

- Python 3.12+; Pydantic 2 models (`StrEnum`/`Literal`/`Field`); domain models serialize to YAML/Markdown/JSONL files keyed by deterministic IDs, written via `Workspace.atomic_write` (idempotent overwrite).
- Domain services are deterministic and single-threaded — state transitions, retries, and fault isolation (try/except) live in Python; no `asyncio.gather`. Bounded `ThreadPoolExecutor` parallelism is allowed _inside_ a service step for independent I/O-bound subprocess calls (codex/git/opencli), always via `pool.map` so result order matches serial exactly.
- Bilingual (Chinese/English) docstrings are common; match the surrounding file.
- Ruff selects `E,F,I,B,UP`; alembic migration scripts are excluded from linting.
