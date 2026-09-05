# Daily Work Budget — Phase B (P1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Two Phase-B (P1) improvements on top of Phase A: (1) cross-repo parallel extraction with a global LLM-concurrency cap, and (2) sealed/append-only grouping so a new commit never invalidates an old group's cached extraction.

**Architecture:** `Extractor` gains a global `threading.Semaphore` acquired around every LLM call; `make_extract_node` extracts repos in parallel via `pool.map` (order-preserving). Grouping becomes append-only: a commit's `group_id` is decided once at ingest and persisted to the ledger, so a backlog group's membership is frozen and its (already-stable) fingerprint cache never invalidates.

**Tech Stack:** Python 3.12, Pydantic 2, SQLModel (SQLite), typer, `uv`.

## Global Constraints

- Python 3.12+; lint `uv run ruff check .` (E,F,I,B,UP; line-length 100), types `uv run mypy src`, tests `uv run pytest`.
- Deterministic totals invariant: scores computed in code; LLM output never carries a `total`.
- Subprocess discipline: args as arrays, per-call timeouts, JSON validated via Pydantic.
- SQLModel records store `payload_json`, upsert via `session.merge`.
- Graph runtime stays sequential/deterministic; bounded `ThreadPoolExecutor` only inside nodes via `pool.map` (order must match serial exactly).
- Evidence-first / no auto-publish invariants unchanged.

## Design deviation from spec (recorded for the user)

Spec §7 says "cache key changes from `group_fingerprint` to `group_id`". This plan keeps the **fingerprint** cache key. Rationale: once grouping is append-only, a group's commit set is frozen and its `CommitDetail` content is read from the ledger (never re-fetched), so `group_fingerprint` is already stable — the group_id-keyed cache would add robustness only against a rare DB-re-seed, at the cost of threading `group_id` through `Ingestor → run_daily → daily_nodes → make_extract_node → extract_grouped`. The durable `group_id` is still introduced (it is what makes grouping append-only), just not used as the cache key. This is a YAGNI deviation from one sentence of §7; the §7 *goal* (stable cache, "永久复用") is fully met.

---

### Task B1: `global_max_concurrency` config

**Files:**
- Modify: `src/finch/settings.py`
- Modify: `finch.example.yaml`
- Test: `tests/unit/test_settings.py`

**Interfaces:**
- Produces: `ExtractionSettings.global_max_concurrency: int = Field(default=4, ge=1)`.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_settings.py`:

```python
def test_extraction_global_concurrency_default():
    s = load_settings(Path("finch.example.yaml"))
    assert s.extraction.global_max_concurrency == 4
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_settings.py::test_extraction_global_concurrency_default -v`
Expected: FAIL — `AttributeError: 'ExtractionSettings' object has no attribute 'global_max_concurrency'`

- [ ] **Step 3: Write minimal implementation**

In `src/finch/settings.py`, add to `ExtractionSettings`:

```python
class ExtractionSettings(BaseModel):
    """commit 提取配置（批量提取 + 按 prompt 字节自适应拆批 + 全局 LLM 并发上限）。"""

    max_prompt_bytes: int = 50000
    max_groups_per_batch: int = 12
    max_concurrent_batches: int = 2
    global_max_concurrency: int = Field(default=4, ge=1)
    timeout_seconds: int = 180
```

Append to `finch.example.yaml` under `extraction:`:

```yaml
extraction:
  max_prompt_bytes: 50000
  max_groups_per_batch: 12
  max_concurrent_batches: 2
  global_max_concurrency: 4
  timeout_seconds: 180
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_settings.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/finch/settings.py finch.example.yaml tests/unit/test_settings.py
git commit -m "feat(settings): add global_max_concurrency to extraction config"
```

---

### Task B2: Cross-repo parallel extraction + global semaphore

**Files:**
- Modify: `src/finch/evidence/extractor.py`
- Modify: `src/finch/graph/pipeline.py`
- Test: `tests/unit/test_extractor.py`, `tests/graph/test_pipeline.py`

**Interfaces:**
- Produces:
  - `Extractor.__init__` adds `self._llm_semaphore = threading.Semaphore(self.settings.global_max_concurrency)`.
  - `_extract_valid` wraps the `runner.run(...)` call in `with self._llm_semaphore:`.
  - `make_extract_node` extracts repos via `ThreadPoolExecutor(max_workers=min(repo_count, 3))` + `pool.map`, preserving the existing all-or-nothing `mark_failed`/`mark_extracted` semantics and card order.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_extractor.py`:

