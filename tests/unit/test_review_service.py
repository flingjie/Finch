from finch.content.models import ClaimRef, Draft, DraftKind
from finch.evidence.models import ClaimConfidence
from finch.review.feedback import FeedbackService
from finch.review.models import ReviewAction, SkipReason
from finch.review.service import ReviewService, compute_diff
from finch.storage.database import Store
from finch.storage.repositories import DraftRepository, FeedbackRepository, ReviewRepository


def _draft(id="d1", body="hi"):
    return Draft(id=id, kind=DraftKind.REPLY, candidate_id="t1", body=body,
                 claims=[ClaimRef(statement="x", evidence_card_id="ev_1",
                                  confidence=ClaimConfidence.VERIFIED)])


def _svc(tmp_path):
    store = Store(tmp_path / "db.sqlite"); store.init()
    return (ReviewService(DraftRepository(store), ReviewRepository(store)), store)


def test_compute_diff():
    diff = compute_diff("hello\nworld", "hello\nfinch")
    assert "-world" in diff and "+finch" in diff


def test_approve_revise_skip_replayable(tmp_path):
    svc, store = _svc(tmp_path)
    DraftRepository(store).upsert_draft(_draft())
    a = svc.approve("d1")
    assert a.action == ReviewAction.APPROVE
    # 可重放：重复 approve 不新增记录
    svc.approve("d1")
    assert len(ReviewRepository(store).list_reviews()) == 1
    # skip 覆盖
    s = svc.skip("d1", SkipReason.NOT_NOW)
    assert s.reason == "not_now"


def test_revise_saves_diff(tmp_path):
    svc, store = _svc(tmp_path)
    DraftRepository(store).upsert_draft(_draft(body="before"))
    r = svc.revise("d1", "after")
    assert r.revised_body == "after" and r.diff and "-before" in r.diff


def test_list_pending_excludes_reviewed(tmp_path):
    svc, store = _svc(tmp_path)
    repo = DraftRepository(store)
    repo.upsert_draft(_draft("d1")); repo.upsert_draft(_draft("d2"))
    svc.approve("d1")
    assert [d.id for d in svc.list_pending()] == ["d2"]
