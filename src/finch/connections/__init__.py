"""连接闭环领域：机会、回复草稿、关系复盘。"""

from finch.connections.service import (
    ConnectionDecision,
    ConnectionOpportunity,
    RelationshipReview,
    ReplyDraft,
    apply_stage_upgrade,
    build_connection_opportunity,
    craft_reply,
    review_relationship,
)

__all__ = [
    "ConnectionDecision",
    "ConnectionOpportunity",
    "ReplyDraft",
    "RelationshipReview",
    "apply_stage_upgrade",
    "build_connection_opportunity",
    "craft_reply",
    "review_relationship",
]
