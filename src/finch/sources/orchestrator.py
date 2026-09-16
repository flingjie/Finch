"""Discovery orchestrator：按源串行浏览器采集，落盘 raw → normalize → upsert → project。"""

from __future__ import annotations

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
from finch.sources.models import RawArtifact, ResultKind, Source, SourceStatus
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
    artifacts: list[RawArtifact] = field(default_factory=list)


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
    ) -> SyncResult:
        ctx = context or DiscoveryContext()
        connector = self.connectors.get(source)
        if connector is None:
            return SyncResult(
                source=source,
                status=SourceStatus.UNAVAILABLE,
                detail="no connector",
            )

        caps = fetch_capabilities(run_fn=self.gateway._run_fn, profile=self.gateway.profile)
        self.gateway.capability_snapshot_id = caps.snapshot_id
        status = connector.probe(caps)
        if status == SourceStatus.UNAVAILABLE:
            return SyncResult(source=source, status=status, detail="probe unavailable")

        run_id, run_dir = self.raw_store.begin_run(source)
        created = 0
        normalized: list[RawArtifact] = []
        raw_count = 0
        last_kind: ResultKind | None = None
        detail = ""

        # GitHub: special path via GhClient (creator depth: user/repos/commits/…)
        if source == Source.GITHUB and isinstance(connector, GitHubConnector):
            for q in ctx.queries or ["octocat"]:
                fetch = getattr(connector, "fetch_creator", None) or connector.fetch_user
                result = fetch(q)
                last_kind = result.kind
                raw_ref = self.raw_store.write_raw(
                    run_dir, q, result.rows or {"empty": True}
                )
                raw_count += len(result.rows)
                arts = connector.normalize(result)
                for art in arts:
                    art.raw_ref = raw_ref
                    _, was_new = self.artifacts.upsert(art)
                    if was_new:
                        created += 1
                    normalized.append(art)
            proj = self._project(normalized)
            payload = self._run_payload(
                run_id, source, caps.snapshot_id, raw_count, len(normalized), created, last_kind
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

        requests = connector.plan(ctx, caps)
        if (
            not requests
            and not normalized
            and (ctx.queries or ctx.urls)
            and status == SourceStatus.READY
        ):
            detail = "no matching commands in capability snapshot"
            status = SourceStatus.DEGRADED

        for req in requests:
            # github surface is not in opencli policy — skip gateway for non-opencli
            if req.surface == "github":
                continue
            try:
                result = self.gateway.run(req)
            except Exception as exc:  # noqa: BLE001
                detail = str(exc)[:300]
                last_kind = ResultKind.ERROR
                continue
            last_kind = result.kind
            if result.kind in {
                ResultKind.AUTH_REQUIRED,
                ResultKind.BRIDGE_DOWN,
                ResultKind.CONFIG_ERROR,
            }:
                detail = result.stderr_summary or result.kind.value
                # Stop this source but do not raise
                break
            raw_payload: Any = result.rows if result.rows else {"kind": result.kind.value}
            item_key = req.command + "_" + (req.args[0] if req.args else "x")
            raw_ref = self.raw_store.write_raw(run_dir, item_key[:80], raw_payload)
            raw_count += len(result.rows)
            arts = connector.normalize(result)
            for art in arts:
                art.raw_ref = raw_ref
                _, was_new = self.artifacts.upsert(art)
                if was_new:
                    created += 1
                normalized.append(art)

        if ctx.cursor:
            self.cursors.set(source, ctx.cursor)

        proj = self._project(normalized)
        payload = self._run_payload(
            run_id, source, caps.snapshot_id, raw_count, len(normalized), created, last_kind
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
        )

    def _project(self, artifacts: list[RawArtifact]) -> ProjectResult:
        if not artifacts:
            return ProjectResult()
        try:
            return self.projector.project(artifacts)
        except Exception:  # noqa: BLE001
            return ProjectResult()

    def sync_all(
        self,
        sources: list[Source] | None = None,
        context_by_source: dict[Source, DiscoveryContext] | None = None,
    ) -> list[SyncResult]:
        targets = sources or list(self.connectors.keys())
        results: list[SyncResult] = []
        for src in targets:
            ctx = (context_by_source or {}).get(src) or DiscoveryContext()
            try:
                results.append(self.sync_source(src, ctx))
            except Exception as exc:  # noqa: BLE001
                results.append(
                    SyncResult(
                        source=src,
                        status=SourceStatus.DEGRADED,
                        detail=f"unhandled: {exc}"[:300],
                    )
                )
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
    ) -> dict[str, Any]:
        return {
            "run_id": run_id,
            "source": source.value,
            "capability_snapshot_id": snapshot_id,
            "started_at": datetime.now(UTC).isoformat(),
            "raw_count": raw_count,
            "normalized_count": normalized_count,
            "created_count": created_count,
            "kind": kind.value if kind else None,
        }
