from finch.content.claims import bind_claim, validate_draft
from finch.content.models import ClaimRef, Draft, DraftKind
from finch.evidence.models import ClaimConfidence


def test_bind_claim_rejects_out_of_set_card():
    assert bind_claim("x", "ev_999", ClaimConfidence.VERIFIED, card_ids={"ev_1"}) is None


def test_bind_claim_rejects_non_assertable():
    assert bind_claim("x", "ev_1", ClaimConfidence.INFERRED, card_ids={"ev_1"}) is None


def test_bind_claim_ok():
    c = bind_claim("x", "ev_1", ClaimConfidence.VERIFIED, card_ids={"ev_1"})
    assert c is not None and c.evidence_card_id == "ev_1"


def test_validate_draft_reports_violations():
    good = Draft(
        id="d",
        kind=DraftKind.REPLY,
        candidate_id="t",
        body="hi",
        claims=[
            ClaimRef(
                statement="x",
                evidence_card_id="ev_1",
                confidence=ClaimConfidence.SUPPORTED,
            )
        ],
    )
    assert validate_draft(good, card_ids={"ev_1"}) == []
    bad = Draft(
        id="d2",
        kind=DraftKind.REPLY,
        candidate_id="t",
        body="hi",
        claims=[
            ClaimRef(
                statement="x",
                evidence_card_id="ev_999",
                confidence=ClaimConfidence.INFERRED,
            )
        ],
    )
    assert len(validate_draft(bad, card_ids={"ev_1"})) >= 2
    empty = Draft(id="d3", kind=DraftKind.REPLY, candidate_id="t", body="hi", claims=[])
    assert validate_draft(empty, card_ids={"ev_1"})
