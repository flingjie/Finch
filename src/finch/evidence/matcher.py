"""Evidence 召回匹配（Phase 4 B1）：确定性 token overlap + Jaccard。"""

import re

from finch.evidence.models import EvidenceCard, RankedCandidate
from finch.twitter.models import DiscussionCandidate

_TOKEN_RE = re.compile(r"[a-z0-9]{3,}")


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


def token_overlap(candidate_text: str, card: EvidenceCard) -> float:
    """candidate.text 与 card.claim+card.topics 的 Jaccard 相似度（0..1）。

    卡侧 token = claim 分词 ∪ {t.lower() for t in card.topics}；两边都空则 0。
    """
    candidate_tokens = _tokens(candidate_text)
    card_tokens = _tokens(card.claim) | {t.lower() for t in card.topics}
    if not candidate_tokens or not card_tokens:
        return 0.0
    intersection = candidate_tokens & card_tokens
    if not intersection:
        return 0.0
    union = candidate_tokens | card_tokens
    return len(intersection) / len(union)


def recall(
    candidates: list[DiscussionCandidate],
    cards: list[EvidenceCard],
    *,
    top_k: int,
) -> list[RankedCandidate]:
    """确定性召回：Jaccard>0 才保留 (candidate, card)；按最高 Jaccard 降序取 top_k。"""
    ranked: list[RankedCandidate] = []
    for candidate in candidates:
        overlaps: list[tuple[float, str]] = []
        for card in cards:
            score = token_overlap(candidate.text, card)
            if score > 0.0:
                overlaps.append((score, card.id))
        if not overlaps:
            continue
        overlaps.sort(key=lambda pair: pair[0], reverse=True)
        ranked.append(
            RankedCandidate(
                candidate_id=candidate.id,
                card_ids=[card_id for _, card_id in overlaps],
                recall_score=overlaps[0][0],
            )
        )
    ranked.sort(key=lambda item: item.recall_score, reverse=True)
    return ranked[:top_k]
