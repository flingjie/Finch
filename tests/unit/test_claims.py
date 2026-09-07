from finch.content.claims import validate_draft
from finch.content.models import ClaimRef, Draft, DraftKind
from finch.evidence.models import ClaimConfidence


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
