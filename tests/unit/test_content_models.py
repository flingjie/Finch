from finch.content.models import ClaimRef, DailyBrief, Draft, DraftKind, DraftWarning
from finch.evidence.models import ClaimConfidence


def test_claim_ref_shape():
    c = ClaimRef(statement="x", evidence_card_id="ev_1", confidence=ClaimConfidence.VERIFIED)
    assert c.evidence_card_id == "ev_1"


def test_draft_reply_and_original():
    r = Draft(id="d1", kind=DraftKind.REPLY, candidate_id="t1", language="en",
              body="hi", claims=[ClaimRef(statement="x", evidence_card_id="ev_1",
                                          confidence=ClaimConfidence.VERIFIED)])
    o = Draft(id="d2", kind=DraftKind.ORIGINAL, language="zh", body="日记",
              claims=[])
    assert r.candidate_id == "t1"
    assert o.candidate_id is None


def test_draft_has_new_fields():
    """Test Draft model has the new C2 fields with proper defaults."""
    # Original draft without content_job_id
    draft = Draft(id="d1", kind=DraftKind.ORIGINAL, body="test body")
    assert draft.content_job_id is None
    assert draft.position_statement == ""
    assert draft.critic_report_id is None

    # Modern draft with all new fields
    modern = Draft(
        id="d2",
        kind=DraftKind.REPLY,
        candidate_id="c1",
        content_job_id="job_1",
        position_statement="We should use pooling",
        critic_report_id="critic_1",
        body="reply body",
    )
    assert modern.content_job_id == "job_1"
    assert modern.position_statement == "We should use pooling"
    assert modern.critic_report_id == "critic_1"


def test_daily_brief_shape():
    b = DailyBrief(run_id="r", has_drafts=True, reply_count=1, body="# brief")
    assert b.reply_count == 1


def test_draft_warning_binds_to_draft():
    """Task 3.4：DraftWarning 把警告归属到具体草稿，而非全局列表。"""
    w = DraftWarning(draft_id="d1", checker="evidence", message="unsupported claim")
    assert w.draft_id == "d1"
    assert w.checker == "evidence"
    assert w.message == "unsupported claim"
