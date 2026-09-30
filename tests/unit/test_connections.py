"""Connection loop tests: relationship review (机会判断已迁移到 finch.opportunities)。"""

from finch.connections.service import apply_stage_upgrade, review_relationship
from finch.peers.models import RelationshipStage
from finch.peers.service import PeerService


def _peer():
    return PeerService().from_author(platform="x", author_id="alice", username="alice")


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
