from finch.content.models import ClaimRef, DailyBrief, Draft, DraftKind
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


def test_daily_brief_shape():
    b = DailyBrief(run_id="r", has_drafts=True, reply_count=1, body="# brief")
    assert b.reply_count == 1