```python
def test_extractor_semaphore_caps_concurrent_llm_calls(tmp_path):
    import threading
    from finch.evidence.models import Claim, ClaimConfidence, EngineeringEvent
    from finch.settings import ExtractionSettings

    calls = []
    lock = threading.Lock()
    state = {"active": 0, "max_active": 0}

    class BlockingRunner:
        def run(self, prompt, model, timeout=None):
            with lock:
                state["active"] += 1
                state["max_active"] = max(state["max_active"], state["active"])
            import time
            time.sleep(0.05)
            with lock:
                state["active"] -= 1
            return BatchExtractionOutput(items=[])

    settings = ExtractionSettings(global_max_concurrency=1)
    extractor = Extractor(BlockingRunner(), settings=settings, cache_path=tmp_path / "c.json")
    # 两个 group，各自成批 → 若 semaphore 生效，并发不会超过 1
    groups = [[_detail("a" * 40, "feat: one")], [_detail("b" * 40, "feat: two")]]
    try:
        extractor.extract_grouped(groups, "r")
    except IncompleteBatchExtractionError:
        pass  # BlockingRunner 返回空 items → 补偿后仍缺失，预期抛错；我们只关心并发
    assert state["max_active"] <= 1
```

(Use the existing `_detail` helper and `BatchExtractionOutput`/`IncompleteBatchExtractionError` imports already in `test_extractor.py`; add `from finch.settings import ExtractionSettings`.)

Add to `tests/graph/test_pipeline.py`:

```python
def test_make_extract_node_parallel_preserves_card_order(tmp_path):
    from finch.evidence.extractor import Extractor
    from finch.evidence.models import Claim, ClaimConfidence, EngineeringEvent
    from finch.github.models import CommitDetail, CommitFile
    from finch.storage.database import Store
    from finch.storage.repositories import CommitIngestionRepository, EvidenceRepository

    calls = []

    class FakeExtractor:
        def extract_grouped(self, groups, repo):
            calls.append(repo)
            return [EngineeringEvent(
                id=f"evt_{repo}", repository=repo, commits=[groups[0][0].sha],
                problem=Claim(statement="p", confidence=ClaimConfidence.SUPPORTED),
                decision=Claim(statement="d", confidence=ClaimConfidence.INFERRED),
                result=Claim(statement="r", confidence=ClaimConfidence.SUPPORTED),
            )]

    def _detail(sha, repo):
        return CommitDetail(sha=sha, message="feat: x", author_date="2026-09-01T00:00:00Z",
                            html_url="u", parents=[], files=[])

    store = Store(tmp_path / "db.sqlite")
    store.init()
    groups_by_repo = {
        "a/x": [[_detail("a" * 40, "a/x")]],
        "b/y": [[_detail("b" * 40, "b/y")]],
    }
    node = make_extract_node(
        extractor=FakeExtractor(),
        groups_by_repo=groups_by_repo,
        repo_is_private={},
        known_commit_urls=set(),
        cards_repo=EvidenceRepository(store),
        ingestion_repo=CommitIngestionRepository(store),
        max_extract_retries=3,
    )
    result = node.run({})
    assert result.status == "succeeded"
    assert sorted(calls) == ["a/x", "b/y"]
    # 卡序 = repo 序：a/x 的卡在前
    ids = [c["id"] for c in result.output["items"]]
    assert ids[0].startswith("ev_evt_a/x")
```

