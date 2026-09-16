"""Phase 2: Person identity, scoring, shortlist."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from finch.peers.models import PeerProfile, PlatformIdentity, RelationshipStage
from finch.peers.person import CreatorEvidence, CreatorEvidenceKind, IdentityConfidence
from finch.peers.person_service import PersonRepository, PersonService
from finch.peers.scoring import score_person
from finch.peers.service import PeerService
from finch.peers.shortlist import ShortlistCandidate, ShortlistSlot, select_daily_shortlist
from finch.storage.workspace import Workspace


def _peer(platform: str, author: str, stage=RelationshipStage.DISCOVERED) -> PeerProfile:
    return PeerService().from_author(platform=platform, author_id=author, username=author)


class TestIdentityNoAutoMerge:
    def test_username_alone_rejected(self, tmp_path):
        ws = Workspace(tmp_path)
        ws.ensure()
        svc = PersonService(PersonRepository(ws))
        peer = _peer("x", "alice")
        person = svc.ensure_from_peer(peer)
        updated, audit = svc.propose_link(
            person,
            platform="github",
            external_id="alice",
            handle="alice",
            mutual_profile_link=False,
            weak_signals=0,
        )
        assert audit is not None
        assert audit.action == "reject"
        assert len(updated.identities) == 1

    def test_mutual_link_confirmed(self, tmp_path):
        ws = Workspace(tmp_path)
        ws.ensure()
        svc = PersonService(PersonRepository(ws))
        peer = _peer("x", "alice")
        person = svc.ensure_from_peer(peer)
        updated, audit = svc.propose_link(
            person,
            platform="github",
            external_id="alice-dev",
            handle="alice",
            mutual_profile_link=True,
        )
        assert audit is not None
        assert audit.confidence == IdentityConfidence.CONFIRMED
        assert len(updated.identities) == 2

    def test_unlink_reversible(self, tmp_path):
        ws = Workspace(tmp_path)
        ws.ensure()
        svc = PersonService(PersonRepository(ws))
        peer = _peer("x", "bob")
        person = svc.ensure_from_peer(peer)
        person, _ = svc.propose_link(
            person,
            platform="github",
            external_id="bob-gh",
            mutual_profile_link=True,
        )
        person = svc.unlink(person, platform="github", external_id="bob-gh")
        assert len(person.identities) == 1


class TestScoring:
    def test_popularity_not_in_total(self):
        evs = [
            CreatorEvidence(
                evidence_id="e1",
                person_id="p1",
                artifact_id="twitter:post:1",
                kind=CreatorEvidenceKind.CREATION,
                claim="built a tool",
                support=["artifact"],
                first_hand=True,
                confidence=0.8,
            ),
            CreatorEvidence(
                evidence_id="e2",
                person_id="p1",
                artifact_id="twitter:post:2",
                kind=CreatorEvidenceKind.FIRST_HAND_EXPERIENCE,
                claim="shared failure data",
                support=["artifact"],
                first_hand=True,
                confidence=0.8,
            ),
        ]
        low = score_person(evs, popularity={"followers": 10})
        high = score_person(evs, popularity={"followers": 1_000_000})
        assert low.total == high.total
        assert high.popularity_context["followers"] == 1_000_000


class TestShortlist:
    def test_requires_two_artifacts(self):
        peer = _peer("x", "c1")
        from finch.peers.scoring import PersonScoreBreakdown

        score = PersonScoreBreakdown(1, 1, 1, 1, 1, 1, 1.0, {})
        c = ShortlistCandidate(
            peer=peer, person_id="p", score=score, artifact_ids=["a1"], platform="x"
        )
        assert select_daily_shortlist([c]) == []

    def test_three_slots(self):
        from finch.peers.scoring import PersonScoreBreakdown

        score = PersonScoreBreakdown(0.8, 0.8, 0.5, 0, 0.5, 0.8, 0.7, {})
        peers = []
        for i, (plat, stage) in enumerate(
            [
                ("x", RelationshipStage.DISCOVERED),
                ("reddit", RelationshipStage.DISCOVERED),
                ("github", RelationshipStage.ENGAGED),
            ]
        ):
            p = _peer(plat, f"u{i}", stage)
            p = p.model_copy(update={"relationship_stage": stage})
            peers.append(
                ShortlistCandidate(
                    peer=p,
                    person_id=f"person_{i}",
                    score=score,
                    artifact_ids=["a1", "a2"],
                    platform=plat,
                )
            )
        items = select_daily_shortlist(peers)
        assert 1 <= len(items) <= 3
        slots = {i.slot for i in items}
        assert ShortlistSlot.NEW_CREATOR in slots or ShortlistSlot.REPLY_OPPORTUNITY in slots

    def test_cooldown(self):
        from finch.peers.scoring import PersonScoreBreakdown

        score = PersonScoreBreakdown(1, 1, 1, 0, 0, 0, 0.8, {})
        peer = _peer("x", "cool")
        c = ShortlistCandidate(
            peer=peer,
            person_id="p",
            score=score,
            artifact_ids=["a1", "a2"],
            platform="x",
            last_shown_at=datetime.now(UTC) - timedelta(days=1),
        )
        assert select_daily_shortlist([c]) == []


class TestStageNormalize:
    def test_conversing_maps_to_recurring(self):
        p = PeerProfile(
            id="peer_x",
            platform_identities=[
                PlatformIdentity(platform="x", author_id="a", username="a")
            ],
            relationship_stage=RelationshipStage.CONVERSING,
        )
        assert p.relationship_stage == RelationshipStage.RECURRING
