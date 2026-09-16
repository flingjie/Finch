"""创作者确定性基础分（热度不计分）。"""

from __future__ import annotations

from dataclasses import dataclass

from finch.peers.person import CreatorEvidence, CreatorEvidenceKind

# Spec §7.1 weights (sum = 100)
WEIGHTS = {
    "sustained_creation": 25,
    "first_hand": 25,
    "sharing_willingness": 20,
    "cross_domain": 15,
    "joint_practice": 10,
    "connection_opportunity": 5,
}


@dataclass(frozen=True)
class PersonScoreBreakdown:
    sustained_creation: float
    first_hand: float
    sharing_willingness: float
    cross_domain: float
    joint_practice: float
    connection_opportunity: float
    total: float
    popularity_context: dict  # likes/followers for context only — not in total


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, v))


def score_person(
    evidence: list[CreatorEvidence],
    *,
    has_reply_opening: bool = False,
    popularity: dict | None = None,
) -> PersonScoreBreakdown:
    """确定性基础分；粉丝/点赞仅进 popularity_context。"""
    kinds = {e.kind for e in evidence}
    creation_n = sum(1 for e in evidence if e.kind == CreatorEvidenceKind.CREATION)
    first_hand_n = sum(
        1 for e in evidence if e.kind == CreatorEvidenceKind.FIRST_HAND_EXPERIENCE and e.first_hand
    )
    share_n = sum(
        1
        for e in evidence
        if e.kind
        in {
            CreatorEvidenceKind.KNOWLEDGE_SHARING,
            CreatorEvidenceKind.CONVERSATION_BEHAVIOR,
        }
    )
    cross = CreatorEvidenceKind.CROSS_DOMAIN_BRIDGE in kinds
    marketing = CreatorEvidenceKind.MARKETING_OR_REPOST in kinds

    sustained = _clamp01(creation_n / 3.0)
    first_hand = _clamp01(first_hand_n / 2.0)
    sharing = _clamp01(share_n / 2.0)
    cross_domain = 1.0 if cross else 0.0
    joint = 0.5 if has_reply_opening else 0.0
    if has_reply_opening and first_hand_n > 0:
        opportunity = 1.0
    elif has_reply_opening:
        opportunity = 0.4
    else:
        opportunity = 0.0

    if marketing and creation_n == 0:
        sustained *= 0.3
        sharing *= 0.3

    total = (
        sustained * WEIGHTS["sustained_creation"]
        + first_hand * WEIGHTS["first_hand"]
        + sharing * WEIGHTS["sharing_willingness"]
        + cross_domain * WEIGHTS["cross_domain"]
        + joint * WEIGHTS["joint_practice"]
        + opportunity * WEIGHTS["connection_opportunity"]
    ) / 100.0

    return PersonScoreBreakdown(
        sustained_creation=sustained,
        first_hand=first_hand,
        sharing_willingness=sharing,
        cross_domain=cross_domain,
        joint_practice=joint,
        connection_opportunity=opportunity,
        total=_clamp01(total),
        popularity_context=dict(popularity or {}),
    )
