"""产品发现主流程单测（无真实网络；fake sync + fake runner）。"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel

from finch.products.cards import ProductCardBatchOutput, ProductCardItem, ProductCardService
from finch.products.discovery import (
    DiscoveryRunReport,
    run_product_discovery,
    select_daily_products,
)
from finch.products.presentation import ProductPresentationRepository
from finch.products.repository import ProductRepository
from finch.settings import (
    ProductDiscoverySettings,
    Settings,
    SourcesSettings,
    SourceTwitterPlan,
)
from finch.sources.fingerprint import artifact_id, content_fingerprint
from finch.sources.models import (
    AuthorIdentity,
    RawArtifact,
    ResultKind,
    Source,
    SourceStatus,
)
from finch.sources.orchestrator import DiscoveryOrchestrator, QueryOutcome, SyncResult
from finch.storage.workspace import Workspace


def _art(sid: str, url: str, text: str) -> RawArtifact:
    return RawArtifact(
        artifact_id=artifact_id("twitter", "post", sid),
        source=Source.TWITTER,
        source_type="post",
        source_id=sid,
        canonical_url=url,
        author_identity=AuthorIdentity(platform="x", external_id="alice", handle="alice"),
        text=text,
        retrieved_at=datetime.now(UTC),
        capture_method="adapter",
        content_fingerprint=content_fingerprint(text, url=url),
    )


def _settings(tmp_path) -> Settings:
    settings = Settings(
        paths={"var_dir": tmp_path},  # type: ignore[arg-type]
        products=ProductDiscoverySettings(
            sources=SourcesSettings(
                twitter=SourceTwitterPlan(
                    enabled=True, mode="query", queries=["github.com has:links"]
                )
            )
        ),
    )
    settings.paths.var_dir = tmp_path
    return settings


def test_run_product_discovery_selects_and_snapshots(tmp_path, monkeypatch):
    ws = Workspace(tmp_path)
    ws.ensure()
    fresh = _art(
        "1",
        "https://github.com/acme/widget",
        "We launched widget, an agent harness for QA pain points and flaky test struggles",
    )
    ok = SyncResult(
        source=Source.TWITTER,
        status=SourceStatus.READY,
        raw_count=1,
        normalized_count=1,
        projected_count=0,
        kind=ResultKind.SUCCESS,
        artifacts=[fresh],
    )
    captured: dict[str, bool] = {}

    def fake_sync_all(self, sources=None, context_by_source=None, *, project_peers=True):
        captured["project_peers"] = project_peers
        return [ok]

    monkeypatch.setattr(DiscoveryOrchestrator, "sync_all", fake_sync_all)

    settings = _settings(tmp_path)
    result = run_product_discovery(settings, runner=None, skip_sync=False)

    assert captured["project_peers"] is False
    assert 1 <= len(result.products) <= 10
    assert result.snapshot is not None
    assert result.snapshot.product_ids == [p.id for p in result.products]
    # 不足 min_candidates(5) 时记录 shortfall。
    assert result.snapshot.shortfall.get("candidates") == 5 - len(result.products)
    # product discovery 不写旧 var/peers/。
    assert list((tmp_path / "peers").glob("*.yaml")) == []


class _FakeCardRunner:
    def __init__(self, key: str) -> None:
        self.key = key

    def run(self, prompt: str, output_model: type[BaseModel], *, timeout: float = 600.0):
        assert output_model is ProductCardBatchOutput
        return ProductCardBatchOutput(
            products=[
                ProductCardItem(
                    key=self.key,
                    name="Widget",
                    tagline="A QA agent harness",
                    founder_name="Alice",
                    target_user="QA engineers",
                    problem_solved="flaky tests",
                    core_solution="agent harness",
                    differentiation="deterministic gates",
                    progress="launched",
                    why_worth_attention="real first-hand build",
                )
            ]
        )


def test_card_service_fills_fields(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    repo = ProductRepository(ws)
    from finch.products.ids import product_id_for

    pid = product_id_for("github.com/acme/widget")
    from finch.products.models import Product

    repo.upsert(Product(id=pid, key="github.com/acme/widget"))

    svc = ProductCardService(ws, runner=_FakeCardRunner("github.com/acme/widget"))
    saved, failed = svc.fill(repo.list_all())
    assert saved == 1
    assert failed == 0
    filled = repo.get(pid)
    assert filled is not None
    assert filled.name == "Widget"
    assert filled.card_version == "product-card-batch-v1"


def test_select_daily_products_applies_cooldown(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    settings = _settings(tmp_path)
    repo = ProductRepository(ws)
    presentation = ProductPresentationRepository(ws)
    from finch.products.ids import product_id_for
    from finch.products.models import Product

    pid = product_id_for("github.com/acme/widget")
    repo.upsert(Product(id=pid, key="github.com/acme/widget", score_total=0.9))

    selected, shortfall = select_daily_products(
        repo.list_all(), settings=settings, presentation_repo=presentation
    )
    assert [p.id for p in selected] == [pid]

    presentation.record_shown(product_id=pid, surface="home", snapshot_id="snap1")
    selected, _ = select_daily_products(
        repo.list_all(), settings=settings, presentation_repo=presentation
    )
    assert selected == []  # 冷却期内不再出现


def test_run_product_discovery_builds_report(tmp_path, monkeypatch):
    ws = Workspace(tmp_path)
    ws.ensure()
    fresh = _art(
        "1",
        "https://github.com/acme/widget",
        "We launched widget, an agent harness for QA pain points and flaky test struggles",
    )
    ok = SyncResult(
        source=Source.TWITTER,
        status=SourceStatus.READY,
        raw_count=1,
        normalized_count=1,
        projected_count=0,
        kind=ResultKind.SUCCESS,
        artifacts=[fresh],
        per_query=[
            QueryOutcome(query="github.com has:links", outcome="ran", raw_count=1,
                         kind=ResultKind.SUCCESS)
        ],
    )
    monkeypatch.setattr(DiscoveryOrchestrator, "sync_all", lambda self, **kw: [ok])

    settings = _settings(tmp_path)
    result = run_product_discovery(settings, runner=_FakeCardRunner("github.com/acme/widget"))

    assert isinstance(result.report, DiscoveryRunReport)
    assert result.report.raw_count == 1
    assert result.report.normalized_count == 1
    assert result.report.product_count == len(result.products)
    assert result.report.card_fill_success == 1
    assert result.report.card_fill_failed == 0
    assert result.report.elapsed_seconds >= 0.0
    assert result.snapshot is not None
    assert result.snapshot.source_states == {"twitter": "success"}
    assert result.snapshot.card_fill_success == 1
    assert result.snapshot.card_fill_failed == 0
