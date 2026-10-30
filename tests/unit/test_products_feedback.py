"""产品用户反馈研究单测：查询构造、归属过滤、去宣传、去重、摘录门禁、缓存。"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from pydantic import BaseModel

from finch.products.feedback import (
    FeedbackExtraction,
    FeedbackItemDraft,
    FeedbackPointDraft,
    build_feedback_queries,
    build_feedback_report,
    filter_feedback_candidates,
    research_feedback,
)
from finch.products.models import (
    EvidenceStatus,
    ExperienceLevel,
    Founder,
    FounderHandle,
    Product,
)
from finch.products.repository import FeedbackRepository
from finch.settings import FeedbackResearchSettings, ProductDiscoverySettings, Settings
from finch.sources.fingerprint import artifact_id, content_fingerprint
from finch.sources.models import AuthorIdentity, RawArtifact, Source
from finch.storage.workspace import Workspace


def _art(
    source: str,
    sid: str,
    url: str,
    text: str,
    author: str = "alice",
    platform: str | None = None,
) -> RawArtifact:
    return RawArtifact(
        artifact_id=artifact_id(source, "post", sid),
        source=Source(source),
        source_type="post",
        source_id=sid,
        canonical_url=url,
        author_identity=AuthorIdentity(
            platform=platform or ("github" if source == "github" else "reddit"),
            external_id=author,
            handle=author,
        ),
        text=text,
        retrieved_at=datetime.now(UTC),
        capture_method="adapter",
        content_fingerprint=content_fingerprint(text, url=url),
    )


def _product() -> Product:
    return Product(id="prod_x", key="github.com/acme/widget", name="Widget")


def test_build_feedback_queries_uses_name():
    q = build_feedback_queries(_product())
    assert Source.REDDIT in q and Source.TWITTER in q
    assert any("Widget" in x for x in q[Source.REDDIT])


def test_filter_attribution_promo_founder_and_dedupe():
    product = _product()
    product.founder = Founder(
        founder_id="f",
        display_name="acme",
        handles=[FounderHandle(platform="reddit", handle="acme")],
    )
    matching = _art("reddit", "1", "https://reddit.com/r/x/1", "Widget is great but has bugs")
    other = _art("reddit", "2", "https://reddit.com/r/x/2", "Totally unrelated product")
    founder = _art("reddit", "3", "https://reddit.com/r/x/3", "Widget helped me", author="acme")
    promo = _art("reddit", "4", "https://reddit.com/r/x/4", "We built Widget and it's amazing")
    dup = _art("reddit", "1", matching.canonical_url, matching.text)

    kept = filter_feedback_candidates([matching, other, founder, promo, dup], product)
    assert [a.source_id for a in kept] == ["1"]


def test_filter_github_repo_scoped_but_drops_founder():
    product = _product()
    product.founder = Founder(
        founder_id="f",
        display_name="acme",
        handles=[FounderHandle(platform="github", handle="acme")],
    )
    gh_user = _art("github", "1", "https://github.com/acme/widget/issues/1", "Crash on startup")
    gh_founder = _art("github", "2", "https://github.com/acme/widget/issues/2", "Fixing now", author="acme")
    kept = filter_feedback_candidates([gh_user, gh_founder], product)
    assert [a.source_id for a in kept] == ["1"]


def _candidate(text: str, url: str = "https://reddit.com/r/x/1") -> RawArtifact:
    return _art("reddit", "1", url, text)


def test_build_report_keeps_traceable_feedback():
    product = _product()
    cand = _candidate("I use Widget daily and it crashes on import")
    extraction = FeedbackExtraction(
        feedback=[
            FeedbackItemDraft(
                original_excerpt="I use Widget daily and it crashes on import",
                user_problem="crashes on import",
                experience_level="first_hand",
                evidence_status="explicit",
                product_match=True,
            )
        ]
    )
    report = build_feedback_report(extraction, product, [cand], version="v1", search_fingerprint="fp")
    assert not report.insufficient_feedback
    assert len(report.feedback) == 1
    f = report.feedback[0]
    assert f.source == Source.REDDIT
    assert f.source_url == cand.canonical_url
    assert f.experience_level == ExperienceLevel.FIRST_HAND
    assert f.evidence_status == EvidenceStatus.EXPLICIT
    assert f.feedback_id.startswith("fb_")
    assert report.sources == ["reddit"]


def test_build_report_drops_fabricated_and_downgrades_first_hand():
    product = _product()
    cand = _candidate("Widget crashes on import")
    extraction = FeedbackExtraction(
        feedback=[
            FeedbackItemDraft(original_excerpt="fabricated text not in candidate", product_match=True),
            FeedbackItemDraft(original_excerpt="Widget crashes on import", product_match=False),
            FeedbackItemDraft(
                original_excerpt="Widget crashes on import",
                experience_level="first_hand",  # 无第一手信号 → 降级 unknown
                product_match=True,
            ),
        ]
    )
    report = build_feedback_report(extraction, product, [cand], version="v1", search_fingerprint="fp")
    assert len(report.feedback) == 1
    assert report.feedback[0].experience_level == ExperienceLevel.UNKNOWN


def test_build_report_insufficient_when_no_feedback():
    product = _product()
    cand = _candidate("Widget crashes on import")
    report = build_feedback_report(
        FeedbackExtraction(feedback=[]), product, [cand], version="v1", search_fingerprint="fp"
    )
    assert report.insufficient_feedback is True
    assert report.feedback == []


def test_build_report_points_map_index_to_feedback_id():
    product = _product()
    cand = _candidate("Widget crashes on import")
    extraction = FeedbackExtraction(
        feedback=[
            FeedbackItemDraft(
                original_excerpt="Widget crashes on import",
                user_problem="crash",
                product_match=True,
            )
        ],
        recurring_problems=[FeedbackPointDraft(claim="import crash", feedback_refs=[0], status="explicit")],
    )
    report = build_feedback_report(extraction, product, [cand], version="v1", search_fingerprint="fp")
    assert report.recurring_problems[0].feedback_refs == [report.feedback[0].feedback_id]
    assert report.recurring_problems[0].count == 1
    assert report.recurring_problems[0].status == EvidenceStatus.EXPLICIT


def _settings(tmp_path) -> Settings:
    return Settings(
        products=ProductDiscoverySettings(
            feedback=FeedbackResearchSettings(sources=[Source.REDDIT], stale_hours=72)
        ),
        paths={"var_dir": str(tmp_path)},  # type: ignore[arg-type]
    )


class _FakeRunner:
    def __init__(self, extraction: FeedbackExtraction) -> None:
        self.extraction = extraction

    def run(self, prompt: str, output_model: type[BaseModel], *, timeout: float = 600.0):
        assert output_model is FeedbackExtraction
        return self.extraction


class _FakeOrchestrator:
    def __init__(self, artifacts: list[RawArtifact]) -> None:
        self.artifacts = artifacts
        self.calls = 0

    def sync_source(self, source, context, *, project_peers=True):
        self.calls += 1
        return SimpleNamespace(artifacts=self.artifacts)


class _FakeGithub:
    def fetch_repo_feedback(self, repo):
        from finch.sources.connectors.github import RepoFeedbackResult

        return RepoFeedbackResult(artifacts=[])


def test_research_feedback_full_flow_and_cache(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    repo = FeedbackRepository(ws)
    settings = _settings(tmp_path)
    product = _product()
    art = _candidate("I use Widget daily and it crashes on import")
    extraction = FeedbackExtraction(
        feedback=[
            FeedbackItemDraft(
                original_excerpt="I use Widget daily and it crashes on import",
                user_problem="crashes on import",
                experience_level="first_hand",
                product_match=True,
            )
        ]
    )
    orchestrator = _FakeOrchestrator([art])

    report = research_feedback(
        product,
        runner=_FakeRunner(extraction),
        repository=repo,
        settings=settings,
        orchestrator=orchestrator,
        github_connector=_FakeGithub(),
        force=False,
    )
    assert report is not None and not report.insufficient_feedback
    assert orchestrator.calls >= 1

    # 缓存命中：即使不传 orchestrator/runner 也复用已存报告（不重新搜索）。
    cached = research_feedback(
        product,
        runner=None,
        repository=repo,
        settings=settings,
        orchestrator=None,
        github_connector=None,
        force=False,
    )
    assert cached is not None
    assert cached.report_id == report.report_id


def test_feedback_repository_clear_raw(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    repo = FeedbackRepository(ws)
    repo.append_raw("prod_x", {"platform": "reddit", "url": "u1"})
    repo.append_raw("prod_x", {"platform": "reddit", "url": "u2"})
    assert len(repo.read_raw("prod_x")) == 2
    repo.clear_raw("prod_x")
    assert repo.read_raw("prod_x") == []
