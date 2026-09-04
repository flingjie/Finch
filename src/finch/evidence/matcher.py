"""Evidence 召回匹配（Phase 4 B1）：确定性 token overlap + Jaccard。"""

import re

from finch.evidence.models import EvidenceCard, RankedCandidate
from finch.twitter.models import DiscussionCandidate

_TOKEN_RE = re.compile(r"[a-z0-9]{3,}")


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


def _jaccard(candidate_tokens: set[str], card_tokens: set[str]) -> float:
    """预计算 token 集合的 Jaccard 相似度（0..1）；两边任一为空则 0。"""
    if not candidate_tokens or not card_tokens:
        return 0.0
    intersection = candidate_tokens & card_tokens
    if not intersection:
        return 0.0
    union = candidate_tokens | card_tokens
    return len(intersection) / len(union)


def _card_tokens(card: EvidenceCard) -> set[str]:
    """卡侧 token = claim 分词 ∪ {t.lower() for t in card.topics}。"""
    return _tokens(card.claim) | {t.lower() for t in card.topics}


def token_overlap(candidate_text: str, card: EvidenceCard) -> float:
    """candidate.text 与 card.claim+card.topics 的 Jaccard 相似度（0..1）。

    卡侧 token = claim 分词 ∪ {t.lower() for t in card.topics}；两边都空则 0。
    """
    return _jaccard(_tokens(candidate_text), _card_tokens(card))


def recall(
    candidates: list[DiscussionCandidate],
    cards: list[EvidenceCard],
    *,
    top_k: int,
) -> list[RankedCandidate]:
    """确定性召回：Jaccard>0 才保留 (candidate, card)；按最高 Jaccard 降序取 top_k。"""
    # 预计算卡侧 token，内层只做集合交并；候选 token 逐 candidate 计算（用各自 text，
    # 避免按 id 聚合导致重复 id 错用文本）。
    card_tokens_by_id = {card.id: _card_tokens(card) for card in cards}

    ranked: list[RankedCandidate] = []
    for candidate in candidates:
        cand_tokens = _tokens(candidate.text)
        overlaps: list[tuple[float, str]] = []
        for card in cards:
            score = _jaccard(cand_tokens, card_tokens_by_id[card.id])
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
