from datetime import UTC, datetime

from finch.evidence.matcher import recall, token_overlap
from finch.evidence.models import ClaimConfidence, EvidenceCard
from finch.twitter.models import DiscussionCandidate


def _cand(id: str, text: str) -> DiscussionCandidate:
    return DiscussionCandidate(
        id=id, author_handle="u", text=text, url="https://x.com/u/status/"+id,
        published_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

def _card(id: str, claim: str, topics: list[str]) -> EvidenceCard:
    return EvidenceCard(
        id=id, event_id="e", claim=claim, sources=[],
        confidence=ClaimConfidence.VERIFIED, publishable=True, topics=topics,
    )

def test_overlap_jaccard():
    card = _card("ev", "token bucket rate limiting", ["rate-limit"])
    assert token_overlap("we need rate limiting in the agent loop", card) > 0
    assert token_overlap("completely unrelated cooking recipe", card) == 0

def test_recall_drops_no_overlap_and_respects_top_k():
    cards = [_card("ev1", "token bucket rate limiting", ["rate"])]
    cands = [
        _cand("a", "token bucket for tools"),
        _cand("b", "rate limiting the agent"),
        _cand("c", "banana muffin recipe"),
    ]
    out = recall(cands, cards, top_k=1)
    assert len(out) == 1
    assert out[0].candidate_id in {"a", "b"}
    assert out[0].card_ids == ["ev1"]
    assert "c" not in [x.candidate_id for x in recall(cands, cards, top_k=10)]
