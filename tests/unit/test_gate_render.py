from finch.evidence.models import ClaimConfidence, EvidenceCard
from finch.gate.models import InputRequest, ProposedPosition
from finch.gate.render import render_evidence, render_input_request, render_position_diff


def _request():
    return InputRequest(
        run_id="r1",
        job_id="j1",
        topic="topic here",
        why_now="why now here",
        proposed_position=ProposedPosition(claim="c", decision="d", tradeoff="t"),
        questions=["q1"],
    )


def test_render_input_request_contains_sections():
    text = render_input_request(_request(), [])
    assert "topic here" in text
    assert "why now here" in text
    assert "判断：c" in text
    assert "决策：d" in text
    assert "取舍：t" in text
    assert "q1" in text
    assert "请选择" in text


def test_render_input_request_empty_position():
    text = render_input_request(
        _request().model_copy(update={"proposed_position": ProposedPosition()}), []
    )
    assert "判断：(未填)" in text


def test_render_evidence():
    cards = [EvidenceCard(
        id="ev1", event_id="e", claim="the claim", sources=[],
        confidence=ClaimConfidence.VERIFIED, publishable=True, topics=["t"],
    )]
    assert "the claim" in render_evidence(cards)
    assert "无证据" in render_evidence([])


def test_render_position_diff_shows_change():
    before = ProposedPosition(claim="c", decision="d", tradeoff="t")
    after = ProposedPosition(claim="c2", decision="d", tradeoff="t")
    diff = render_position_diff(before, after)
    assert "-claim: c" in diff
    assert "+claim: c2" in diff
