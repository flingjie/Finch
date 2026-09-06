# Daily Work Budget — Phase C (P2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Two Phase-C (P2) improvements: (1) hierarchical extraction of oversized change groups (split → partial extract → one `merge_events` call), and (2) parallelize the original + engagement dual track.

**Architecture:** `Extractor` detects groups exceeding `max_commits_per_group_prompt` (15) or `max_group_prompt_bytes` (25000), splits them into chunks, extracts a partial `EngineeringEvent` per chunk (reusing the existing batch prompt), then merges the partials via one new `merge_events` LLM call. `run_dual_track` wraps the two track lambdas in `ThreadPoolExecutor(max_workers=2)` + `pool.map`, keeping the existing `_capture` exception isolation and `DualTrackResult` assembly.

**Tech Stack:** Python 3.12, Pydantic 2, SQLModel (SQLite), typer, `uv`.

## Global Constraints

- Python 3.12+; lint `uv run ruff check .` (E,F,I,B,UP; line-length 100), types `uv run mypy src`, tests `uv run pytest`.
- Deterministic totals invariant: scores computed in code; LLM output never carries a `total`.
- Subprocess discipline: args as arrays, per-call timeouts, JSON validated via Pydantic.
- Graph runtime stays sequential/deterministic. The dual-track parallelism in Task 2 is ABOVE the graph (orchestrating two independent fault-isolated tracks), per spec §10 — it must use `ThreadPoolExecutor`, not `asyncio.gather`, and preserve `_capture` isolation + result order.
- Evidence-first / no auto-publish invariants unchanged.

---

### Task 1: Hierarchical large-group extraction

**Files:**
- Modify: `src/finch/settings.py`
- Modify: `src/finch/evidence/extractor.py`
- Create: `prompts/merge-engineering-events.md`
- Test: `tests/unit/test_extractor.py`

**Interfaces:**
- Produces:
  - `ExtractionSettings.max_commits_per_group_prompt: int = Field(default=15, ge=1)` and `max_group_prompt_bytes: int = Field(default=25000, ge=1)`.
  - `MergeEventsOutput(BaseModel)` with `event: EngineeringEvent`.
  - `Extractor._is_oversized(group) -> bool`, `_split_group(group) -> list[list[CommitDetail]]`, `_merge_partials(partials, repo) -> EngineeringEvent`, `_extract_oversized_group(group, repo, template) -> EngineeringEvent`.
  - `extract_grouped` routes oversized groups through `_extract_oversized_group` (still cached under `group_fingerprint`); normal groups go through the existing `_extract_groups` batch path unchanged.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_extractor.py`:

```python
def test_extractor_is_oversized(tmp_path):
    from finch.settings import ExtractionSettings

    s = ExtractionSettings(max_commits_per_group_prompt=3, max_group_prompt_bytes=1000)
    extractor = Extractor(None, settings=s, cache_path=tmp_path / "c.json")
    small = [_detail(f"{i:040d}", f"feat: {i}") for i in range(3)]
    big = [_detail(f"{i:040d}", f"feat: {i}") for i in range(4)]
    assert extractor._is_oversized(small) is False
    assert extractor._is_oversized(big) is True  # 4 commits > 3


def test_extractor_split_group():
    from finch.settings import ExtractionSettings

    s = ExtractionSettings(max_commits_per_group_prompt=2)
    extractor = Extractor(None, settings=s, cache_path=None)
    group = [_detail(f"{i:040d}", f"feat: {i}") for i in range(5)]
    chunks = extractor._split_group(group)
    assert [len(c) for c in chunks] == [2, 2, 1]
    assert [c.sha for chunk in chunks for c in chunk] == [c.sha for c in group]  # 保序


def test_extract_grouped_routes_oversized_group(tmp_path):
    from finch.settings import ExtractionSettings

    def _evt(sha, id_):
        return EngineeringEvent(
            id=id_, repository="r", commits=[sha],
            problem=Claim(statement="p", confidence=ClaimConfidence.SUPPORTED),
            decision=Claim(statement="d", confidence=ClaimConfidence.INFERRED),
            result=Claim(statement="r", confidence=ClaimConfidence.SUPPORTED),
        )

    class FakeRunner:
        def run(self, prompt, model, timeout=None):
            if model is BatchExtractionOutput:
                # 每个 chunk 返回一个 partial（group_id g_0）
                return BatchExtractionOutput(
                    items=[{"group_id": "g_0", "event": _evt("a" * 40, "part").model_dump(mode="json")}]
                )
            # merge 调用 → MergeEventsOutput
            return MergeEventsOutput(event=_evt("a" * 40, "merged"))

    s = ExtractionSettings(max_commits_per_group_prompt=2)
    extractor = Extractor(FakeRunner(), settings=s, cache_path=tmp_path / "c.json")
    group = [_detail(f"{i:040d}", f"feat: {i}") for i in range(3)]  # 3 commits > 2 → 超大
    out = extractor.extract_grouped([group], "r")
    assert [e.id for e in out] == ["merged"]
