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
