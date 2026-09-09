from datetime import UTC, datetime

from finch.content.checkers.base import CheckResult
from finch.content.models import ClaimRef, Draft, DraftKind, RecommendedFormat
from finch.evidence.models import ClaimConfidence, EvidenceCard, Source
from finch.inbox.models import DecisionAction, DecisionRecord
from finch.learn.models import Feedback
from finch.storage.repositories import (
    CriticReportRepository,
    DecisionRecordRepository,
    DraftRepository,
    DraftVersionRepository,
    EvidenceRepository,
    FeedbackRepository,
)
from finch.storage.workspace import Workspace


def test_upsert_card_roundtrip(tmp_path):
    ws = Workspace(tmp_path)
    repo = EvidenceRepository(ws)
    card = EvidenceCard(
        id="ev_1", event_id="evt", claim="x",
        sources=[Source(type="commit", url="https://github.com/a/b/commit/1")],
        confidence=ClaimConfidence.VERIFIED, publishable=True, topics=["t"],
    )
    repo.upsert_card(card)
    got = repo.get_card("ev_1")
    assert got is not None
    assert got.claim == "x"
    card2 = card.model_copy(update={"claim": "y"})
    repo.upsert_card(card2)
    assert repo.get_card("ev_1").claim == "y"
    assert [c.id for c in repo.list_cards()] == ["ev_1"]


def test_upsert_cards_batch_roundtrip(tmp_path):
    ws = Workspace(tmp_path)
    repo = EvidenceRepository(ws)
    cards = [
        EvidenceCard(
            id=f"ev_{i}", event_id="evt", claim=f"c{i}",
            sources=[], confidence=ClaimConfidence.VERIFIED, publishable=True, topics=[],
        )
        for i in range(3)
    ]
    repo.upsert_cards(cards)
    assert [c.id for c in repo.list_cards()] == ["ev_0", "ev_1", "ev_2"]

    # 覆盖：同 id 批量更新而非重复
    updated = [c.model_copy(update={"claim": f"c{i}v2"}) for i, c in enumerate(cards)]
    repo.upsert_cards(updated)
    assert len(repo.list_cards()) == 3
    assert [c.claim for c in repo.list_cards()] == ["c0v2", "c1v2", "c2v2"]


def _draft():
    return Draft(id="d1", kind=DraftKind.REPLY, candidate_id="t1", body="hi",
                 claims=[ClaimRef(statement="x", evidence_card_id="ev_1",
                                  confidence=ClaimConfidence.VERIFIED)])


def test_draft_roundtrip(tmp_path):
    ws = Workspace(tmp_path)
    repo = DraftRepository(ws)
    repo.upsert_draft(_draft())
    assert repo.get_draft("d1") is not None
    assert repo.list_drafts()[0].id == "d1"


def test_feedback_roundtrip(tmp_path):
    ws = Workspace(tmp_path)
    repo = FeedbackRepository(ws)
    repo.save_feedback(Feedback(draft_id="d1", published_url="https://x.com/u/status/1",
                                recorded_at=datetime(2026, 1, 1)))
    assert repo.get_feedback("d1").published_url == "https://x.com/u/status/1"


def test_draft_version_roundtrip_ordering_and_idempotent_merge(tmp_path):
    ws = Workspace(tmp_path)
    repo = DraftVersionRepository(ws)
    repo.upsert_version("d1", 0, _draft().model_copy(update={"body": "v0"}))
    repo.upsert_version("d1", 1, _draft().model_copy(update={"body": "v1"}))
    assert [v.body for v in repo.list_versions("d1")] == ["v0", "v1"]
    # 幂等 merge：round 0 被覆盖而非重复
    repo.upsert_version("d1", 0, _draft().model_copy(update={"body": "v0-again"}))
    versions = repo.list_versions("d1")
    assert len(versions) == 2
    assert [v.body for v in versions] == ["v0-again", "v1"]


def test_critic_report_roundtrip(tmp_path):
    ws = Workspace(tmp_path)
    repo = CriticReportRepository(ws)
    repo.upsert_report(
        "d1",
        0,
        [
            CheckResult(
                checker="specificity",
                passed=False,
                severity="high",
                locations=["s[0]"],
                issues=["vague"],
                rewrite_instructions=["be specific"],
            )
        ],
        "rewrite",
    )
    repo.upsert_report(
        "d1", 1, [CheckResult(checker="specificity", passed=True, severity="low")], "pass"
    )
    reports = repo.list_reports("d1")
    assert [r["outcome"] for r in reports] == ["rewrite", "pass"]
    assert reports[0]["checks"][0]["checker"] == "specificity"
    assert reports[0]["checks"][0]["passed"] is False


def test_decision_record_repository_roundtrip(tmp_path):
    ws = Workspace(tmp_path)
    repo = DecisionRecordRepository(ws)
    rec = DecisionRecord(
        id="dec_j1", job_id="j1", draft_id="d1",
        action=DecisionAction.ACCEPT,
        approved_content_hash="h",
        decided_at=datetime.now(UTC),
    )
    repo.save(rec)
    assert repo.get("j1") is not None
    assert repo.get("j1").action == DecisionAction.ACCEPT
    assert len(repo.list()) == 1


def test_draft_repository_list_by_job(tmp_path):
    ws = Workspace(tmp_path)
    repo = DraftRepository(ws)
    repo.upsert_draft(Draft(id="d1", kind=DraftKind.ORIGINAL, body="a", content_job_id="j1"))
    repo.upsert_draft(Draft(id="d2", kind=DraftKind.ORIGINAL, body="b", content_job_id="j2"))
    assert [d.id for d in repo.list_by_job("j1")] == ["d1"]
    assert repo.list_by_job("nope") == []


def test_contentjob_find_by_generation_key(tmp_path):
    from finch.content.jobs import ContentJob, ContentJobStatus
    from finch.storage.repositories import ContentJobRepository

    ws = Workspace(tmp_path)
    repo = ContentJobRepository(ws)
    job = ContentJob(
        id="job_1",
        source_card_ids=["card_1"],
        candidate_id=None,
        reader_problem="Problem",
        author_position=None,
        recommended_format=RecommendedFormat.REPLY,
        status=ContentJobStatus.PROPOSED,
        origin="practice",
        generation_key="gk_123",
    )
    repo.upsert_job(job)

    found = repo.find_by_generation_key("gk_123")
    assert found is not None
    assert found.id == "job_1"
    assert found.origin == "practice"
    assert repo.find_by_generation_key("nope") is None


def test_list_jobs_skips_unparseable_legacy_rows(tmp_path):
    from finch.content.jobs import ContentJob, ContentJobStatus
    from finch.storage.repositories import ContentJobRepository

    ws = Workspace(tmp_path)
    repo = ContentJobRepository(ws)
    repo.upsert_job(
        ContentJob(
            id="job_ok",
            source_card_ids=[],
            reader_problem="p",
            author_position=None,
            recommended_format=RecommendedFormat.REPLY,
            status=ContentJobStatus.PROPOSED,
            core_message="m",
        )
    )
    # 模拟旧版行：payload 含不在枚举里的 status，无法解析为当前 ContentJob。
    legacy_path = ws.dir("ideas") / "job_tp_legacy.yaml"
    legacy_path.write_text("id: job_tp_legacy\nstatus: ready\n", encoding="utf-8")

    jobs = repo.list_jobs()
    assert [j.id for j in jobs] == ["job_ok"]
    assert repo.list_job_parse_failures() == ["job_tp_legacy"]
