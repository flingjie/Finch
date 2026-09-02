# Phase 3: Twitter Scout Implementation Plan

## Goal
Implement the `twitter/` package: `OpenCliTwitterClient`, query builder, normalizer,
deduplication, command allowlist/denylist, and CLI commands. Add contract tests with
real fixtures and unit tests for all components.

## Territory Summary

- Phase 1 (Skeleton + Runtime) and Phase 2 (GitHub Commit Intelligence) are complete.
- `src/finch/twitter/` has four placeholder modules:
  - `models.py` — empty placeholder
  - `opencli_client.py` — only `version()` and `doctor()`
  - `query_builder.py` — empty placeholder
  - `normalizer.py` — empty placeholder
- `_run()` helper exists in `gh_client.py` and is already imported by `opencli_client.py`.
- Real `opencli twitter search` JSON schema captured:
  - Fields: `id`, `author`, `bio`, `text`, `created_at`, `likes`, `views`, `url`,
    `has_media`, `media_urls`, `media_posters`, `card`, `quoted_tweet`
  - `quoted_tweet` is nested with same fields (minus `quoted_tweet`)
  - `views` is a string (e.g. "2038"), `likes` is int
  - `created_at` format: "Wed Sep 02 06:05:25 +0000 2026"
- `finch.yaml` already has `twitter:` section with `daily_limit`, `per_query_limit`, `queries`.
- `settings.py` has `TwitterSettings` model.

## Phase 3A: Data Models + Normalizer

### Files to modify/create

1. **`src/finch/twitter/models.py`** — Create Twitter data models
   - `Tweet` — Pydantic model with all fields from real JSON schema
   - `QuotedTweet` — nested model for quoted_tweet
   - `DiscussionCandidate` — wrapper with metadata (query_id, captured_at, metrics)
   - `TwitterError` — exception for Twitter errors
   - Handle `created_at` parsing from Twitter's format
   - `views` → int coercion (comes as string from API)

2. **`src/finch/twitter/normalizer.py`** — Normalize and deduplicate tweets
   - `normalize_url(url: str) -> str` — canonicalize x.com/i/status/ID → x.com/handle/status/ID
   - `deduplicate(tweets: list[Tweet]) -> list[Tweet]` — by ID or normalized URL
   - `filter_noise(tweets: list[Tweet]) -> list[Tweet]` — empty text, ads, blocked authors

3. **Tests:**
   - `tests/unit/test_twitter_models.py` — model validation, edge cases
   - `tests/unit/test_normalizer.py` — URL normalization, dedup, noise filtering
   - `tests/contract/test_opencli_models.py` — real fixture validates against model

## Phase 3B: OpenCliTwitterClient + Query Builder

### Files to modify/create

4. **`src/finch/twitter/query_builder.py`** — Build and validate queries
   - `QueryConfig` — Pydantic model: id, text, filter (top/live), priority
   - `QueryBuilder` — load from `finch.yaml`, validate, generate `opencli` argv
   - Enforce `--format json` on all queries
   - Track query version for reproducibility

5. **`src/finch/twitter/opencli_client.py`** — Full read-only client
   - `ALLOWLIST` — set of allowed command prefixes: `twitter search`, `twitter thread`, `twitter bookmarks`, `twitter timeline`, `twitter profile`
   - `DENYLIST` — set of blocked commands: `twitter post`, `twitter reply`, `twitter quote`, `twitter like`, `twitter retweet`, `twitter follow`, `twitter unfollow`, `twitter delete`, `browser click`, `browser type`, `browser eval`
   - `search(query: str, product: str = "top", limit: int = 20) -> list[Tweet]`
   - `thread(url: str) -> list[Tweet]`
   - `bookmarks(limit: int = 50) -> list[Tweet]`
   - `timeline(limit: int = 50) -> list[Tweet]`
   - `profile(handle: str) -> Tweet | None`
   - `_call(args: list[str]) -> list[dict]` — private, enforces allowlist, parses JSON, handles errors
   - Error handling:
     - `TWITTER_SOURCE_UNAVAILABLE` — Bridge offline / not logged in
     - `RATE_LIMITED` — rate limit hit
     - `COMMAND_BLOCKED` — denylist hit (should never happen, but defense in depth)
   - `_run()` from `gh_client.py` is reused for subprocess execution

6. **Update `finch.yaml`** — Add default queries matching spec §8:
   ```yaml
   queries:
     - id: agent_harness_live
       text: '"agent harness" OR "agent loop"'
       filter: live
       priority: 5
     - id: agent_eval_live
       text: '"agent evals" OR "trajectory evaluation"'
       filter: live
       priority: 5
   ```

7. **Tests:**
   - `tests/unit/test_opencli_client.py` — allowlist enforcement, denylist blocks, error handling, JSON parsing
   - `tests/unit/test_query_builder.py` — query generation, format enforcement
   - `tests/contract/test_opencli_models.py` — real fixture still validates

## Phase 3C: CLI Commands

### Files to modify

8. **`src/finch/cli.py`** — Add twitter CLI subcommands
   - `finch twitter search --query-set <id>` — run a configured query set
   - `finch twitter import-bookmarks --limit 50` — import bookmarks
   - `finch twitter diagnose` — check opencli twitter health
   - Output: count + preview of tweets found

9. **Tests:**
   - `tests/unit/test_cli_twitter.py` — CLI invocation tests

## Phase 3D: Integration + Cleanup

10. **Run full test suite:** `uv run pytest`
11. **Run ruff + mypy:** `uv run ruff check src tests && uv run mypy src`
12. **Save contract fixture:** `tests/fixtures/opencli/twitter-search.json`

## Acceptance Criteria

- [ ] `OpenCliTwitterClient.search()` returns `list[Tweet]` from real `opencli` JSON
- [ ] Any write command in `DENYLIST` raises `TwitterError` with `COMMAND_BLOCKED`
- [ ] Missing `created_at` → `published_at=None` (not fabricated)
- [ ] Duplicate tweets by ID are deduplicated
- [ ] Empty text / noise tweets are filtered
- [ ] Contract test validates real fixture against model
- [ ] `finch twitter search` CLI command works
- [ ] All tests pass: `uv run pytest`
- [ ] ruff + mypy clean

## Files Changed Summary

| File | Action |
|---|---|
| `src/finch/twitter/models.py` | Create |
| `src/finch/twitter/normalizer.py` | Create |
| `src/finch/twitter/query_builder.py` | Create |
| `src/finch/twitter/opencli_client.py` | Rewrite |
| `src/finch/cli.py` | Add twitter commands |
| `finch.yaml` | Add default queries |
| `tests/unit/test_twitter_models.py` | Create |
| `tests/unit/test_normalizer.py` | Create |
| `tests/unit/test_opencli_client.py` | Create |
| `tests/unit/test_query_builder.py` | Create |
| `tests/unit/test_cli_twitter.py` | Create |
| `tests/contract/test_opencli_models.py` | Create |
| `tests/fixtures/opencli/twitter-search.json` | Save |
