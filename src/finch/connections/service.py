"""连接闭环领域：关系复盘。

机会判断（``ConnectionOpportunity``）已迁移到 ``finch.opportunities``；本模块只保留
关系复盘（``review_relationship`` / ``apply_stage_upgrade``），供 ``finch connections
record`` 在用户登记真实互动后建议阶段升级（不自动发消息）。
"""

from pydantic import BaseModel, Field

from finch.peers.models import PeerProfile, RelationshipStage


class RelationshipReview(BaseModel):
    """关系复盘：是否有自然后续理由。"""

    peer_id: str
    person_id: str
    current_stage: RelationshipStage
    suggested_stage: RelationshipStage
    natural_next_reason: str = ""
    should_contact: bool = False
    dormant_reason: str = ""
    not_progress_signals: list[str] = Field(default_factory=list)


def review_relationship(
    peer: PeerProfile,
    *,
    person_id: str,
    bidirectional_exchanges: int = 0,
    started_experiment: bool = False,
    shared_visible_outcome: bool = False,
    natural_next_reason: str = "",
    stale_days: int = 0,
) -> RelationshipReview:
    """根据互动证据建议阶段；无新理由则不应联系。"""
    current = RelationshipStage.normalize(peer.relationship_stage)
    suggested = current
    not_progress: list[str] = []

    if bidirectional_exchanges >= 1 and current == RelationshipStage.DISCOVERED:
        suggested = RelationshipStage.ENGAGED
    if bidirectional_exchanges >= 2:
        suggested = RelationshipStage.RECURRING
    if started_experiment:
        suggested = RelationshipStage.PRACTICING
    if shared_visible_outcome:
        suggested = RelationshipStage.COLLABORATING

    should_contact = bool(natural_next_reason.strip())
    dormant_reason = ""
    if not should_contact and stale_days >= 30 and current not in {
        RelationshipStage.DISCOVERED,
        RelationshipStage.DORMANT,
    }:
        suggested = RelationshipStage.DORMANT
        dormant_reason = "无新的自然后续理由，暂转为 dormant"
        not_progress.append("time elapsed alone is not a reason to message")

    if not natural_next_reason.strip():
        not_progress.append("no new contribution or shared question")

    return RelationshipReview(
        peer_id=peer.id,
        person_id=person_id,
        current_stage=current,
        suggested_stage=suggested,
        natural_next_reason=natural_next_reason,
        should_contact=should_contact,
        dormant_reason=dormant_reason,
        not_progress_signals=not_progress,
    )


def apply_stage_upgrade(
    peer: PeerProfile, review: RelationshipReview
) -> PeerProfile:
    """仅当 review 建议更高/休眠阶段时更新；不自动发消息。"""
    return peer.model_copy(update={"relationship_stage": review.suggested_stage})
