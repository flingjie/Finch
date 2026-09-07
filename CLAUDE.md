# CLAUDE.md

Guidance for Claude Code (claude.ai/code) working in this repository.

## Project

Finch is an evidence-driven builder companion. It reads GitHub evidence via `gh` and Twitter/X via `opencli`, extracts engineering events into evidence cards, matches them to public technical discussion, and produces human-reviewed replies and original content. It also runs an independent engagement track that searches by interest and proposes bounded interactions (bookmark / observe / reply / quote), all gated behind human approval.

## Commands

```bash
uv sync                 # install dependencies (Python 3.12+)
uv run pytest           # full test suite
uv run ruff check .     # lint + format checks (line-length 100, py312)
uv run mypy src         # type-check
uv run finch <command>  # CLI entry point (typer)
```

Run a single test file/pattern with `uv run pytest tests/unit/test_foo.py -k name`.

CLI surface (typer sub-apps / commands): `finch ideas ...` (commit / search / list / show / confirm / revise-position / skip), `finch drafts ...` (create / show / revise), `finch review ...` (list / show / approve / revise / skip), `finch engagement ...` (list / show / approve / reject / edit / metrics), `finch weekly`, `finch learn <draft_id> ...` (记录发布反馈), `finch voice ...` (show / approve-example / reject-example), `finch github reflect`, `finch twitter ...` (search / import-bookmarks / diagnose), `finch init [--prune]`, `finch diagnose`.

## Architecture

Skill + domain services, not an LLM agent loop and not a graph runtime. Three skills drive the pipeline; ordering, state, retries, and idempotency live in deterministic Python domain services. Codex (`codex exec`) is called as a subprocess only at specific "smart" steps (assess / write / critic).

```
skills/
  commit-to-idea/    Commit/PR/Issue → IdeaCandidate → finch ideas commit
  search-to-idea/    公开讨论帖子 → IdeaCandidate → finch ideas search
  idea-to-draft/     已确认 idea → Draft + CriticReport → finch drafts create
  _shared/           idea-contract / evidence-policy / author-position / quality-policy / voice-guide

src/finch/
  ideas/        IdeaService（ContentJob 状态机：PROPOSED→CONFIRMED→DRAFTED，或→SKIPPED）、
                CommitService（commit→IdeaCandidate）、SearchService（帖子→IdeaCandidate）
  drafts/       DraftService（已确认 idea → Draft + CriticReport，幂等，不自动发布）
  idea/         finch drafts 复用的纯函数：rewrite_idea / idea_checker_suite（去掉 EvidenceChecker）
  content/      ContentJob、writer、critic 检查器、voice profile
  inbox/        原创 + 互动轨道的投影与决策（InboxDecisionService，供 review 命令）
  learn/        Feedback 模型 + weekly 周复盘 + finch learn（记录发布反馈）
  evidence/     Commit → EngineeringEvent → EvidenceCard 提取 + 安全扫描（scan_cards）
  github/       gh adapter (read-only): commit/PR/issue reading, repo discovery
  twitter/      opencli adapter (read-only): search/thread/bookmarks
  engagement/   engagement 轨道库（models 供 inbox 投影；search/scoring/proposals/guard/evidence_upgrade/metrics）
  storage/      SQLite via SQLModel: Store + repositories (payload_json pattern)
  settings.py   finch.yaml + env loading (Pydantic)
  cli.py        typer app (ideas/drafts/review/engagement/weekly/voice/github/twitter/init/diagnose)
```

Config lives in `finch.yaml` (repositories, repository_discovery, twitter, quality_gates, paths, engagement, interests, llm, extraction). Prompts live in `prompts/`.

## Engagement track (library, not daily-orchestrated)

The engagement module (`engagement/`) still exists as a library: search → prefilter → deterministic 5-dim scoring → ranked proposals → guarded execution → feedback → conversation evidence → verified upgrade to personal evidence. Its `InteractionCandidate`/`ConversationEvidence` models feed the inbox (`finch review` / `finch engagement`). The previous daily dual-track orchestration (`run_daily` / `run_dual_track`) has been removed.

Pipeline files: `models.py` (domain types) → `search.py` (PostSearchProvider: X + Reddit stub) → `scoring.py` (weighted_total is the *only* place `total` is computed; the LLM never decides it) → `proposals.py` (choose_action + bounded drafts) → `guard.py` (execution precondition check) → `evidence_upgrade.py` (conversation→personal gate) → `metrics.py` (quality-first metrics).

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
