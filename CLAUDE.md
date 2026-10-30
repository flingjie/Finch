# CLAUDE.md

Guidance for Claude Code (claude.ai/code) working in this repository.

## Project

Finch is an AI product discovery + founder connection copilot. It finds AI products and founders worth watching, explains why they are worth learning from or talking to, breaks down the solution (who it solves what problem for, the prior approach, the new solution, evidence vs. assumption), and threads real interactions into relationship context. Products are the main object of study; founders are the main object of connection. Problems are a first-class object of study alongside products, linked by evidence or manual confirmation. Content production is not the purpose and does not serve connection.

Shipped scope: product discovery + solution explorer + problem inbox (`problem-discovery`) + relationships briefing. Product↔Problem relation layer, Problem Radar, and Investigate are later rounds, as are deeper founder connection, product comparison, and insight accumulation. Canonical definition: `docs/product-contract.md`. Finch is fully standalone — no builderDNA dependency.

## Commands

```bash
uv sync                 # install dependencies (Python 3.12+)
uv run pytest           # full test suite
uv run ruff check .     # lint + format checks (line-length 100, py312)
uv run mypy src         # type-check
uv run finch <command>  # CLI entry point (typer)
```

Run a single test file/pattern with `uv run pytest tests/unit/test_foo.py -k name`.

CLI surface (core): `finch products daily` (`--refresh` runs discovery, else replays latest snapshot; `--view home|browse`; `--json`; `--lookback-hours`; `--question`), `finch products list`, `finch products show <id>`, `finch products feedback <id>` (`--refresh` re-searches user feedback, else replays cache), `finch products explore <id>` (`--refresh` forces re-analysis; auto-runs feedback research when absent/stale; `--skip-feedback` to analyze from existing material only).

Problem discovery: `finch problem-discovery add|discover|list|show|summary|present|last-presented`.

Relationships: `finch relationships import|add-person|add-event|record-contact|next|weekly`.

Active read-only infra: `finch init`, `finch diagnose`, `finch twitter ...`, `finch sources ...` (doctor/sync), `finch repos ...` (discover/list/export).

Frozen old-mission commands (hidden from `finch --help`, code+tests kept, data kept): connect, peers, people, connections, conversations, collisions, experiments, inspirations, ideas, drafts, review, voice, profile, practice, article, angles, methods, community, dialogue, summaries, materials, problems, attempts, grounded, knowledge, github reflect, learn, weekly, summarize, context.

## Architecture

Skill + domain services, not an LLM agent loop and not a graph runtime. Ordering, state, retries, and idempotency live in deterministic Python domain services. Codex (`codex exec`) is called as a subprocess only at specific "smart" steps (product cards / solution analysis).

```
src/finch/
  products/        Product / Founder / Solution / Feedback models + product discovery + feedback research + solution explorer
  problem_discovery/  real user problem extraction + problem inbox
  relationships/      person task understanding + weekly briefing
  sources/         cross-platform read-only fetch (opencli gateway + RawArtifact)
  repos/           X-shared GitHub repo discovery (product discovery input)
  github/ twitter/ reddit/ hackernews/ webfetch/ ingest/  read-only adapters
  storage/         file workspace: Workspace + repositories (YAML/Markdown/JSONL, atomic write)
  codex/           `codex exec` subprocess (timeout + Pydantic JSON)
  llm/             OpenAI-compatible nodes
  settings.py      finch.yaml + env loading (Pydantic)
  # —— frozen (old mission; code + tests kept, hidden from default CLI) ——
  peers/ opportunities/ connections/ conversations/ ideas/ drafts/ content/ practice/
  article/ angle_discovery/ content_summary/ expression_methods/ communities/ grounded/
  problems/ attempts/ knowledge/ materials/ learn/ inbox/ evidence/ collisions/ inspirations/
```

Product discovery pipeline (`products/discovery.py`): `build_discovery_plan`/`build_context_by_source` (with `settings.products.sources`) → `DiscoveryOrchestrator.sync_all(project_peers=False)` → `filter_artifacts` → `ProductProjector.build_product_candidates` (cluster by product key) → optional `products_from_latest_repos_run` → `score_product` (deterministic) → `select_daily_products` (cooldown + top-N) → `ProductCardService.fill` (LLM batch) → `ProductSnapshot`.

Product feedback research (`products/feedback.py`): select a product → targeted search (Reddit/X via `DiscoveryOrchestrator.sync_source`; GitHub via `GitHubConnector.fetch_repo_feedback` for issues/PRs/discussions) → `filter_feedback_candidates` (attribution / drop founder+promo / dedupe) → `assess_feedback` (LLM) → `build_feedback_report` (deterministic gates: excerpt must trace to a candidate, feedback refs subset, empty refs forced ASSUMPTION, no feedback → `insufficient_feedback`) → idempotent report cached by `search_fingerprint` + TTL.

Solution explorer (`products/explorer.py`): select a product → `assess_solution` (LLM) → `build_solution` (deterministic gate: 6 narrative fields required; evidence refs subset of product source_refs, feedback refs subset of report `feedback_id`s, empty refs forced ASSUMPTION; verdict coerced) → idempotent upsert keyed by `solution_id` including `feedback_report_id` (feedback change invalidates).

## Invariants (do not violate)

- **Evidence first** — product cards and solutions cite source artifacts; no fabricated evidence.
- **Evidence vs. assumption** — `EvidenceStatus` is explicit / inferred / assumption; claims without source refs must be `assumption`.
- **No auto-publish** — `gh` and `opencli` adapters are read-only.
- **Deterministic totals** — `score_total` / `score_dimensions` are computed in code; LLM output never carries a `total`.
- **Subprocess discipline** — args as arrays (no shell string concat), per-call timeouts, JSON output validated through Pydantic.
- **Frozen ≠ deleted** — old-mission domain services stay frozen (hidden from CLI, code+tests intact); do not delete until the new direction is validated.

## Conventions

- Python 3.12+; Pydantic 2 models (`StrEnum`/`Literal`/`Field`); domain models serialize to YAML/Markdown/JSONL files keyed by deterministic IDs, written via `Workspace.atomic_write` (idempotent overwrite).
- Domain services are deterministic and single-threaded — state transitions, retries, and fault isolation (try/except) live in Python; no `asyncio.gather`. Bounded `ThreadPoolExecutor` parallelism is allowed _inside_ a service step for independent I/O-bound subprocess calls, always via `pool.map` so result order matches serial exactly.
- Bilingual (Chinese/English) docstrings are common; match the surrounding file.
- Ruff selects `E,F,I,B,UP`; line-length 100. Persistence is the file workspace (`Workspace.atomic_write`); there is no database or Alembic.

Config lives in `finch.yaml` (gitignored): `products` (sources / daily_limit / min_candidates / max_card_candidates / card_batch_size / cooldown_days / repos_as_input / solution / feedback), `problem_discovery` (version / lookback / budgets / sources), `relationships`, plus frozen `sources`, `repo_discovery`, `opencli`, `interests`, `llm`. `finch.example.yaml` is the committed template.
