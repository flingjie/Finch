# Config Explainability + Query Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `finch sources sync` report, per source and per query, the actual mode / queries run / result counts / skipped-vs-empty-vs-failed state, and rewrite the example/runtime query config into two short, task-focused theme groups.

**Architecture:** Add `configured_queries`/`configured_urls` to `DiscoveryContext` so the orchestrator knows what was configured-but-skipped; add a `QueryOutcome` dataclass + `classify_result` + `sync_result_to_dict` helpers to `orchestrator.py`; track per-request outcomes inside `sync_source`; persist them via `_run_payload`; render a four-state + per-query report in `sources_sync` (human + `--json`). Query words are rewritten in `finch.example.yaml` and `finch.yaml` (content only, no schema change).

**Tech Stack:** Python 3.12, Pydantic 2 (`StrEnum`/`dataclass`), Typer CLI, file Workspace (YAML).

## Global Constraints

- `mode` stays a single `Literal["query","hot","url","timeline","disabled"]` — do NOT add multi-mode.
- No structured "product/problem/practice" or "intent" labels in the settings schema — query redesign is content-only.
- Python 3.12+; dataclasses/StrEnum; no new database (persist via `Workspace`).
- `uv run pytest` / `uv run ruff check .` / `uv run mypy src` must stay green. Ruff selects `E,F,I,B,UP`, line-length 100.
- Subprocess args as arrays; no shell string concat.
- Query content converges to exactly two themes: `agent_in_production` ("Agent 实际落地") and `ai_personal_empowerment` ("AI 个人赋能").

---

### Task 1: Carry configured queries into `DiscoveryContext`

**Files:**
- Modify: `src/finch/sources/connectors/__init__.py:18-28`
- Modify: `src/finch/sources/query_plan.py:59-124` (the `build_context_by_source` body)
- Test: `tests/unit/test_source_query_plan.py`

**Interfaces:**
- Consumes: `SourceTwitterPlan` / `SourcesSettings` / `Settings` (existing).
- Produces: `DiscoveryContext.configured_queries: list[str]`, `DiscoveryContext.configured_urls: list[str]` (defaults `field(default_factory=list)`).

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_source_query_plan.py`:

```python
def test_timeline_mode_records_configured_queries_as_skipped():
    settings = Settings(
        sources=SourcesSettings(
            twitter=SourceTwitterPlan(
                queries=["agent stuck"], enabled=True, mode="timeline"
            )
        )
    )
    ctx = build_context_by_source(settings, all_sources=True)
    assert ctx[Source.TWITTER].mode == "timeline"
    assert ctx[Source.TWITTER].queries == []
    assert ctx[Source.TWITTER].configured_queries == ["agent stuck"]


