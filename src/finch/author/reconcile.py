"""确定性发布匹配：把已批准草稿关联到线上帖子（P0）。"""

import difflib
from dataclasses import dataclass, field
from datetime import UTC, datetime

from finch.author.models import PublicationLink
from finch.storage.database import Store
from finch.storage.repositories import (
    AuthorPostRepository,
    PublicationIntentRepository,
    PublicationLinkRepository,
)


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def _ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


@dataclass
class ReconcileResult:
    linked: list[PublicationLink] = field(default_factory=list)
    needs_manual: list[dict] = field(default_factory=list)  # {source_id, candidates}
    awaiting: list[str] = field(default_factory=list)


def reconcile(store: Store, *, similarity_threshold: float = 0.85) -> ReconcileResult:
    intents = PublicationIntentRepository(store).list()
    links = PublicationLinkRepository(store).list()
    linked_sources = {link.source_id for link in links}
    posts = AuthorPostRepository(store).list()
    link_repo = PublicationLinkRepository(store)
    result = ReconcileResult()
    used_post_ids: set[str] = set()

    for intent in intents:
        if intent.source_id in linked_sources:
            continue
        candidates = [
            p for p in posts
            if p.kind in {"original", "quote"}
            and p.published_at >= intent.approved_at
            and p.author_account_id  # 账号过滤在调用方/后续按 user_id 精确化
            and p.remote_post_id not in used_post_ids
        ]
        # exact
        exact = [p for p in candidates if _normalize(p.body) == _normalize(intent.approved_body)]
        if len(exact) == 1:
            link = PublicationLink(
                source_id=intent.source_id, remote_post_id=exact[0].remote_post_id,
                matched_by="exact_text", confidence=1.0, linked_at=datetime.now(UTC),
            )
            link_repo.save(link)
            result.linked.append(link)
            used_post_ids.add(link.remote_post_id)
            continue
        if len(exact) > 1:
            result.needs_manual.append(
                {"source_id": intent.source_id, "candidates": [p.remote_post_id for p in exact]}
            )
            continue
        # similar (唯一候选)
        similar = [
            p for p in candidates
            if _ratio(p.body, intent.approved_body) >= similarity_threshold
        ]
        if len(similar) == 1:
            link = PublicationLink(
                source_id=intent.source_id, remote_post_id=similar[0].remote_post_id,
                matched_by="similar_text", confidence=_ratio(similar[0].body, intent.approved_body),
                linked_at=datetime.now(UTC),
            )
            link_repo.save(link)
            result.linked.append(link)
            used_post_ids.add(link.remote_post_id)
        elif len(similar) > 1:
            result.needs_manual.append(
                {"source_id": intent.source_id, "candidates": [p.remote_post_id for p in similar]}
            )
        else:
            result.awaiting.append(intent.source_id)

    return result
