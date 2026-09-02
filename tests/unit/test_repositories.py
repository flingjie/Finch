from finch.evidence.models import ClaimConfidence, EvidenceCard, Source
from finch.storage.database import Store
from finch.storage.repositories import EvidenceRepository


def test_upsert_card_roundtrip(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    repo = EvidenceRepository(store)
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
