from finch.evidence.models import ClaimConfidence, EvidenceCard, Source
from finch.evidence.safety import scan_cards


def _card(**kw):
    base = dict(
        id="ev_1", event_id="evt", claim="added trajectory checks",
        sources=[Source(type="commit", url="https://github.com/flingjie/FDE-Gym/commit/abc123")],
        confidence=ClaimConfidence.VERIFIED, publishable=True, topics=[],
    )
    base.update(kw)
    return EvidenceCard(**base)


def test_secret_detected_in_claim():
    c = _card(claim="token ghp_abcdefghijklmnopqrstuvwxyz1234 leaked")
    r = scan_cards([c], repo_is_private={}, known_commit_urls={c.sources[0].url})
    assert r.hard_fail
    assert r.hits[0].code == "secret_detected"


def test_private_repo_or_unpublishable():
    c = _card(publishable=False)
    r = scan_cards([c], repo_is_private={}, known_commit_urls={c.sources[0].url})
    assert any(h.code == "private_repo_content" for h in r.hits)
    c2 = _card(publishable=True)
    r2 = scan_cards(
        [c2],
        repo_is_private={"flingjie/FDE-Gym": True},
        known_commit_urls={c2.sources[0].url},
    )
    assert any(h.code == "private_repo_content" for h in r2.hits)


def test_nonexistent_commit():
    c = _card()
    r = scan_cards([c], repo_is_private={}, known_commit_urls=set())
    assert any(h.code == "nonexistent_commit" for h in r.hits)


def test_clean_card_passes():
    c = _card()
    r = scan_cards(
        [c],
        repo_is_private={"flingjie/FDE-Gym": False},
        known_commit_urls={c.sources[0].url},
    )
    assert r.hard_fail is False