(Adjust the `ids[0]` assertion to whatever `build_cards` actually produces for the fake event; the intent is that repo `a/x`'s cards precede repo `b/y`'s.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_extractor.py::test_extractor_semaphore_caps_concurrent_llm_calls -v`
Expected: FAIL — `AttributeError: 'Extractor' object has no attribute '_llm_semaphore'` (or the parallel-order test fails because extraction is still serial — the order test will actually pass, so rely on the semaphore test for RED).

- [ ] **Step 3: Write minimal implementation**

**3a.** In `src/finch/evidence/extractor.py`, add `import threading` (top) and change `__init__`:

```python
    def __init__(
        self,
        runner: StructuredInferenceRunner,
        settings: ExtractionSettings | None = None,
        cache_path: Path | None = None,
    ):
        self.runner = runner
        self.settings = settings or ExtractionSettings()
        self.cache = ExtractionCache(cache_path) if cache_path is not None else None
        self._llm_semaphore = threading.Semaphore(self.settings.global_max_concurrency)
```

Change `_extract_valid` to acquire the semaphore around the LLM call:

```python
    def _extract_valid(
        self,
        groups: list[list[CommitDetail]],
        repo: str,
        template: str,
    ) -> tuple[dict[int, EngineeringEvent], list[int]]:
        prompt = template.replace("{groups}", _render_batch(groups))
        with self._llm_semaphore:
            output = cast(
                BatchExtractionOutput,
                self.runner.run(prompt, BatchExtractionOutput, timeout=self.settings.timeout_seconds),
            )
        return _validate_batch(output, groups, repo)
```

**3b.** In `src/finch/graph/pipeline.py`, add `from concurrent.futures import ThreadPoolExecutor` and rewrite `ExtractNode.run`:

```python
    class ExtractNode(Node):
        def run(self, ctx: dict) -> NodeResult:
            all_shas = {
                repo: [c.sha for g in groups for c in g]
                for repo, groups in groups_by_repo.items()
            }
            repo_items = list(groups_by_repo.items())

            def _extract_one(repo: str, groups: list[list[CommitDetail]]) -> list[EvidenceCard]:
                events = extractor.extract_grouped(groups, repo)
                repo_cards = build_cards(events)
                if repo_is_private.get(repo, False):
                    repo_cards = [c.model_copy(update={"publishable": False}) for c in repo_cards]
                return repo_cards

            try:
                if len(repo_items) == 1:
                    cards = _extract_one(*repo_items[0])
                else:
                    with ThreadPoolExecutor(max_workers=min(len(repo_items), 3)) as pool:
                        cards_by_repo = list(
                            pool.map(lambda kv: _extract_one(kv[0], kv[1]), repo_items)
                        )
                    cards = [c for repo_cards in cards_by_repo for c in repo_cards]
            except Exception:  # noqa: BLE001
                for r, s in all_shas.items():
                    ingestion_repo.mark_failed(r, s, max_extract_retries)
                raise

            report = scan_cards(
                cards,
                repo_is_private=repo_is_private,
                known_commit_urls=known_commit_urls,
            )
            if report.hard_fail:
                for r, s in all_shas.items():
                    ingestion_repo.mark_failed(r, s, max_extract_retries)
                return NodeResult(
                    status="failed", error_code=report.hits[0].code, retryable=False
                )
            cards_repo.upsert_cards(cards)
            for r, s in all_shas.items():
                ingestion_repo.mark_extracted(r, s)
            return NodeResult(
                status="succeeded", output=items_payload(cast(list[BaseModel], cards))
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_extractor.py tests/graph/test_pipeline.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/finch/evidence/extractor.py src/finch/graph/pipeline.py tests/unit/test_extractor.py tests/graph/test_pipeline.py
git commit -m "feat(extract): cross-repo parallel extraction with global LLM semaphore"
```

---

### Task B3: Append-only grouping + durable group_id

**Files:**
- Modify: `src/finch/storage/repositories.py`
- Modify: `src/finch/github/ingestion.py`
- Test: `tests/unit/test_ingestion.py`

**Interfaces:**
- Produces:
  - `CommitIngestionRepository.mark_group_ids(repository: str, assignments: list[tuple[str, str]]) -> None` (persists `group_id` per sha).
  - `assign_group_ids(records: list[CommitIngestionRecord]) -> dict[str, list[CommitIngestionRecord]]` (module-level in `ingestion.py`) — freeze already-assigned (`group_id` non-null) records; group the rest via `group_commits` and assign `group_id = group[0].sha`.
  - `Ingestor._ingest_repo` step 3 becomes append-only: `assign_group_ids` → `mark_group_ids` (idempotent) → build `list[list[CommitDetail]]` → `select_groups`. Return type unchanged (`list[list[CommitDetail]]`).

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_ingestion.py`:

```python
def test_mark_group_ids_roundtrip(tmp_path):
    from finch.storage.repositories import CommitIngestionRecord

    store = Store(tmp_path / "db.sqlite")
    store.init()
    repo = CommitIngestionRepository(store)
    repo.upsert_pending(REPO, [_summary("a" * 40), _summary("b" * 40)])
    repo.mark_group_ids(REPO, [("a" * 40, "g1"), ("b" * 40, "g1")])
    grouped = repo.list_grouped(REPO)
    assert {r.group_id for r in grouped} == {"g1"}


def test_assign_group_ids_freezes_assigned_and_groups_new(tmp_path):
    from finch.github.ingestion import assign_group_ids

    store = Store(tmp_path / "db.sqlite")
    store.init()
    repo = CommitIngestionRepository(store)
    # 已分配（模拟 backlog）: a 与 b 同组 g1
    repo.upsert_pending(REPO, [_summary("a" * 40), _summary("b" * 40), _summary("c" * 40)])
    repo.store_detail(REPO, _detail("a" * 40))
    repo.store_detail(REPO, _detail("b" * 40))
    repo.store_detail(REPO, _detail("c" * 40))
    repo.mark_group_ids(REPO, [("a" * 40, "g1"), ("b" * 40, "g1")])

    records = repo.list_grouped(REPO)
    # c 未分配（group_id None），a/b 已分配
    groups = assign_group_ids(records)
    # a/b 冻结在 g1；c 新建一组（group_id = c.sha）
    assert "g1" in groups
    assert len(groups["g1"]) == 2
    new_gid = "c" * 40
    assert new_gid in groups
    assert len(groups[new_gid]) == 1
```

(Reuse the existing `_summary`/`_detail` helpers in `test_ingestion.py`; note `_detail` there takes `(sha, files, message)` — pass a files list.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_ingestion.py::test_mark_group_ids_roundtrip -v`
Expected: FAIL — `AttributeError: 'CommitIngestionRepository' object has no attribute 'mark_group_ids'`

- [ ] **Step 3: Write minimal implementation**

**3a.** In `src/finch/storage/repositories.py`, add `mark_group_ids` to `CommitIngestionRepository` (after `mark_failed`):

```python
    def mark_group_ids(self, repository: str, assignments: list[tuple[str, str]]) -> None:
        """持久化 commit 的 group_id（append-only 分组，幂等：重复写同值无害）。"""
        if not assignments:
            return
        with Session(self.store.engine) as session:
            for sha, group_id in assignments:
                record = session.get(CommitIngestionRecord, (repository, sha))
                if record is None:
                    continue
                record.group_id = group_id
                record.updated_at = datetime.now(UTC)
                session.add(record)
            session.commit()
```

**3b.** In `src/finch/github/ingestion.py`, add a module-level function and rewrite step 3.

Add imports (top of file):

```python
from ..storage.repositories import (
    CommitIngestionRecord,
    CommitIngestionRepository,
    RepoCursorRepository,
)
```

Add the function (after `_as_utc`):

```python
def assign_group_ids(
    records: list[CommitIngestionRecord],
) -> dict[str, list[CommitIngestionRecord]]:
    """append-only 分组：已分配（group_id 非空）冻结；未分配经 ``group_commits`` 分组，
    新组 group_id = 首 commit sha（author 顺序最早）。返回 group_id -> records。"""
    assigned: dict[str, list[CommitIngestionRecord]] = {}
    unassigned: list[CommitIngestionRecord] = []
    for r in records:
        if r.group_id is not None:
            assigned.setdefault(r.group_id, []).append(r)
        else:
            unassigned.append(r)
    if not unassigned:
        return assigned

    details_by_sha = {
        r.sha: CommitDetail.model_validate_json(r.payload_json) for r in unassigned
    }
    ordered = sorted(unassigned, key=lambda r: details_by_sha[r.sha].author_date)
    new_groups = group_commits([details_by_sha[r.sha] for r in ordered])
    record_by_sha = {r.sha: r for r in unassigned}
    for group in new_groups:
        gid = group[0].sha
        for detail in group:
            record = record_by_sha[detail.sha]
            record.group_id = gid
            assigned.setdefault(gid, []).append(record)
    return assigned
```

Rewrite `_ingest_repo` step 3 (replace the current `# 3) 段 2` block):

```python
        # 3) 段 2：append-only 分组（冻结已分配、只给新 commit 分组）+ 选出本轮提取预算
        grouped = self.ingestion.list_grouped(repo)
        groups_by_id = assign_group_ids(grouped)
        self.ingestion.mark_group_ids(
            repo,
            [(r.sha, gid) for gid, members in groups_by_id.items() for r in members],
        )
        groups = [
            [CommitDetail.model_validate_json(r.payload_json) for r in members]
            for members in groups_by_id.values()
        ]
        discovered = {
            r.sha: _as_utc(r.discovered_at)
            for members in groups_by_id.values()
            for r in members
        }
        return select_groups(groups, existing_topics, budget, discovered, now)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_ingestion.py tests/unit/test_ingestion_repository.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/finch/storage/repositories.py src/finch/github/ingestion.py tests/unit/test_ingestion.py
git commit -m "feat(github): append-only grouping with durable group_id"
```

---

## Self-Review

**Spec coverage (Phase B):**
- §8 cross-repo parallel + global semaphore → Task B2.
- §7 sealed/append-only groups → Task B3 (grouping frozen; cache stability achieved via fingerprint, see recorded deviation).
- §11 `global_max_concurrency` config → Task B1.

**Placeholder scan:** none — each step has concrete code.

**Type consistency:**
- `mark_group_ids(repository, assignments: list[tuple[str, str]])` matches its single call in `_ingest_repo`.
- `assign_group_ids(records: list[CommitIngestionRecord]) -> dict[str, list[CommitIngestionRecord]]` matches its call and the iteration `for gid, members in groups_by_id.items()`.
- `Extractor._llm_semaphore` is created in `__init__` and used in `_extract_valid`; `make_extract_node` still receives `extractor`, `groups_by_repo: dict[str, list[list[CommitDetail]]]`, `ingestion_repo`, `max_extract_retries` (unchanged signatures).

**Recorded deviation:** spec §7's "cache key = group_id" is not implemented (fingerprint key retained — see header). Flag for user confirmation during execution handoff.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-09-05-daily-work-budget-phase-b.md`. Two execution options:

**1. Subagent-Driven (recommended)** — fresh subagent per task, review between tasks.

**2. Inline Execution** — execute in this session with checkpoints.

Which approach?
