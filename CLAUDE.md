# CLAUDE.md

Guidance for Claude Code (claude.ai/code) working in this repository.

## Project

Finch is a peer-connection and personal-expression system, not a content-generation tool. It discovers peers worth long-term conversation via `gh` (GitHub evidence) and `opencli` (Twitter/X), understands the problems they're solving, prepares valuable interactions, and threads those into ongoing relationship context — then forms the user's own viewpoints from practice and conversation and writes content that sounds like them. Content production is the *result* of connection, not the goal. The north-star metric is how many "contextual, continuable" peer relationships are added or deepened each week. Canonical definition: `docs/product-contract.md`. Finch is fully standalone — no builderDNA dependency.

## Commands

```bash
uv sync                 # install dependencies (Python 3.12+)
uv run pytest           # full test suite
uv run ruff check .     # lint + format checks (line-length 100, py312)
uv run mypy src         # type-check
uv run finch <command>  # CLI entry point (typer)
```

Run a single test file/pattern with `uv run pytest tests/unit/test_foo.py -k name`.

CLI surface (typer sub-apps / commands): `finch connect ...` (daily / prepare / approve / reject / edit / record), `finch peers ...` (list / show), `finch conversations ...` (list / show / follow-up), `finch ideas ...` (commit / create / list / show / confirm / revise-position / skip), `finch practice ...` (start / diagnose / save / finish / show), `finch style ...` (analyze), `finch drafts ...` (create / show / revise), `finch review ...` (list / show / approve / revise / skip), `finch weekly`, `finch learn <draft_id> ...` (记录发布反馈), `finch voice ...` (show / approve-example / reject-example / revoke-example / propose), `finch github reflect`, `finch twitter ...` (search / import-bookmarks / diagnose), `finch init [--prune]`, `finch diagnose`.

## Architecture

Skill + domain services, not an LLM agent loop and not a graph runtime. Seven core-loop skills cover the connection + expression tasks (peer discovery / interaction preparation / conversation follow-up / idea discovery / drafting / voice / weekly reflection), plus four independent training tools (expression practice / writing-style analysis / feynman / sticky-message) that never enter the default pipeline. Ordering, state, retries, and idempotency live in deterministic Python domain services. Codex (`codex exec`) is called as a subprocess only at specific "smart" steps (assess / write / critic).

```
skills/
  peer-discovery/         公开内容 → PeerProfile 候选（finch connect daily / peers）
  interaction-preparation/ 同行 + 帖子 + 用户证据 → 互动建议（finch connect prepare）
  conversation-follow-up/  恢复对话上下文 → 下一步（finch conversations follow-up）
  idea-discovery/         Commit/PR/测试 + 用户片段 + 已验证对话 → AuthorIdea（finch ideas）
  idea-to-draft/          已确认观点 → Draft + CriticReport（finch drafts create）
  voice-profile/          个人表达画像，只从用户认可样本更新（finch voice）
  weekly-reflection/      关系质量/观点形成/表达反馈复盘（finch weekly）
  # —— 独立训练工具（不进入默认流水线）——
  expression-practice/    表达训练（finch practice）
  writing-style-analysis/ 分析他人写作风格，只读不写画像（finch style analyze）
  feynman-practice/       费曼技巧（检查理解）
  sticky-message/         检查想法是否清晰易记
  _shared/                idea-contract / evidence-policy / author-position / expression-contract / publication-safety

src/finch/
  peers/         PeerProfile 关系领域（platform + author_id 幂等归一化）
  conversations/ ConversationThread / InteractionRecord 关系领域
  ideas/         IdeaService（AuthorIdea 状态机：PROPOSED→CONFIRMED→DRAFTED，或→SKIPPED）、
                 CommitService、FragmentService
  drafts/        DraftService（已确认观点 → Draft + CriticReport，幂等，不自动发布）
  practice/      PracticeService（expression-practice 会话）
  idea/          finch drafts 复用的纯函数：rewrite_idea / idea_checker_suite
  content/       ContentJob（AuthorIdea 内部状态实现）、writer、critic 检查器、voice profile
  style/         writing-style-analysis：StyleReport/StyleComparison + SourceResolver
  webfetch/      通用网页正文提取器（只读 adapter，fail-closed）
  inbox/         连接 + 表达循环的只读统一投影与决策（InboxDecisionService，供 review 命令）
  learn/         Feedback 模型 + weekly 指标 + WeeklyReflectionService 定性复盘 + finch learn
  evidence/      Commit → EngineeringEvent → EvidenceCard 提取 + 安全扫描（scan_cards）
  github/        gh adapter (read-only): commit/PR/issue reading, repo discovery
  twitter/       opencli adapter (read-only): search/thread/bookmarks
  engagement/    peer-discovery 底层库：search/scoring/proposals/guard/evidence_upgrade/metrics
  storage/       SQLite via SQLModel: Store + repositories (payload_json pattern)
  settings.py    finch.yaml + env loading (Pydantic)
  cli.py         typer app (connect/peers/conversations/ideas/practice/style/drafts/review/weekly/voice/github/twitter/init/diagnose)
```

