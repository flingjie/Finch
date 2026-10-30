"""Discovery orchestrator：按源串行浏览器采集，落盘 raw → normalize → upsert → project。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from finch.sources.capabilities import fetch_capabilities
from finch.sources.connectors import DiscoveryContext, SourceConnector
from finch.sources.connectors.github import GitHubConnector
from finch.sources.connectors.reddit import RedditConnector
from finch.sources.connectors.twitter import TwitterConnector
from finch.sources.connectors.v2ex import V2exConnector
from finch.sources.connectors.weixin import WeixinConnector
from finch.sources.connectors.xiaohongshu import XiaohongshuConnector
from finch.sources.models import (
    OpenCliRequest,
    RawArtifact,
    ResultKind,
    Source,
    SourceStatus,
)
from finch.sources.opencli_gateway import OpenCliGateway
from finch.sources.projector import ArtifactProjector, ProjectResult
from finch.sources.store import ArtifactRepository, CursorStore, RawStore, RunRecorder
from finch.storage.workspace import Workspace


def default_connectors() -> dict[Source, SourceConnector]:
    return {
        Source.TWITTER: TwitterConnector(),
        Source.REDDIT: RedditConnector(),
        Source.GITHUB: GitHubConnector(),
        Source.V2EX: V2exConnector(),
        Source.WEIXIN: WeixinConnector(),
        Source.XIAOHONGSHU: XiaohongshuConnector(),
    }


_ERROR_KINDS = {
    ResultKind.BRIDGE_DOWN,
    ResultKind.TIMEOUT,
    ResultKind.AUTH_REQUIRED,
    ResultKind.CONFIG_ERROR,
    ResultKind.CANCELLED,
    ResultKind.UNAVAILABLE,
    ResultKind.BLOCKED,
    ResultKind.ERROR,
}

_BREAK_KINDS = {
    ResultKind.AUTH_REQUIRED,
    ResultKind.BRIDGE_DOWN,
    ResultKind.CONFIG_ERROR,
}


def _error_type(result: SyncResult) -> str:
    """Deterministic error label: specific ResultKind first, else non-READY status."""
    if result.kind in _ERROR_KINDS:
        return result.kind.value
    if result.status != SourceStatus.READY:
        return result.status.value
    return ""


@dataclass
class QueryOutcome:
    """单条查询/时间线的执行结果。"""

    query: str = ""
    outcome: str = ""  # ran | empty | failed | skipped
    raw_count: int = 0
    normalized_count: int = 0
    kind: ResultKind | None = None
    detail: str = ""
    partial: bool = False


def aggregate_state(result: SyncResult) -> str:
    """源级聚合状态：success / empty / partial_success / failed。

    优先聚合 per_query：任一 query failed 或 partial → 有 raw 为 partial_success、
    无 raw 为 failed。无 per_query 时沿用旧 classify 语义（kind 错误 / status 非 READY
    且有 detail 或空 raw → failed）。
    """
    has_failure = any(o.outcome == "failed" for o in result.per_query)
    has_partial = any(o.partial for o in result.per_query)
    if has_failure or has_partial:
        return "partial_success" if result.raw_count > 0 else "failed"
    if result.kind in _ERROR_KINDS:
        return "failed"
    if result.status != SourceStatus.READY and (result.detail or result.raw_count == 0):
        return "failed"
    return "success" if result.raw_count > 0 else "empty"


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
        "partial": o.partial,
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
        "projection_failed": result.projection_failed,
        "projection_error": result.projection_error,
        "mode": result.mode,
        "ran_queries": list(result.ran_queries),
        "skipped_queries": list(result.skipped_queries),
        "per_query": [_query_outcome_dict(o) for o in result.per_query],
        "started_at": result.started_at,
    }


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
    projection_failed: bool = False
    projection_error: str = ""


class DiscoveryOrchestrator:
    """调度多源只读采集；单源失败不阻塞其他源。"""

    def __init__(
        self,
        workspace: Workspace,
        *,
        gateway: OpenCliGateway | None = None,
        connectors: dict[Source, SourceConnector] | None = None,
        projector: ArtifactProjector | None = None,
    ) -> None:
        self.ws = workspace
        self.gateway = gateway or OpenCliGateway()
        self.connectors = connectors or default_connectors()
        self.raw_store = RawStore(workspace)
        self.artifacts = ArtifactRepository(workspace)
        self.cursors = CursorStore(workspace)
        self.runs = RunRecorder(workspace)
        self.projector = projector or ArtifactProjector(workspace)

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
                        detail=result.stderr_summary or "",
                        partial=bool(result.rows and result.stderr_summary),
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
            payload["projection_failed"] = proj.failed
            payload["projection_error"] = proj.error
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
                projection_failed=proj.failed,
                projection_error=proj.error,
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
            if result.kind in _ERROR_KINDS:
                detail = result.stderr_summary or result.kind.value
                per_query.append(
                    QueryOutcome(query=label, outcome="failed", kind=result.kind,
                                 detail=detail, raw_count=len(result.rows))
                )
                if result.kind in _BREAK_KINDS:
                    break
                continue
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
            per_query.extend(
                QueryOutcome(query=q, outcome="skipped")
                for q in ctx.configured_queries
            )

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
        payload["projection_failed"] = proj.failed
        payload["projection_error"] = proj.error
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
            projection_failed=proj.failed,
            projection_error=proj.error,
        )

    def _project(self, artifacts: list[RawArtifact]) -> ProjectResult:
        if not artifacts:
            return ProjectResult()
        try:
            return self.projector.project(artifacts)
        except Exception as exc:  # noqa: BLE001
            return ProjectResult(failed=True, error=str(exc)[:300])

    def sync_all(
        self,
        sources: list[Source] | None = None,
        context_by_source: dict[Source, DiscoveryContext] | None = None,
        *,
        project_peers: bool = True,
    ) -> list[SyncResult]:
        candidates = sources if sources is not None else list(self.connectors.keys())
        if context_by_source is not None:
            # 未启用/未配置的源不出现在 context_by_source 中，直接跳过（不是失败）。
            targets = [s for s in candidates if s in context_by_source]
        else:
            targets = list(candidates)
        results: list[SyncResult] = []
        for src in targets:
            ctx = (context_by_source or {}).get(src) or DiscoveryContext()
            started = time.perf_counter()
            try:
                result = self.sync_source(src, ctx, project_peers=project_peers)
            except Exception as exc:  # noqa: BLE001
                result = SyncResult(
                    source=src,
                    status=SourceStatus.DEGRADED,
                    detail=f"unhandled: {exc}"[:300],
                )
            result.elapsed_seconds = round(time.perf_counter() - started, 4)
            result.error_type = _error_type(result)
            results.append(result)
        return results

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