def test_query_mode_configured_equals_run():
    settings = Settings(
        sources=SourcesSettings(
            twitter=SourceTwitterPlan(queries=["agent stuck"], enabled=True, mode="query")
        )
    )
    ctx = build_context_by_source(settings, all_sources=True)
    assert ctx[Source.TWITTER].queries == ["agent stuck"]
    assert ctx[Source.TWITTER].configured_queries == ["agent stuck"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_source_query_plan.py -k configured -v`
Expected: FAIL — `DiscoveryContext(...) got an unexpected keyword argument 'configured_queries'` (for the first assertion path) or `AttributeError` when the field doesn't exist.

- [ ] **Step 3: Add the fields**

In `src/finch/sources/connectors/__init__.py`, change `DiscoveryContext` to:

```python
@dataclass
class DiscoveryContext:
    """一轮采集的查询上下文。"""

    queries: list[str] = field(default_factory=list)
    limit: int = 20
    urls: list[str] = field(default_factory=list)
    cursor: str | None = None
    mode: str = "query"
    config_error: str = ""
    extra: dict = field(default_factory=dict)
    configured_queries: list[str] = field(default_factory=list)  # 配置了但可能没跑
    configured_urls: list[str] = field(default_factory=list)
```

- [ ] **Step 4: Populate them in `build_context_by_source`**

In `src/finch/sources/query_plan.py`, make three edits.

Edit A — CLI override branch (the `use_cli and src == source` block). Replace:

```python
        if use_cli and src == source:
            if src == Source.GITHUB:
                # GitHub queries are always logins; CLI --query treated as users.
                out[src] = DiscoveryContext(queries=queries, urls=[], limit=limit)
            else:
                # Weixin 与其它源一致：--query 走搜索，--url 走文章导入。
                out[src] = DiscoveryContext(queries=queries, urls=urls, limit=limit)
            continue
```

with:

```python
        if use_cli and src == source:
            if src == Source.GITHUB:
                # GitHub queries are always logins; CLI --query treated as users.
                out[src] = DiscoveryContext(
                    queries=queries, urls=[], limit=limit,
                    configured_queries=list(queries), configured_urls=[],
                )
            else:
                # Weixin 与其它源一致：--query 走搜索，--url 走文章导入。
                out[src] = DiscoveryContext(
                    queries=queries, urls=urls, limit=limit,
                    configured_queries=list(queries), configured_urls=list(urls),
                )
            continue
```

Edit B — timeline branch. Replace:

```python
        # 时间线模式：关注时间线是独立输入（无 queries/urls，不合并主题词，不回退搜索）。
        if plan.mode == "timeline":
            out[src] = DiscoveryContext(
                queries=[], urls=[], limit=plan.fetch_limit, mode="timeline"
            )
            continue
```

with:

```python
        # 时间线模式：关注时间线是独立输入（无 queries/urls，不合并主题词，不回退搜索）。
        if plan.mode == "timeline":
            configured_q, _ = _payload(src_cfg, src)
            out[src] = DiscoveryContext(
                queries=[],
                urls=[],
                configured_queries=list(configured_q),
                configured_urls=[],
                limit=plan.fetch_limit,
                mode="timeline",
            )
            continue
```

Edit C — final context (query/url/hot). Replace:

```python
        out[src] = DiscoveryContext(
            queries=q,
            urls=u,
            limit=plan.fetch_limit,
            mode=plan.mode,
        )
```

with:

```python
        out[src] = DiscoveryContext(
            queries=q,
            urls=u,
            configured_queries=list(q),
            configured_urls=list(u),
            limit=plan.fetch_limit,
            mode=plan.mode,
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_source_query_plan.py -q`
Expected: PASS (all existing + new).

- [ ] **Step 6: Commit**

```bash
git add src/finch/sources/connectors/__init__.py src/finch/sources/query_plan.py tests/unit/test_source_query_plan.py
git commit -m "feat(sources): carry configured queries into DiscoveryContext"
```

---

### Task 2: Add report helpers to the orchestrator

**Files:**
- Modify: `src/finch/sources/orchestrator.py` (imports, `_error_type` area, `SyncResult`)
- Test: `tests/unit/test_source_report.py` (new)

**Interfaces:**
- Consumes: `ResultKind`, `SourceStatus`, `OpenCliRequest` (from `finch.sources.models`).
- Produces:
  - `QueryOutcome` dataclass (`query`, `outcome`, `raw_count`, `normalized_count`, `kind`, `detail`)
  - `classify_result(result: SyncResult) -> str` → one of `"failed" | "empty" | "success"`
  - `_request_label(req: OpenCliRequest, mode: str) -> str`
  - `sync_result_to_dict(result: SyncResult) -> dict[str, Any]`
  - `SyncResult` gains `mode`, `ran_queries`, `skipped_queries`, `per_query`, `started_at`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_source_report.py`:

```python
"""Unit tests for source run reporting helpers."""

from __future__ import annotations

from finch.sources.models import OpenCliRequest, ResultKind, Source, SourceStatus
from finch.sources.orchestrator import (
    QueryOutcome,
    SyncResult,
    _request_label,
    classify_result,
    sync_result_to_dict,
)


def test_classify_result_states():
    assert (
        classify_result(SyncResult(source=Source.TWITTER, status=SourceStatus.READY, raw_count=1))
        == "success"
    )
    assert (
        classify_result(
            SyncResult(source=Source.TWITTER, status=SourceStatus.READY, raw_count=0)
        )
        == "empty"
    )
    assert (
        classify_result(
            SyncResult(source=Source.TWITTER, status=SourceStatus.DEGRADED, kind=ResultKind.TIMEOUT)
        )
        == "failed"
    )
    assert (
        classify_result(
            SyncResult(source=Source.TWITTER, status=SourceStatus.DEGRADED, detail="timeline unavailable")
        )
        == "failed"
    )


def test_request_label():
    assert _request_label(OpenCliRequest(surface="twitter", command="timeline"), "timeline") == "timeline"
    assert _request_label(OpenCliRequest(surface="v2ex", command="hot"), "hot") == "hot"
    assert (
        _request_label(OpenCliRequest(surface="reddit", command="search", args=("agent stuck",)), "query")
        == "agent stuck"
    )
    assert (
        _request_label(OpenCliRequest(surface="twitter", command="timeline", args=("--limit", "20")), "timeline")
        == "timeline"
    )


def test_sync_result_to_dict_serializes_enums_and_per_query():
    r = SyncResult(
        source=Source.REDDIT,
        status=SourceStatus.READY,
        raw_count=1,
        mode="query",
        ran_queries=["agent stuck"],
        per_query=[QueryOutcome(query="agent stuck", outcome="ran", raw_count=1, kind=ResultKind.SUCCESS)],
        started_at="2026-10-30T10:00:00+00:00",
    )
    d = sync_result_to_dict(r)
    assert d["source"] == "reddit"
    assert d["status"] == "READY"
    assert d["kind"] == "SUCCESS"
    assert d["per_query"][0]["query"] == "agent stuck"
    assert d["per_query"][0]["kind"] == "SUCCESS"
    assert "artifacts" not in d
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_source_report.py -v`
Expected: FAIL — `ImportError: cannot import name 'QueryOutcome'`.

- [ ] **Step 3: Add imports**

In `src/finch/sources/orchestrator.py`, change the models import line:

```python
from finch.sources.models import RawArtifact, ResultKind, Source, SourceStatus
```

to:

```python
from finch.sources.models import (
    OpenCliRequest,
    RawArtifact,
    ResultKind,
    Source,
    SourceStatus,
)
```

- [ ] **Step 4: Add `QueryOutcome`, `classify_result`, `_request_label`, `sync_result_to_dict`**

In `src/finch/sources/orchestrator.py`, insert after the `_error_type` function (before `@dataclass class SyncResult`):

```python
@dataclass
class QueryOutcome:
    """单条查询/时间线的执行结果。"""

    query: str = ""
    outcome: str = ""  # ran | empty | failed | skipped
    raw_count: int = 0
    normalized_count: int = 0
    kind: ResultKind | None = None
    detail: str = ""


def classify_result(result: SyncResult) -> str:
    """按源把执行结果归类为 failed / empty / success（not_executed 由 CLI 对缺失源合成）。"""
    if result.kind in _ERROR_KINDS:
        return "failed"
    if result.status != SourceStatus.READY:
        return "failed"
    if result.raw_count == 0:
        return "empty"
    return "success"


def _request_label(req: OpenCliRequest, mode: str) -> str:
    """从一次请求反推查询标签（timeline/hot 用模式名，其余用首个非旗标参数）。"""
    if mode in ("timeline", "hot"):
        return mode
    if req.args and not req.args[0].startswith("-"):
        return req.args[0]
    return req.command


def _query_outcome_dict(o: QueryOutcome) -> dict[str, Any]:
    return {
        "query": o.query,
        "outcome": o.outcome,
        "raw_count": o.raw_count,
        "normalized_count": o.normalized_count,
        "kind": o.kind.value if o.kind else None,
        "detail": o.detail,
    }


def sync_result_to_dict(result: SyncResult) -> dict[str, Any]:
    """SyncResult → 可 JSON 序列化的 dict（不含 artifacts）。"""
    return {
        "source": result.source.value,
        "status": result.status.value,
        "run_id": result.run_id,
        "raw_count": result.raw_count,
        "normalized_count": result.normalized_count,
        "created_count": result.created_count,
        "projected_count": result.projected_count,
        "kind": result.kind.value if result.kind else None,
        "detail": result.detail,
        "elapsed_seconds": result.elapsed_seconds,
        "error_type": result.error_type,
        "mode": result.mode,
        "ran_queries": list(result.ran_queries),
        "skipped_queries": list(result.skipped_queries),
        "per_query": [_query_outcome_dict(o) for o in result.per_query],
        "started_at": result.started_at,
    }
```

- [ ] **Step 5: Extend `SyncResult`**

Replace the `SyncResult` dataclass body (the field list after `source`/`status`) with:

```python
@dataclass
class SyncResult:
    source: Source
    status: SourceStatus
    run_id: str = ""
    raw_count: int = 0
    normalized_count: int = 0
    created_count: int = 0
    projected_count: int = 0
    kind: ResultKind | None = None
    detail: str = ""
    elapsed_seconds: float = 0.0
    error_type: str = ""
    artifacts: list[RawArtifact] = field(default_factory=list)
    mode: str = ""
    ran_queries: list[str] = field(default_factory=list)
    skipped_queries: list[str] = field(default_factory=list)
    per_query: list[QueryOutcome] = field(default_factory=list)
    started_at: str = ""
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_source_report.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/finch/sources/orchestrator.py tests/unit/test_source_report.py
git commit -m "feat(sources): add per-query outcome and report helpers"
```

---

### Task 3: Track per-query outcomes in `sync_source` and persist them

**Files:**
- Modify: `src/finch/sources/orchestrator.py` (`sync_source`, `_run_payload`)
- Test: `tests/unit/test_source_connectors.py` (extend `TestOrchestratorIsolation`)

**Interfaces:**
- Consumes: `QueryOutcome`, `_request_label`, `_query_outcome_dict` (Task 2); `DiscoveryContext.configured_queries` (Task 1).
- Produces: populated `SyncResult.per_query` / `.ran_queries` / `.skipped_queries` / `.mode` / `.started_at`; `_run_payload` now stores those.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_source_connectors.py` inside `class TestOrchestratorIsolation`:

```python
    def test_sync_reports_per_query_outcomes_and_skips(self, tmp_path: Path):
        ws = Workspace(tmp_path)
        ws.ensure()

        def fake_run(argv, timeout):
            if argv[:2] == ["opencli", "list"]:
                return {
                    "ok": True,
                    "exit_code": 0,
                    "stdout": json.dumps(
                        [
                            {"site": "twitter", "commands": ["timeline"]},
                            {"site": "reddit", "commands": ["search"]},
                        ]
                    ),
                    "stderr": "",
                }
            if "timeline" in argv:
                return {
                    "ok": True,
                    "exit_code": 0,
                    "stdout": (FIXTURES / "twitter-search.json").read_text(),
                    "stderr": "",
                }
            if "reddit" in argv and "empty" in argv:
                return {"ok": True, "exit_code": 66, "stdout": "[]", "stderr": ""}
            return {"ok": True, "exit_code": 0, "stdout": "[]", "stderr": ""}

        from finch.sources.connectors import DiscoveryContext
        from finch.sources.models import Source as S
        from finch.sources.opencli_gateway import OpenCliGateway

        orch = DiscoveryOrchestrator(ws, gateway=OpenCliGateway(run_fn=fake_run))
        results = orch.sync_all(
            sources=[S.TWITTER, S.REDDIT],
            context_by_source={
                S.TWITTER: DiscoveryContext(
                    mode="timeline", configured_queries=["agent stuck", "tool calling failed"]
                ),
                S.REDDIT: DiscoveryContext(queries=["empty"]),
            },
        )
        by = {r.source: r for r in results}
        tw = by[S.TWITTER]
        assert tw.mode == "timeline"
        assert [o.query for o in tw.per_query if o.outcome == "ran"] == ["timeline"]
        assert tw.skipped_queries == ["agent stuck", "tool calling failed"]
        rd = by[S.REDDIT]
        assert rd.per_query[0].query == "empty"
        assert rd.per_query[0].outcome == "empty"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_source_connectors.py::TestOrchestratorIsolation::test_sync_reports_per_query_outcomes_and_skips -v`
Expected: FAIL — assertion on `tw.skipped_queries` (empty now) or `rd.per_query` (empty now).

- [ ] **Step 3: Rewrite `sync_source`**

Replace the entire `sync_source` method (currently `orchestrator.py:93-255`) with:

```python
    def sync_source(
        self,
        source: Source,
        context: DiscoveryContext | None = None,
        *,
        project_peers: bool = True,
    ) -> SyncResult:
        ctx = context or DiscoveryContext()
        if ctx.config_error:
            return SyncResult(
                source=source,
                status=SourceStatus.DEGRADED,
                kind=ResultKind.CONFIG_ERROR,
                detail=ctx.config_error,
                mode=ctx.mode,
            )
        connector = self.connectors.get(source)
        if connector is None:
            return SyncResult(
                source=source,
                status=SourceStatus.UNAVAILABLE,
                detail="no connector",
                mode=ctx.mode,
            )

        caps = fetch_capabilities(run_fn=self.gateway._run_fn, profile=self.gateway.profile)
        self.gateway.capability_snapshot_id = caps.snapshot_id
        status = connector.probe(caps)
        if status == SourceStatus.UNAVAILABLE:
            return SyncResult(
                source=source, status=status, detail="probe unavailable", mode=ctx.mode
            )

        run_id, run_dir = self.raw_store.begin_run(source)
        started_at = datetime.now(UTC).isoformat()
        created = 0
        normalized: list[RawArtifact] = []
        raw_count = 0
        last_kind: ResultKind | None = None
        detail = ""
        per_query: list[QueryOutcome] = []

        # GitHub: special path via GhClient (creator depth: user/repos/commits/…)
        if source == Source.GITHUB and isinstance(connector, GitHubConnector):
            for q in ctx.queries:
                fetch = getattr(connector, "fetch_creator", None) or connector.fetch_user
                result = fetch(q)
                last_kind = result.kind
                raw_ref = self.raw_store.write_raw(
                    run_dir, q, result.rows or {"empty": True}
                )
                raw_count += len(result.rows)
                norm_before = len(normalized)
                arts = connector.normalize(result)
                for art in arts:
                    art.raw_ref = raw_ref
                    _, was_new = self.artifacts.upsert(art)
                    if was_new:
                        created += 1
                    normalized.append(art)
                per_query.append(
                    QueryOutcome(
                        query=q,
                        outcome="empty" if not result.rows else "ran",
                        raw_count=len(result.rows),
                        normalized_count=len(normalized) - norm_before,
                        kind=result.kind,
                    )
                )
            ran = [o.query for o in per_query if o.outcome != "skipped"]
            skipped = [o.query for o in per_query if o.outcome == "skipped"]
            proj = self._project(normalized) if project_peers else ProjectResult()
            payload = self._run_payload(
                run_id, source, caps.snapshot_id, raw_count, len(normalized), created,
                last_kind, mode=ctx.mode, configured_queries=ctx.configured_queries,
                ran_queries=ran, skipped_queries=skipped, per_query=per_query,
                started_at=started_at,
            )
            payload["projected_count"] = proj.projected_peers
            self.runs.save(run_id, payload)
            return SyncResult(
                source=source,
                status=SourceStatus.READY if normalized else status,
                run_id=run_id,
                raw_count=raw_count,
                normalized_count=len(normalized),
                created_count=created,
                projected_count=proj.projected_peers,
                kind=last_kind,
                artifacts=normalized,
                mode=ctx.mode,
                ran_queries=ran,
                skipped_queries=skipped,
                per_query=per_query,
                started_at=started_at,
            )

        # Weixin URL-import without opencli when only urls + empty capability
        if source == Source.WEIXIN and ctx.urls and isinstance(connector, WeixinConnector):
            plans = connector.plan(ctx, caps)
            if not plans:
                for url in ctx.urls:
                    art = connector.normalize_url_import(url, title="", text="")
                    raw_ref = self.raw_store.write_raw(
                        run_dir, art.source_id, {"url": url, "import": True}
                    )
                    art.raw_ref = raw_ref
                    raw_count += 1
                    _, was_new = self.artifacts.upsert(art)
                    if was_new:
                        created += 1
                    normalized.append(art)
                    per_query.append(
                        QueryOutcome(query=url, outcome="ran", raw_count=1, normalized_count=1)
                    )

        requests = connector.plan(ctx, caps)
        if (
            not requests
            and not normalized
            and (ctx.queries or ctx.urls or ctx.mode == "timeline")
            and status == SourceStatus.READY
        ):
            detail = (
                "timeline unavailable (no timeline command in capability snapshot); "
                "not falling back to search"
                if ctx.mode == "timeline"
                else "no matching commands in capability snapshot"
            )
            status = SourceStatus.DEGRADED

        for req in requests:
            # github surface is not in opencli policy — skip gateway for non-opencli
            if req.surface == "github":
                continue
            label = _request_label(req, ctx.mode)
            try:
                result = self.gateway.run(req)
            except Exception as exc:  # noqa: BLE001
                detail = str(exc)[:300]
                last_kind = ResultKind.ERROR
                per_query.append(
                    QueryOutcome(query=label, outcome="failed", kind=ResultKind.ERROR,
                                 detail=str(exc)[:300])
                )
                continue
            last_kind = result.kind
            if result.kind in {
                ResultKind.AUTH_REQUIRED,
                ResultKind.BRIDGE_DOWN,
                ResultKind.CONFIG_ERROR,
            }:
                detail = result.stderr_summary or result.kind.value
                per_query.append(
                    QueryOutcome(query=label, outcome="failed", kind=result.kind,
                                 detail=detail, raw_count=len(result.rows))
                )
                break
            raw_payload: Any = result.rows if result.rows else {"kind": result.kind.value}
            item_key = req.command + "_" + (req.args[0] if req.args else "x")
            raw_ref = self.raw_store.write_raw(run_dir, item_key[:80], raw_payload)
            raw_count += len(result.rows)
            norm_before = len(normalized)
            arts = connector.normalize(result)
            for art in arts:
                art.raw_ref = raw_ref
                _, was_new = self.artifacts.upsert(art)
                if was_new:
                    created += 1
                normalized.append(art)
            per_query.append(
                QueryOutcome(
                    query=label,
                    outcome="empty" if not result.rows else "ran",
                    raw_count=len(result.rows),
                    normalized_count=len(normalized) - norm_before,
                    kind=result.kind,
                )
            )

        # 时间线模式下，配置里写了但被忽略的查询显式记成 skipped。
        if ctx.mode == "timeline" and ctx.configured_queries:
            per_query.extend(QueryOutcome(query=q, outcome="skipped") for q in ctx.configured_queries)

        if ctx.cursor:
            self.cursors.set(source, ctx.cursor)

        ran = [o.query for o in per_query if o.outcome != "skipped"]
        skipped = [o.query for o in per_query if o.outcome == "skipped"]
        proj = self._project(normalized) if project_peers else ProjectResult()
        payload = self._run_payload(
            run_id, source, caps.snapshot_id, raw_count, len(normalized), created, last_kind,
            mode=ctx.mode, configured_queries=ctx.configured_queries,
            ran_queries=ran, skipped_queries=skipped, per_query=per_query,
            started_at=started_at,
        )
        payload["projected_count"] = proj.projected_peers
        if detail:
            payload["detail"] = detail
        self.runs.save(run_id, payload)

        final_status = status
        if last_kind == ResultKind.AUTH_REQUIRED:
            final_status = SourceStatus.AUTH_REQUIRED
        elif last_kind in {ResultKind.BRIDGE_DOWN, ResultKind.TIMEOUT}:
            final_status = SourceStatus.DEGRADED

        return SyncResult(
            source=source,
            status=final_status,
            run_id=run_id,
            raw_count=raw_count,
            normalized_count=len(normalized),
            created_count=created,
            projected_count=proj.projected_peers,
            kind=last_kind,
            detail=detail,
            artifacts=normalized,
            mode=ctx.mode,
            ran_queries=ran,
            skipped_queries=skipped,
            per_query=per_query,
            started_at=started_at,
        )
```

- [ ] **Step 4: Extend `_run_payload`**

Replace the `_run_payload` static method with:

```python
    @staticmethod
    def _run_payload(
        run_id: str,
        source: Source,
        snapshot_id: str,
        raw_count: int,
        normalized_count: int,
        created_count: int,
        kind: ResultKind | None,
        *,
        mode: str = "",
        configured_queries: list[str] | None = None,
        ran_queries: list[str] | None = None,
        skipped_queries: list[str] | None = None,
        per_query: list[QueryOutcome] | None = None,
        started_at: str = "",
    ) -> dict[str, Any]:
        return {
            "run_id": run_id,
            "source": source.value,
            "capability_snapshot_id": snapshot_id,
            "started_at": started_at or datetime.now(UTC).isoformat(),
            "raw_count": raw_count,
            "normalized_count": normalized_count,
            "created_count": created_count,
            "kind": kind.value if kind else None,
            "mode": mode,
            "configured_queries": list(configured_queries or []),
            "ran_queries": list(ran_queries or []),
            "skipped_queries": list(skipped_queries or []),
            "per_query": [_query_outcome_dict(o) for o in (per_query or [])],
        }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_source_connectors.py tests/unit/test_source_report.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/finch/sources/orchestrator.py tests/unit/test_source_connectors.py
git commit -m "feat(sources): track per-query outcomes and persist run report"
```

---

### Task 4: Render the report in `finch sources sync`

**Files:**
- Modify: `src/finch/cli.py` (`sources_sync`)
- Test: `tests/unit/test_cli_sources.py` (new)

**Interfaces:**
- Consumes: `classify_result`, `sync_result_to_dict` (Task 2); `SyncResult` fields (Task 3); `Source` enum.
- Produces: `finch sources sync --json` + four-state human report.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_cli_sources.py`:

```python
"""CLI smoke for finch sources sync report."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from finch import cli
from finch.cli import app
from finch.settings import Paths, Settings
from finch.sources.models import ResultKind, Source, SourceStatus
from finch.sources.orchestrator import (
    DiscoveryOrchestrator,
    QueryOutcome,
    SyncResult,
)


def test_sources_sync_json(tmp_path, monkeypatch):
    settings = Settings(paths=Paths(var_dir=tmp_path))
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    def fake_sync_all(self, **kwargs):
        return [
            SyncResult(
                source=Source.REDDIT,
                status=SourceStatus.READY,
                raw_count=1,
                mode="query",
                ran_queries=["agent stuck"],
                per_query=[
                    QueryOutcome(
                        query="agent stuck", outcome="ran", raw_count=1, kind=ResultKind.SUCCESS
                    )
                ],
                started_at="2026-10-30T10:00:00+00:00",
            )
        ]

    monkeypatch.setattr(DiscoveryOrchestrator, "sync_all", fake_sync_all)
    r = CliRunner().invoke(app, ["sources", "sync", "--all", "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    reddit = next(row for row in payload if row["source"] == "reddit")
    assert reddit["mode"] == "query"
    assert reddit["state"] == "success"
    assert reddit["per_query"][0]["outcome"] == "ran"
```

> Note: `monkeypatch.setattr(DiscoveryOrchestrator, "sync_all", fake_sync_all)` patches the class attribute; `orch.sync_all(...)` then calls `fake_sync_all(orch, **kwargs)`. With default `Settings`, `build_context_by_source` returns `{}` (all sources disabled), so no real fetch happens — the CLI's JSON path iterates `list(Source)` and emits `not_executed` for the five absent sources plus the fake reddit row.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_cli_sources.py -v`
Expected: FAIL — `sources sync` does not accept `--json` (Typer `No such option`).

- [ ] **Step 3: Rewrite `sources_sync`**

In `src/finch/cli.py`, replace the `sources_sync` function (currently around line 1692). Keep the argument/context building identical; only change the tail (the `for r in results` loop) and add `--json`. The full new function:

```python
def sources_sync(
    source: str | None = typer.Option(
        None, "--source", help="twitter|reddit|github|v2ex|weixin|xiaohongshu"
    ),
    all_sources: bool = typer.Option(False, "--all", help="同步全部已注册源"),
    query: list[str] = typer.Option([], "--query", "-q", help="查询词（可重复）"),
    url: list[str] = typer.Option([], "--url", help="URL 导入（公众号/笔记等）"),
    limit: int = typer.Option(20, "--limit", help="每查询条数上限"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """只读同步：raw 落盘 → RawArtifact → Peer/Person 投影（单源失败不阻塞）。"""
    from finch.sources.models import Source
    from finch.sources.opencli_gateway import OpenCliGateway
    from finch.sources.orchestrator import (
        DiscoveryOrchestrator,
        classify_result,
        sync_result_to_dict,
    )
    from finch.sources.query_plan import build_context_by_source

    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    gateway = OpenCliGateway(profile=settings.opencli.profile)
    orch = DiscoveryOrchestrator(ws, gateway=gateway)

    src: Source | None = None
    if source is not None and not all_sources:
        try:
            src = Source(source)
        except ValueError as exc:
            typer.echo(f"unknown source: {source}")
            raise typer.Exit(code=1) from exc

    contexts = build_context_by_source(
        settings,
        cli_queries=list(query),
        cli_urls=list(url),
        source=src,
        limit=limit,
        all_sources=all_sources or source is None,
    )

    if all_sources or source is None:
        results = orch.sync_all(context_by_source=contexts)
        all_targets = list(Source)
    else:
        assert src is not None
        results = [orch.sync_source(src, contexts.get(src))]
        all_targets = [src]

    if as_json:
        rows = []
        by_source = {r.source: r for r in results}
        for s in all_targets:
            r = by_source.get(s)
            if r is None:
                rows.append({"source": s.value, "state": "not_executed", "status": "disabled"})
            else:
                rows.append({**sync_result_to_dict(r), "state": classify_result(r)})
        typer.echo(json.dumps(rows, ensure_ascii=False, indent=2))
        return

    by_source = {r.source: r for r in results}
    for s in all_targets:
        r = by_source.get(s)
        if r is None:
            typer.echo(f"{s.value}: not_executed [disabled]")
            continue
        state = classify_result(r)
        typer.echo(
            f"{r.source.value}: {state} [{r.mode or '-'}] "
            f"started={r.started_at or '-'} raw={r.raw_count} "
            f"norm={r.normalized_count} new={r.created_count} "
            f"projected={r.projected_count}"
            + (f" ({r.detail})" if r.detail else "")
        )
        for o in r.per_query:
            if o.outcome == "skipped":
                typer.echo(f"  - skipped (mode={r.mode}): {o.query}")
            elif o.outcome == "failed":
                typer.echo(f"  - {o.query}: failed ({o.kind.value if o.kind else 'error'})")
            elif o.outcome == "empty":
                typer.echo(f"  - {o.query}: empty (0 raw)")
            else:
                typer.echo(f"  - {o.query}: ran ({o.raw_count} raw)")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_cli_sources.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/finch/cli.py tests/unit/test_cli_sources.py
git commit -m "feat(sources): render four-state + per-query sync report"
```

---

### Task 5: Rewrite `finch.example.yaml` queries + add two themes

**Files:**
- Modify: `finch.example.yaml` (sources queries, add `exploration_topics`, `products.sources` queries)
- Test: `tests/unit/test_settings.py`

**Interfaces:**
- Consumes: `load_settings(Path("finch.example.yaml"))`.
- Produces: `settings.sources.exploration_topics` with exactly 2 topics.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_settings.py`:

```python
def test_example_sources_converge_to_two_topics():
    s = load_settings(Path("finch.example.yaml"))
    ids = [t.id for t in s.sources.exploration_topics]
    assert ids == ["agent_in_production", "ai_personal_empowerment"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_settings.py::test_example_sources_converge_to_two_topics -v`
Expected: FAIL — `ids == []` (example yaml currently has no `exploration_topics`).

- [ ] **Step 3: Edit `finch.example.yaml`**

Three edits.

Edit A — replace the `sources.twitter.queries` list (currently the four long OR-queries under `twitter:`) with short task-focused queries:

```yaml
      queries:
        - '"agent stuck" OR "tool calling failed"'
        - '"agent evals" OR "human in the loop"'
        - '"AI notes remembering" OR "knowledge retrieval"'
```

Edit B — replace the `sources.reddit.queries` list with:

```yaml
      queries:
        - "agent stuck"
        - "tool calling failed"
        - "AI notes remembering"
        - "knowledge retrieval"
```

Edit C — add `exploration_topics` under `sources:` (immediately after the `xiaohongshu:` block, before `products:`), replacing the current v2ex/weixin/xiaohongshu long queries with short ones and adding the two-topic list:

```yaml
  v2ex:
    enabled: true
    mode: query
    fetch_limit: 100
    queries:
      - "Agent 卡住"
      - "工具调用失败"
      - "笔记很多但记不住"

  weixin:
    enabled: true
    mode: query
    fetch_limit: 50
    queries:
      - "Agent 卡住"
      - "工具调用失败"
      - "学了不会用"
    urls: []

  xiaohongshu:
    enabled: true
    mode: query
    fetch_limit: 100
    queries:
      - "AI 自动化 翻车"
      - "笔记很多但记不住"
    urls: []

  # 两主题轮换；意图轴（困难/替代/绕路/放弃/价值）只作写词指南，不落字段。
  exploration_topics:
    - id: agent_in_production
      label: Agent 实际落地
      queries_by_source:
        twitter:
          - '"agent stuck" OR "tool calling failed"'
        reddit:
          - "agent stuck"
          - "tool calling failed"
        weixin:
          - "Agent 卡住"
          - "工具调用失败"
        xiaohongshu:
          - "AI 自动化 翻车"
      github_users: []
    - id: ai_personal_empowerment
      label: AI 个人赋能
      queries_by_source:
        twitter:
          - '"AI notes remembering" OR "knowledge retrieval"'
        reddit:
          - "AI notes remembering"
          - "knowledge retrieval"
        weixin:
          - "笔记很多但记不住"
          - "学了不会用"
        xiaohongshu:
          - "笔记很多但记不住"
      github_users: []
```

Edit D — in `products.sources`, add task constraints to the wide launch queries. Replace `products.sources.twitter.queries` with:

```yaml
      queries:
        - '("I built" OR "just launched") (learning OR "knowledge management" OR agent)'
        - '("solo founder" OR bootstrapped) (agent OR learning)'
```

and `products.sources.reddit.queries` with:

```yaml
      queries:
        - "I built a side project for learning"
        - "launched my agent product"
        - "product validation feedback"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_settings.py tests/unit/test_source_query_plan.py -q`
Expected: PASS (settings load still valid; two topics present).

- [ ] **Step 5: Commit**

```bash
git add finch.example.yaml tests/unit/test_settings.py
git commit -m "config(sources): converge queries to two short task-focused themes"
```

---

### Task 6: Rewrite the runtime `finch.yaml` (local, not committed)

**Files:**
- Modify: `finch.yaml` (gitignored — do NOT `git add`)

**Interfaces:**
- Consumes: existing `finch.yaml` `sources` / `exploration_topics` / `products.sources` blocks.

- [ ] **Step 1: Converge `sources.exploration_topics` from 5 topics to 2**

Replace the 5-topic `exploration_topics` list (the `reliability_and_recovery` … `clear_writing_and_decision_review` blocks) with the same 2-topic list as Task 5 Edit C.

- [ ] **Step 2: Shorten the long fixed queries**

Replace `sources.twitter.queries` (4 long OR-queries), `sources.reddit.queries` (3 long), and the weixin/xiaohongshu long queries with the short ones from Task 5 Edits A/B/C. Keep `sources.twitter.mode: timeline` (queries stay as configured-but-skipped, which the report now surfaces).

- [ ] **Step 3: Add task constraints to `products.sources`**

Replace `products.sources.twitter.queries` and `products.sources.reddit.queries` with the constrained versions from Task 5 Edit D.

- [ ] **Step 4: Smoke-verify the report end-to-end**

Run: `uv run finch sources sync --all --json`
Expected: exit 0; JSON rows for all six sources; twitter row shows `"mode": "timeline"` with `per_query` containing `{"query": "timeline", "outcome": "ran"}` plus `skipped` entries for the configured queries; disabled sources (github, weixin, xiaohongshu) show `"state": "not_executed"` (or are absent if they return no run). No crash, no fabricated counts.

- [ ] **Step 5: No commit (gitignored file)**

`finch.yaml` stays local. Confirm with `git status --short` that `finch.yaml` is not listed.

---

## Self-Review Notes

- Spec section 3 (report mechanism) → Tasks 1-4. Spec section 4 (query redesign) → Tasks 5-6. Spec "明确不做" items (multi-mode, labels, lookback, run-history command, daily summary) are not introduced by any task.
- `ran_queries` / `skipped_queries` / `mode` / `started_at` / `per_query` names are identical across Tasks 2, 3, 4.
- `classify_result` returns only `failed/empty/success`; the `not_executed` state is synthesized at the CLI layer (Task 4) for sources absent from results — matching spec 3.2.
- No TBD/TODO; every code step is complete.
