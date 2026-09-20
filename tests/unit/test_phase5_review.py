"""Phase 5 metrics and precipitation tests."""

from datetime import UTC, datetime, timedelta

from finch.collisions.models import (
    ExperimentStatus,
    build_collision,
    start_experiment,
)
from finch.collisions.precipitation import experiment_to_idea_seed
from finch.engagement.metrics import (
    active_cross_domain_relationships_30d,
    explain_recommendation_adjustments,
)
from finch.engagement.models import InteractionRecord, RecommendationFeedback, VerificationStatus
from finch.peers.service import PeerService


class TestNorthStarMetric:
    def test_counts_multi_touch_peers(self):
        now = datetime(2026, 9, 16, tzinfo=UTC)
        peer = PeerService().from_author(platform="x", author_id="a", username="a")
        recs = [
            InteractionRecord(
                id="r1",
                peer_id=peer.id,
                platform="x",
                source_url="u1",
                body="hello with substance",
                occurred_at=now - timedelta(days=2),
                direction="outbound",
                verification_status=VerificationStatus.USER_ATTESTED,
            ),
            InteractionRecord(
                id="r2",
                peer_id=peer.id,
                platform="x",
                source_url="u2",
                body="follow-up with substance",
                occurred_at=now - timedelta(days=1),
                direction="inbound",
                verification_status=VerificationStatus.USER_ATTESTED,
            ),
        ]
        n = active_cross_domain_relationships_30d(
            peers=[peer], interactions=recs, now=now
        )
        assert n == 1

    def test_ignores_empty_bodies(self):
        now = datetime(2026, 9, 16, tzinfo=UTC)
        peer = PeerService().from_author(platform="x", author_id="b", username="b")
        recs = [
            InteractionRecord(
                id="r1",
                peer_id=peer.id,
                platform="x",
                source_url="u1",
                body="",
                occurred_at=now,
                direction="outbound",
            ),
            InteractionRecord(
                id="r2",
                peer_id=peer.id,
                platform="x",
                source_url="u2",
                body="",
                occurred_at=now,
                direction="inbound",
            ),
        ]
        assert (
            active_cross_domain_relationships_30d(
                peers=[peer], interactions=recs, now=now
            )
            == 0
        )


class TestExplainableFeedback:
    def test_skip_heavy_suggests_tighten(self):
        now = datetime.now(UTC)
        fb = [
            RecommendationFeedback(
                id=f"f{i}",
                opportunity_id="o",
                snapshot_id="s",
                dimension="interest",
                value="unsuitable",
                created_at=now,
            )
            for i in range(4)
        ]
        adj = explain_recommendation_adjustments(fb)
        assert any("tighten" in a["effect"] for a in adj)

    def test_insufficient(self):
        adj = explain_recommendation_adjustments([])
        assert adj[0]["effect"] == "no change"


class TestPrecipitation:
    def test_experiment_seed_separates_layers(self):
        card = build_collision(
            their_domain="a",
            your_domain="b",
            surface_similarity="s",
            structural_similarity="struct",
            shared_question="q",
            transferable_mechanism="m",
            falsifiable_hypothesis="if X then Y",
            artifact_ids=["a1", "a2"],
        )
        assert card is not None
        exp = start_experiment(
            card,
            minimal_action="try flag",
            observation_plan="measure confirms",
            stop_condition="3 days no change",
        )
        exp = exp.model_copy(
            update={
                "status": ExperimentStatus.FAILED,
                "outcome": "no lift observed",
                "result_kind": "failure_review",
                "evidence_notes": ["day1 baseline", "day3 flat"],
            }
        )
        seed = experiment_to_idea_seed(exp)
        assert seed["experiment_fact"] == "no lift observed"
        assert "失败" in seed["personal_judgment"] or "边界" in seed["personal_judgment"]
        assert "开放" in seed["open_question"] or "假设" in seed["open_question"]
        assert exp.experiment_id in seed["source_refs"]
