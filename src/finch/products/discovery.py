"""产品发现主流程：sources sync → 过滤 → 产品聚类 → 确定性打分 → 每日选择 → 卡片填充 → 快照。

复用 ``sources`` 编排与 ``discovery.filtering``，但把工件投影到产品（而非同行），且
``sync_all(project_peers=False)`` 不写 ``var/peers/``。空轮保持空（与 ``run_daily_discovery``
同规则，不回退历史）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from finch.codex.runner import CodexRunner
from finch.discovery.filtering import filter_artifacts
from finch.llm.base import StructuredInferenceRunner
from finch.products.cards import ProductCardService
from finch.products.inputs_repos import products_from_latest_repos_run
from finch.products.models import Product, ProductSnapshot
from finch.products.presentation import ProductPresentationRepository
from finch.products.projector import ProductCluster, ProductProjector
from finch.products.repository import ProductRepository, ProductSnapshotRepository
from finch.products.scoring import score_product
from finch.settings import Settings
from finch.sources.models import RawArtifact
from finch.sources.opencli_gateway import OpenCliGateway
from finch.sources.orchestrator import DiscoveryOrchestrator, SyncResult
from finch.sources.query_plan import build_context_by_source, build_discovery_plan
from finch.storage.workspace import Workspace


@dataclass
class ProductDiscoveryResult:
    run_id: str
    sync_results: list[SyncResult] = field(default_factory=list)
    products: list[Product] = field(default_factory=list)
    snapshot: ProductSnapshot | None = None
    detail: str = ""
    filtered: dict[str, int] = field(default_factory=dict)


def _merge_clusters(clusters: list[ProductCluster]) -> list[ProductCluster]:
    """按产品 id 去重，合并工件与证据。"""
    by_id: dict[str, ProductCluster] = {}
    for c in clusters:
        pid = c.product.id
        if pid not in by_id:
            by_id[pid] = c
            continue
        existing = by_id[pid]
        seen_artifacts = {a.artifact_id for a in existing.artifacts}
        for a in c.artifacts:
            if a.artifact_id not in seen_artifacts:
                existing.artifacts.append(a)
        seen_evidence = {e.source_ref for e in existing.product.evidence}
        existing.product.evidence = list(existing.product.evidence) + [
            e for e in c.product.evidence if e.source_ref not in seen_evidence
        ]
        existing.product.source_refs = list(
            dict.fromkeys([*existing.product.source_refs, *c.product.source_refs])
        )
    return list(by_id.values())


def select_daily_products(
    products: list[Product],
    *,
    settings: Settings,
    presentation_repo: ProductPresentationRepository,
    now: datetime | None = None,
) -> tuple[list[Product], dict[str, int]]:
    """确定性选出每日 5–10 个产品：按 id 去重 → 冷却 → (-score_total, id) 排序 → cap。"""
    clock = now or datetime.now(UTC)
    by_id: dict[str, Product] = {p.id: p for p in products}
    cutoff = clock - timedelta(days=settings.products.cooldown_days)

    eligible: list[Product] = []
    for p in by_id.values():
        last = presentation_repo.last_shown_at(p.id)
        if last is not None and last >= cutoff:
            continue
        eligible.append(p)

    eligible.sort(key=lambda p: (-p.score_total, p.id))
    selected = eligible[: settings.products.daily_limit]

    shortfall: dict[str, int] = {}
    if len(selected) < settings.products.min_candidates:
        shortfall["candidates"] = settings.products.min_candidates - len(selected)
    return selected, shortfall


def run_product_discovery(
    settings: Settings,
    *,
    runner: StructuredInferenceRunner | CodexRunner | None = None,
    gateway: OpenCliGateway | None = None,
    skip_sync: bool = False,
    lookback_hours: int | None = None,
    question: str | None = None,
) -> ProductDiscoveryResult:
    """跨平台抓取 → 过滤 → 产品聚类 → 打分 → 每日选择 → 卡片填充 → 快照。"""
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    now = datetime.now(UTC)
    run_id = f"products_{now.strftime('%Y%m%d%H%M%S')}"
    result = ProductDiscoveryResult(run_id=run_id)

    plan = build_discovery_plan(
        settings,
        lookback_hours=(
            lookback_hours if lookback_hours is not None else settings.products.lookback_hours
        ),
        intent=question,
        sources=settings.products.sources,
    )
    contexts = build_context_by_source(
        settings, all_sources=True, limit=20, sources=settings.products.sources
    )

    gw = gateway or OpenCliGateway(profile=settings.opencli.profile)
    orch = DiscoveryOrchestrator(ws, gateway=gw)
    if not skip_sync:
        result.sync_results = orch.sync_all(context_by_source=contexts, project_peers=False)

    artifacts: list[RawArtifact] = []
    for sr in result.sync_results:
        artifacts.extend(sr.artifacts)

    filtered, result.filtered = filter_artifacts(
        artifacts,
        excluded_content=list(plan.excluded_content),
        lookback_hours=plan.lookback_hours,
        as_of=now,
    )

    clusters = ProductProjector().build_product_candidates(filtered)
    if settings.products.repos_as_input and settings.repo_discovery.enabled:
        clusters.extend(products_from_latest_repos_run(ws))
    clusters = _merge_clusters(clusters)

    repo = ProductRepository(ws)
    products: list[Product] = []
    for c in clusters:
        p = c.product
        if c.artifacts:
            score = score_product(c.artifacts, now=now)
            p.score_total = score.total
            p.score_dimensions = {
                "launch_signal": score.launch_signal,
                "founder_build": score.founder_build,
                "problem_clarity": score.problem_clarity,
                "evidence_depth": score.evidence_depth,
                "recency": score.recency,
            }
        repo.upsert(p)
        products.append(p)

    presentation = ProductPresentationRepository(ws)
    selected, shortfall = select_daily_products(
        products, settings=settings, presentation_repo=presentation, now=now
    )

    if runner is not None:
        cards = ProductCardService(
            ws,
            runner=runner,
            max_candidates=settings.products.max_card_candidates,
            batch_size=settings.products.card_batch_size,
        )
        cards.fill(selected)
        # 卡片填充是 upsert 新对象；重读以拿到填好的卡片字段。
        selected = [repo.get(p.id) or p for p in selected]

    snapshot = ProductSnapshot(
        id=run_id,
        created_at=now,
        plan_id=plan.plan_id,
        context_fingerprint=plan.config_fingerprint,
        product_ids=[p.id for p in selected],
        shortfall=shortfall,
        source_coverage={
            sr.source.value: {
                "status": sr.status.value,
                "raw": sr.raw_count,
                "norm": sr.normalized_count,
            }
            for sr in result.sync_results
        },
    )
    ProductSnapshotRepository(ws).upsert(snapshot)

    result.products = selected
    result.snapshot = snapshot
    return result
