"""Phase 3 connection loop tests."""

from finch.connections.service import (
    ConnectionDecision,
    apply_stage_upgrade,
    build_connection_opportunity,
    craft_reply,
    review_relationship,
)
from finch.peers.models import RelationshipStage
from finch.peers.service import PeerService


def _peer():
    return PeerService().from_author(platform="x", author_id="alice", username="alice")


class TestConnectionOpportunity:
    def test_skip_without_user_evidence(self):
        opp = build_connection_opportunity(
            peer=_peer(),
            person_id="person_1",
            their_artifacts=["twitter:post:1"],
            their_summary="debugging evals",
            user_evidence_refs=[],
            user_contribution="",
        )
        assert opp.decision == ConnectionDecision.SKIP

    def test_connect_with_evidence(self):
        opp = build_connection_opportunity(
            peer=_peer(),
            person_id="person_1",
            their_artifacts=["twitter:post:1", "twitter:post:2"],
            their_summary="debugging evals",
            user_evidence_refs=["evidence:card:1"],
            user_contribution="我们把 discarded runs 单独记账",
        )
        assert opp.decision == ConnectionDecision.CONNECT
        assert opp.user_evidence_refs


class TestReplyCraft:
    def test_skip_praise_only(self):
        opp = build_connection_opportunity(
            peer=_peer(),
            person_id="p",
            their_artifacts=["a1", "a2"],
            their_summary="x",
            user_evidence_refs=["e1"],
            user_contribution="real",
        )
        draft = craft_reply(opp, observation="看到你写 discarded runs", user_experience="太棒了", question="")
        assert draft.decision == ConnectionDecision.SKIP

    def test_structured_draft(self):
        opp = build_connection_opportunity(
            peer=_peer(),
            person_id="p",
            their_artifacts=["a1", "a2"],
            their_summary="x",
            user_evidence_refs=["e1"],
            user_contribution="我们把 discarded runs 单独记账",
        )
        draft = craft_reply(
            opp,
            observation="你提到 discarded runs 不进成功率",
            user_experience="我们这边也把人工改写后的 run 标成 discard，成功率才稳住",
            question="你们 discard 的时间窗口是怎么定的？",
        )
        assert draft.decision == ConnectionDecision.CONNECT
        assert "discard" in draft.full_text.lower() or "Discard" in draft.full_text
        assert draft.user_evidence_refs == ["e1"]


class TestRelationshipReview:
    def test_upgrade_to_engaged(self):
        peer = _peer()
        review = review_relationship(
            peer,
            person_id="p",
            bidirectional_exchanges=1,
            natural_next_reason="they replied with a concrete question",
        )
        assert review.suggested_stage == RelationshipStage.ENGAGED
        assert review.should_contact is True
        updated = apply_stage_upgrade(peer, review)
        assert updated.relationship_stage == RelationshipStage.ENGAGED

    def test_no_contact_without_reason(self):
        peer = _peer().model_copy(update={"relationship_stage": RelationshipStage.ENGAGED})
        review = review_relationship(
            peer, person_id="p", bidirectional_exchanges=1, natural_next_reason=""
        )
        assert review.should_contact is False
        assert "no new contribution" in review.not_progress_signals[0]