```

(Ensure `MergeEventsOutput` is imported in the test after Step 3.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_extractor.py::test_extractor_is_oversized -v`
Expected: FAIL — `AttributeError: 'Extractor' object has no attribute '_is_oversized'`

- [ ] **Step 3: Write minimal implementation**

**3a.** In `src/finch/settings.py`, add to `ExtractionSettings`:

```python
    max_commits_per_group_prompt: int = Field(default=15, ge=1)
    max_group_prompt_bytes: int = Field(default=25000, ge=1)
```

Add to `finch.example.yaml` under `extraction:`:

```yaml
    max_commits_per_group_prompt: 15
    max_group_prompt_bytes: 25000
```

**3b.** In `src/finch/evidence/extractor.py`, add the `MergeEventsOutput` model (after `BatchExtractionOutput`):

```python
class MergeEventsOutput(BaseModel):
    event: EngineeringEvent
```

Add the helper methods to `Extractor` (after `_extract_valid`), plus a `_cache_key` helper used by both `extract_grouped` and the new methods:

```python
    def _is_oversized(self, group: list[CommitDetail]) -> bool:
        return len(group) > self.settings.max_commits_per_group_prompt or (
            len(_render_commits(group).encode("utf-8")) > self.settings.max_group_prompt_bytes
        )

    def _split_group(self, group: list[CommitDetail]) -> list[list[CommitDetail]]:
        n = self.settings.max_commits_per_group_prompt
        return [group[i:i + n] for i in range(0, len(group), n)]

    def _merge_partials(
        self, partials: list[EngineeringEvent], repo: str
    ) -> EngineeringEvent:
        template = Path("prompts/merge-engineering-events.md").read_text()
        prompt = template.replace("{repo}", repo).replace(
            "{partials}", json.dumps([p.model_dump(mode="json") for p in partials], ensure_ascii=False)
        )
        with self._llm_semaphore:
            output = cast(
                MergeEventsOutput,
                self.runner.run(prompt, MergeEventsOutput, timeout=self.settings.timeout_seconds),
            )
        return output.event

    def _extract_oversized_group(
        self, group: list[CommitDetail], repo: str, template: str
    ) -> EngineeringEvent:
        partials: list[EngineeringEvent] = []
        for chunk in self._split_group(group):
            partials.extend(self._extract_group_batch([chunk], repo, template))
        merged = self._merge_partials(partials, repo)
        return _finalize_event(merged, repo, group)
```

**3c.** Modify `extract_grouped` to route oversized groups. Replace the current body (after the `if not groups: return []` guard) with:

```python
        template = _BATCH_PROMPT_PATH.read_text()

        events: dict[int, EngineeringEvent] = {}
        miss_indices: list[int] = []
        oversized_indices: list[int] = []
        for i, group in enumerate(groups):
            cached = (
                self.cache.get(group_fingerprint(repo, group, _CACHE_VERSION))
                if self.cache is not None
                else None
            )
            if cached is not None:
                events[i] = cached
            else:
                miss_indices.append(i)
                if self._is_oversized(group):
                    oversized_indices.append(i)

        # 正常组：走既有批量提取（不改缓存语义）
        normal_miss = [i for i in miss_indices if i not in oversized_indices]
        if normal_miss:
            miss_groups = [groups[i] for i in normal_miss]
            fresh = self._extract_groups(miss_groups, repo, template)
            for local_i, global_i in enumerate(normal_miss):
                events[global_i] = fresh[local_i]
                if self.cache is not None:
                    self.cache.put(
                        group_fingerprint(repo, miss_groups[local_i], _CACHE_VERSION),
                        fresh[local_i],
                    )

        # 超大组：逐个分层提取（拆块 → 局部提取 → merge）
        for i in oversized_indices:
            events[i] = self._extract_oversized_group(groups[i], repo, template)
            if self.cache is not None:
                self.cache.put(
                    group_fingerprint(repo, groups[i], _CACHE_VERSION), events[i]
                )

        if self.cache is not None:
            self.cache.save()
        return [events[i] for i in range(len(groups))]
```

**3d.** Create `prompts/merge-engineering-events.md`:

```markdown
You are merging partial extractions of a single engineering change into one coherent event.

The change was too large to extract in one pass and was split into chunks. Each chunk produced a
partial EngineeringEvent below. Merge them into ONE event covering the whole change.

Repository: {repo}

Partial events (JSON):
{partials}

Return a single EngineeringEvent JSON object with:
- id: a short stable slug (e.g. "evt_<topic>")
- repository: {repo}
- commits: the union of all commits in the partials (full SHAs, deduplicated; no invented SHAs)
- problem / decision / result: one merged Claim each (statement + confidence)
- missing_context: deduplicated union
- topics: deduplicated union (max 5)

Rules:
- decision confidence must be INFERRED or lower (no PR/issue evidence at this stage).
- Do not introduce new facts or commits; stay faithful to the partials.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_extractor.py -v`
Expected: PASS (existing + 3 new).

