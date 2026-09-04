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

## Architecture

Deterministic graph runtime, not an LLM agent loop. Codex (`codex exec`) is called as a subprocess only at specific "smart" nodes; ordering, state, retries, and idempotency live in Python.

```
src/finch/
  graph/        deterministic runtime: Node (reads/writes/succeeds_to contract),
                GraphRuntime (sequential, idempotent, replay), daily_nodes assembly,
                dual_track.py (original + engagement fan-out with fault isolation)
  evidence/     Commit -> EngineeringEvent -> EvidenceCard extraction/judging/scoring
  content/      ContentJobs, writer, critic checkers, voice profile
  review/       human review decisions + weekly aggregation
  github/       gh adapter (read-only): commit/PR/issue reading, repo discovery
  twitter/      opencli adapter (read-only): search/thread/bookmarks
  engagement/   NEW dual-track engagement (see below)
  storage/      SQLite via SQLModel: Store + repositories (payload_json pattern)
  settings.py   finch.yaml + env loading (Pydantic)
  cli.py        typer app (run/review/jobs/voice/github/twitter/engagement)
```

Config lives in `finch.yaml` (repositories, repository_discovery, twitter, quality_gates, engagement, interests). Prompts live in `prompts/`.

## Engagement track (dual-track, every run)

`run_daily` runs two independent tracks via `run_dual_track` (sequential, fault-isolated, shared `run_id`; one track failing never erases the other's results, and marks `partial_failure`):

- **Original track** — the existing evidence-gated graph (unchanged). No evidence → empty success, still runs.
- **Engagement track** (`engagement/`) — search → prefilter → deterministic 5-dim scoring → ranked proposals → human approval queue → guarded execution → feedback → conversation evidence → verified upgrade to personal evidence.

Pipeline files: `models.py` (domain types) → `search.py` (PostSearchProvider: X + Reddit stub) → `scoring.py` (weighted_total is the *only* place `total` is computed; the LLM never decides it) → `proposals.py` (choose_action + bounded drafts) → `guard.py` (execution precondition check) → `evidence_upgrade.py` (conversation→personal gate) → `metrics.py` (quality-first metrics).

## Invariants (do not violate)

- **Evidence first** — never generate a post directly from a commit; always Commit → EngineeringEvent → EvidenceCard → Draft.
- **No auto-publish** — `gh` and `opencli` adapters are read-only (opencli has a write-command denylist). Public replies/quotes require human approval; `guard.evaluate_execution` returns `rejected`/`unknown` (never success) unless approved and verified.
- **External ≠ evidence** — searched posts (`ExternalPost`) can never become personal evidence; only verified `ConversationEvidence` may promote, via `promote_to_personal`.
- **Deterministic totals** — weighted/summary scores are computed in code; LLM output never carries a `total`.
- **Subprocess discipline** — args as arrays (no shell string concat), per-call timeouts, JSON output validated through Pydantic.

## Conventions

- Python 3.12+; Pydantic 2 models (`StrEnum`/`Literal`/`Field`); SQLModel records store `payload_json` and upsert via `session.merge` (idempotent).
- The runtime is synchronous — do not introduce `asyncio`/threads; fault isolation uses try/except, not `asyncio.gather`.
- Bilingual (Chinese/English) docstrings are common; match the surrounding file.
- Ruff selects `E,F,I,B,UP`; alembic migration scripts are excluded from linting.
