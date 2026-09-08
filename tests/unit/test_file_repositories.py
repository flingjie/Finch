"""文件仓储行为契约：upsert/get 往返、幂等、二级查找、状态转换。"""

from datetime import UTC, datetime

from finch.author.models import PublicationIntent
from finch.content.jobs import ContentJob, ContentJobStatus
from finch.content.models import Draft, DraftKind
from finch.conversations.models import ConversationThread
from finch.engagement.models import (
    ConversationScore,
    ExternalPost,
    InteractionAction,
    InteractionProposal,
    InteractionRecord,
    InteractionStatus,
)
from finch.inbox.models import DecisionAction, DecisionRecord
from finch.peers.models import PeerProfile, PlatformIdentity
from finch.storage.repositories import (
    ContentJobRepository,
    ConversationThreadRepository,
    CriticReportRepository,
    DecisionRecordRepository,
    DraftRepository,
    InteractionRecordRepository,
    InteractionRepository,
    PeerRepository,
    PublicationIntentRepository,
)
from finch.storage.workspace import Workspace


def test_peer_upsert_get_idempotent(tmp_path):
    repo = PeerRepository(Workspace(tmp_path))
    p = PeerProfile(id="p1", platform_identities=[PlatformIdentity(platform="x", author_id="a")])
    repo.upsert(p)
    repo.upsert(p.model_copy(update={"display_name": "Alice"}))
    assert len(repo.list_all()) == 1
    assert repo.get("p1").display_name == "Alice"


def test_thread_list_by_peer(tmp_path):
    repo = ConversationThreadRepository(Workspace(tmp_path))
    repo.upsert(ConversationThread(id="t1", peer_id="p1", topic="x"))
    repo.upsert(ConversationThread(id="t2", peer_id="p2", topic="y"))
    assert [t.id for t in repo.list_by_peer("p1")] == ["t1"]


def test_content_job_find_by_generation_key(tmp_path):
    repo = ContentJobRepository(Workspace(tmp_path))
    job = ContentJob(
        id="idea_abc", source_card_ids=[], reader_problem="r", recommended_format="short_post",
        status=ContentJobStatus.PROPOSED, generation_key="gk1",
    )
    repo.upsert_job(job)
    assert repo.find_by_generation_key("gk1").id == "idea_abc"
    assert repo.find_by_generation_key("nope") is None


def test_interaction_approve_reject(tmp_path):
    repo = InteractionRepository(Workspace(tmp_path))
    post = ExternalPost(
        id="post1", platform="x", url="https://x.com/u/1", author_id="a", author_name="A",
        content="hello", published_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    score = ConversationScore(
        relevance=0.5, novelty=0.5, discussability=0.5, practical_evidence=0.5,
        relationship_value=0.5, total=0.5, reasons=[],
    )
    cand = InteractionProposal(
        id="c1", post=post, score=score, action=InteractionAction.DRAFT_REPLY,
        approval_required=True, status=InteractionStatus.PROPOSED, generation_key="g1",
    )
    repo.upsert(cand, run_id="run1")
    assert len(repo.list_pending()) == 1
    repo.approve("c1")
    assert repo.get("c1").status == InteractionStatus.APPROVED
    assert repo.list_pending() == []
    repo.reject("c1", "reason")
    assert repo.get("c1").status == InteractionStatus.REJECTED


def test_interaction_record_list_by_peer(tmp_path):
    repo = InteractionRecordRepository(Workspace(tmp_path))
    repo.upsert(InteractionRecord(
        id="rec_c1", proposal_id="c1", peer_id="p1", platform="x",
        source_url="u", published_body="b", occurred_at=datetime.now(UTC), outcome="published",
    ))
    assert [r.id for r in repo.list_by_peer("p1")] == ["rec_c1"]


def test_draft_frontmatter_roundtrip(tmp_path):
    repo = DraftRepository(Workspace(tmp_path))
    draft = Draft(
        id="draft_abc", kind=DraftKind.ORIGINAL, language="en",
        body="# Hi\n\n---\n\nBody.", content_job_id="idea_abc",
    )
    repo.upsert_draft(draft)
    got = repo.get_draft("draft_abc")
    assert got == draft
    assert [d.id for d in repo.list_by_job("idea_abc")] == ["draft_abc"]


def test_decision_get_by_job_id(tmp_path):
    repo = DecisionRecordRepository(Workspace(tmp_path))
    repo.save(DecisionRecord(
        id="dec_idea_abc", job_id="idea_abc", draft_id="draft_abc",
        action=DecisionAction.ACCEPT, approved_content_hash="h", decided_at=datetime.now(UTC),
    ))
    assert repo.get("idea_abc").id == "dec_idea_abc"


def test_publication_intent_get(tmp_path):
    repo = PublicationIntentRepository(Workspace(tmp_path))
    repo.save(PublicationIntent(
        source_type="draft", source_id="d1", approved_body="b", content_hash="h",
        approved_at=datetime.now(UTC), expected_kind="original",
    ))
    assert repo.get("d1").source_id == "d1"


def test_critic_report_same_round_idempotent(tmp_path):
    repo = CriticReportRepository(Workspace(tmp_path))
    repo.upsert_report("draft_1", 0, [], "pass")
    repo.upsert_report("draft_1", 0, [], "rewrite")
    reports = repo.list_reports("draft_1")
    assert len(reports) == 1
    assert reports[0]["outcome"] == "rewrite"
