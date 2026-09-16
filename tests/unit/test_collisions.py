"""Phase 4 collision / micro-experiment tests."""

from datetime import UTC, datetime, timedelta

from finch.collisions.models import (
    build_collision,
    complete_experiment,
    pick_weekly_collision,
    start_experiment,
)


class TestCollision:
    def test_requires_two_artifacts(self):
        assert (
            build_collision(
                their_domain="design",
                your_domain="agents",
                surface_similarity="both involve users",
                structural_similarity="progressive disclosure under uncertainty",
                shared_question="when to ask the user?",
                transferable_mechanism="progressive_disclosure",
                falsifiable_hypothesis="risk-gated confirms reduce noise",
                artifact_ids=["a1"],
            )
            is None
        )

    def test_rejects_surface_equals_structural(self):
        assert (
            build_collision(
                their_domain="a",
                your_domain="b",
                surface_similarity="same words",
                structural_similarity="same words",
                shared_question="q",
                transferable_mechanism="m",
                falsifiable_hypothesis="h",
                artifact_ids=["a1", "a2"],
            )
            is None
        )

    def test_builds_card(self):
        card = build_collision(
            their_domain="design tools",
            your_domain="agent engineering",
            surface_similarity="both involve user ops",
            structural_similarity="only interrupt on high uncertainty",
            shared_question="how to cut HITL cost?",
            transferable_mechanism="progressive_disclosure",
            falsifiable_hypothesis="risk-gated confirms reduce invalid confirms by 30%",
            artifact_ids=["a1", "a2"],
        )
        assert card is not None
        assert len(card.artifact_ids) == 2


class TestCollisionServiceSave:
    def test_generate_saves_card(self, tmp_path):
        from datetime import UTC, datetime

        from pydantic import BaseModel

        from finch.collisions.service import CollisionDraft, CollisionService
        from finch.collisions.repository import CollisionRepository
        from finch.peers.models import RelationshipStage
        from finch.peers.scoring import PersonScoreBreakdown
        from finch.peers.service import PeerService
        from finch.peers.shortlist import ShortlistCandidate, ShortlistItem, ShortlistSlot
        from finch.sources.fingerprint import artifact_id, content_fingerprint
        from finch.sources.models import AuthorIdentity, RawArtifact, Source
        from finch.sources.store import ArtifactRepository
        from finch.storage.workspace import Workspace

        ws = Workspace(tmp_path)
        ws.ensure()
        arts = []
        for sid, text in [("1", "design progressive disclosure"), ("2", "agent interrupt gates")]:
            url = f"https://x.com/u/status/{sid}"
            art = RawArtifact(
                artifact_id=artifact_id("twitter", "post", sid),
                source=Source.TWITTER,
                source_type="post",
                source_id=sid,
                canonical_url=url,
                author_identity=AuthorIdentity(
                    platform="x", external_id="u", handle="u"
                ),
                text=text,
                retrieved_at=datetime.now(UTC),
                capture_method="adapter",
                content_fingerprint=content_fingerprint(text, url=url),
            )
            ArtifactRepository(ws).upsert(art)
            arts.append(art)
        peer = PeerService().from_author(platform="x", author_id="u", username="u")
        peer = peer.model_copy(
            update={
                "source_refs": [a.artifact_id for a in arts],
                "person_id": "person_u",
                "relationship_stage": RelationshipStage.DISCOVERED,
            }
        )
        score = PersonScoreBreakdown(0.8, 0.8, 0.5, 0.5, 0.5, 0.5, 0.7, {})
        item = ShortlistItem(
            slot=ShortlistSlot.NEW_CREATOR,
            candidate=ShortlistCandidate(
                peer=peer,
                person_id="person_u",
                score=score,
                artifact_ids=[a.artifact_id for a in arts],
                platform="x",
            ),
        )

        class FakeRunner:
            def run(self, prompt: str, output_model: type[BaseModel], *, timeout: float = 600.0):
                return CollisionDraft(
                    their_domain="design",
                    your_domain="agents",
                    surface_similarity="both involve users",
                    structural_similarity="interrupt only on high uncertainty",
                    shared_question="when to ask?",
                    transferable_mechanism="progressive_disclosure",
                    falsifiable_hypothesis="risk gates cut noise 30%",
                    artifact_ids=[a.artifact_id for a in arts],
                )

        result = CollisionService(ws, runner=FakeRunner()).generate_from_shortlist(
            [item], your_domains=["agents"]
        )
        assert result.saved is not None
        assert CollisionRepository(ws).get(result.saved.collision_id) is not None


class TestExperiment:
    def test_one_week_due(self):
        card = build_collision(
            their_domain="a",
            your_domain="b",
            surface_similarity="s",
            structural_similarity="struct",
            shared_question="q",
            transferable_mechanism="m",
            falsifiable_hypothesis="if X then Y within 7d",
            artifact_ids=["a1", "a2"],
        )
        assert card is not None
        now = datetime(2026, 9, 16, tzinfo=UTC)
        exp = start_experiment(
            card,
            minimal_action="ship flag",
            observation_plan="count confirms",
            stop_condition="no change in 3 days",
            now=now,
        )
        assert exp.due_at == now + timedelta(days=7)
        assert exp.hypothesis == card.falsifiable_hypothesis
        done = complete_experiment(
            exp, outcome="no lift", result_kind="failure_review", stopped=False
        )
        assert done.status.value == "failed"

    def test_pick_weekly(self):
        c1 = build_collision(
            their_domain="a",
            your_domain="b",
            surface_similarity="s",
            structural_similarity="struct1",
            shared_question="q",
            transferable_mechanism="m",
            falsifiable_hypothesis="short",
            artifact_ids=["a1", "a2"],
        )
        c2 = build_collision(
            their_domain="a",
            your_domain="b",
            surface_similarity="s",
            structural_similarity="struct2",
            shared_question="q",
            transferable_mechanism="m",
            falsifiable_hypothesis="a much longer falsifiable hypothesis to prefer",
            artifact_ids=["a1", "a2", "a3"],
        )
        picked = pick_weekly_collision([c1, c2])  # type: ignore[list-item]
        assert picked is c2