- [ ] **Step 5: Commit**

```bash
git add src/finch/settings.py finch.example.yaml src/finch/evidence/extractor.py prompts/merge-engineering-events.md tests/unit/test_extractor.py
git commit -m "feat(extract): hierarchical extraction of oversized groups via merge_events"
```

---

### Task 2: Parallelize the original + engagement dual track

**Files:**
- Modify: `src/finch/graph/dual_track.py`
- Test: `tests/unit/test_dual_track.py`

**Interfaces:**
- Produces: `run_dual_track(*, run_id=None, original_track, engagement_track) -> DualTrackResult` — same signature and return; internally runs the two tracks concurrently via `ThreadPoolExecutor(max_workers=2)` + `pool.map`, preserving `_capture` exception isolation and result order.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_dual_track.py`:

```python
def test_run_dual_track_runs_both_tracks(tmp_path):
    from finch.graph.dual_track import run_dual_track

    seen = []

    def original(rid):
        seen.append("original")
        from finch.storage.database import RunRecord
        return RunRecord(id=rid, state="COMPLETED")

    def engagement(rid):
        seen.append("engagement")
        from finch.engagement.flow import EngagementRunResult
        return EngagementRunResult(
            run_id=rid, posts_found=0, candidates=[], failures=[],
            status="empty", summary="no posts",
        )

    result = run_dual_track(original_track=original, engagement_track=engagement)
    assert result.status == "succeeded"
    assert sorted(seen) == ["engagement", "original"]  # 两条轨道都执行了
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_dual_track.py::test_run_dual_track_runs_both_tracks -v`
Expected: FAIL — the existing sequential `run_dual_track` runs both tracks too, so this specific test may already PASS. In that case, add a concurrency assertion instead: record `threading.get_ident()` in each track and assert they differ. If it still passes (serial), the test is not RED — accept that and rely on the existing `test_dual_track.py` suite for correctness; the change is behavioral, not additive.

- [ ] **Step 3: Write minimal implementation**

In `src/finch/graph/dual_track.py`, add `from concurrent.futures import ThreadPoolExecutor` and replace `run_dual_track`:

```python
def run_dual_track(
    *,
    run_id: str | None = None,
    original_track: Callable[[str], RunRecord],
    engagement_track: Callable[[str], EngagementRunResult],
) -> DualTrackResult:
    """并行执行两条轨道，共享同一 ``run_id``，并汇总为 ``DualTrackResult``。

    两条轨道各自以 ``_capture`` 隔离（异常捕获为轨道结果，不重新抛出）；用
    ``ThreadPoolExecutor`` 并发执行（不变量禁 ``asyncio.gather``），``pool.map`` 保序。
    任一轨道失败都让 ``DualTrackResult.partial_failure`` 为 True。
    """
    run_id = run_id or uuid4().hex
    tracks = [original_track, engagement_track]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda track: _capture(track, run_id), tracks))
    (original, original_error), (engagement, engagement_error) = results
    return DualTrackResult(
        run_id=run_id,
        original=original,
        original_error=original_error,
        engagement=engagement,
        engagement_error=engagement_error,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_dual_track.py -v`
Expected: PASS (existing dual-track tests + new one). Also run the full suite once.

- [ ] **Step 5: Commit**

```bash
git add src/finch/graph/dual_track.py tests/unit/test_dual_track.py
git commit -m "feat(dual-track): run original and engagement tracks in parallel"
```

---

## Self-Review

**Spec coverage (Phase C):**
- §9 hierarchical large-group extraction → Task 1.
- §10 dual-track parallel → Task 2.
- §11 `max_commits_per_group_prompt` / `max_group_prompt_bytes` config → Task 1 (folded in).

**Placeholder scan:** none — each step has concrete code.

**Type consistency:**
- `MergeEventsOutput.event: EngineeringEvent` matches `_merge_partials` returning `output.event`.
- `_extract_oversized_group(group, repo, template)` is called as `self._extract_oversized_group(groups[i], repo, template)` in `extract_grouped`; `_split_group`/`_merge_partials`/`_is_oversized` signatures match their call sites.
- `run_dual_track` keeps its exact `*` keyword signature and `DualTrackResult` return; `_capture(track, run_id)` is reused unchanged.

**Noted in Step 2:** the dual-track "runs both tracks" test may not be RED (sequential also runs both); the real behavioral check is concurrency. The existing `test_dual_track.py` suite (exception isolation, partial_failure) is the correctness net.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-09-05-daily-work-budget-phase-c.md`. Two execution options:

**1. Subagent-Driven (recommended)** — fresh subagent per task, review between tasks.

**2. Inline Execution** — execute in this session with checkpoints.

Which approach?
