"""KnowledgeService：可复用结论的确定性状态机（不调用 LLM）。"""

from __future__ import annotations

from datetime import UTC, datetime

from finch.knowledge.models import (
    KnowledgeBasis,
    KnowledgeCard,
    ReuseEvent,
    knowledge_id_for,
)
from finch.storage.repositories import KnowledgeCardRepository


def _dedup(items: list[str]) -> list[str]:
    """保序去重。"""
    return list(dict.fromkeys(items))


class KnowledgeService:
    """驱动知识卡生命周期；确定性、无 LLM、幂等。"""

    def __init__(self, cards: KnowledgeCardRepository) -> None:
        self.cards = cards

    def upsert(
        self,
        *,
        claim: str,
        basis: KnowledgeBasis,
        source_refs: list[str] | None = None,
        attempt_ids: list[str] | None = None,
        problem_ids: list[str] | None = None,
        applicability: str = "",
        limitations: str = "",
    ) -> KnowledgeCard:
        """新建（幂等）；已存在则合并追加引用，不覆盖已有 claim 正文。"""
        cid = knowledge_id_for(claim)
        existing = self.cards.get(cid)
        now = datetime.now(UTC)
        if existing is None:
            card = KnowledgeCard(
                id=cid,
                claim=claim.strip(),
                basis=basis,
                source_refs=list(source_refs or []),
                attempt_ids=list(attempt_ids or []),
                problem_ids=list(problem_ids or []),
                applicability=applicability,
                limitations=limitations,
                created_at=now,
                updated_at=now,
            )
            self.cards.upsert(card)
            return card
        merged = existing.model_copy(
            update={
                "source_refs": _dedup([*existing.source_refs, *(source_refs or [])]),
                "attempt_ids": _dedup([*existing.attempt_ids, *(attempt_ids or [])]),
                "problem_ids": _dedup([*existing.problem_ids, *(problem_ids or [])]),
                "applicability": applicability or existing.applicability,
                "limitations": limitations or existing.limitations,
                "updated_at": now,
            }
        )
        self.cards.upsert(merged)
        return merged

    def list_all(self) -> list[KnowledgeCard]:
        return self.cards.list_all()

    def show(self, card_id: str) -> KnowledgeCard:
        card = self.cards.get(card_id)
        if card is None:
            raise KeyError(card_id)
        return card

    def record_reuse(self, card_id: str, *, problem_id: str, note: str = "") -> KnowledgeCard:
        """append 一次实际复用事件；搜索/曝光不算复用。"""
        card = self.show(card_id)
        event = ReuseEvent(problem_id=problem_id, note=note, created_at=datetime.now(UTC))
        card = card.model_copy(
            update={
                "reuse_events": [*card.reuse_events, event],
                "updated_at": datetime.now(UTC),
            }
        )
        self.cards.upsert(card)
        return card

    def search(self, query: str) -> list[KnowledgeCard]:
        """确定性文本匹配（claim/applicability/limitations 子串，大小写不敏感）。"""
        needle = query.strip().casefold()
        if not needle:
            return []
        out: list[KnowledgeCard] = []
        for card in self.cards.list_all():
            blob = "\n".join([card.claim, card.applicability, card.limitations]).casefold()
            if needle in blob:
                out.append(card)
        return out