Config lives in `finch.yaml` (repositories, repository_discovery, twitter, quality_gates, paths, engagement, interests, llm, extraction). Prompts live in `prompts/`.

## Engagement track (peer-discovery library)

The engagement module (`engagement/`) is the peer-discovery library: search → prefilter → peer aggregation → deterministic relationship scoring (`peer_value` / `relationship_value`) → 4-dim semantic scoring (LLM) → ranked proposals → guarded execution → feedback → conversation evidence → verified upgrade to personal evidence. `InteractionProposal` / `ConversationEvidence` feed the inbox (`finch review`); proposals and relationships are surfaced via `finch connect` / `finch peers` / `finch conversations`. The previous daily dual-track orchestration (`run_daily` / `run_dual_track`) has been removed.

Pipeline files: `models.py` (domain types) → `search.py` (PostSearchProvider: X + Reddit) → `peer_aggregation.py` (aggregate posts by author) → `relationship.py` (deterministic peer_value + relationship_value) → `scoring.py` (weighted_total is the *only* place `total` is computed; the LLM never decides it) → `proposals.py` (choose_action + bounded drafts) → `guard.py` (execution precondition check) → `evidence_upgrade.py` (conversation→personal gate) → `metrics.py` (relationship-quality metrics).

## Invariants (do not violate)

- **Evidence first** — never generate a post directly from a commit; always Commit → EngineeringEvent → EvidenceCard → Draft.
- **No auto-publish** — `gh` and `opencli` adapters are read-only (opencli has a write-command denylist). Public replies/quotes require human approval; `guard.evaluate_execution` returns `rejected`/`unknown` (never success) unless approved and verified.
- **External ≠ evidence** — searched posts (`ExternalPost`) can never become personal evidence; only verified `ConversationEvidence` may promote, via `promote_to_personal`.
- **Deterministic totals** — weighted/summary scores are computed in code; LLM output never carries a `total`.
- **Subprocess discipline** — args as arrays (no shell string concat), per-call timeouts, JSON output validated through Pydantic.

## Conventions

- Python 3.12+; Pydantic 2 models (`StrEnum`/`Literal`/`Field`); SQLModel records store `payload_json` and upsert via `session.merge` (idempotent).
- Domain services are deterministic and single-threaded — state transitions, retries, and fault isolation (try/except) live in Python; no `asyncio.gather`. Bounded `ThreadPoolExecutor` parallelism is allowed *inside* a service step for independent I/O-bound subprocess calls (codex/git/opencli), always via `pool.map` so result order matches serial exactly.
- Bilingual (Chinese/English) docstrings are common; match the surrounding file.
- Ruff selects `E,F,I,B,UP`; alembic migration scripts are excluded from linting.
