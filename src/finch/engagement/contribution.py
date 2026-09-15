"""Deterministic contribution check for reply outlines (Build-in-Public P1).

Decides whether a personal note / idea adds enough to a discussion post to
justify a reply outline. No LLM — token overlap + presence of facts/basis.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from finch.content.jobs import ContentJob
from finch.engagement.models import ExternalPost

_TOKEN = re.compile(r"[a-zA-Z0-9_\u4e00-\u9fff]{2,}")


def _tokens(text: str) -> set[str]:
    return {m.group(0).casefold() for m in _TOKEN.finditer(text or "")}


def assess_contribution(
    post: ExternalPost,
    *,
    facts: Sequence[str] = (),
    core_message: str = "",
    observation: str = "",
    interpretation: str = "",
    contribution_basis_refs: Sequence[str] = (),
) -> tuple[bool, str]:
    """Return ``(has_contribution, reason)``.

    No contribution when there are no facts, basis refs, or substantive personal
    text that overlaps the post. Overlap uses shared tokens (problem matching).
    """
    material_parts = [
        *(facts or []),
        core_message or "",
        observation or "",
        interpretation or "",
    ]
    material_text = " ".join(p for p in material_parts if p and p.strip())
    has_basis = bool(contribution_basis_refs) or bool(facts)
    if not material_text.strip() and not contribution_basis_refs:
        return False, "无可用实践事实或贡献依据，暂不回复"

    post_tokens = _tokens(post.content) | _tokens(" ".join(post.matched_topics))
    material_tokens = _tokens(material_text)
    overlap = post_tokens & material_tokens
    # Drop ultra-common English stop-ish tokens that appear in almost every post.
    overlap -= {
        "the",
        "and",
        "for",
        "you",
        "how",
        "what",
        "with",
        "this",
        "that",
        "have",
        "from",
        "are",
        "was",
        "your",
        "can",
        "do",
    }

    if overlap and (has_basis or core_message or observation or interpretation or facts):
        return True, f"实践与讨论共享主题词: {', '.join(sorted(overlap)[:5])}"
    if has_basis or material_text.strip():
        return False, "缺少有效增量：个人素材与帖子主题无交集，暂不回复"
    return False, "缺少有效增量：个人素材与帖子主题无交集，暂不回复"


def assess_job_contribution(post: ExternalPost, job: ContentJob) -> tuple[bool, str]:
    """Contribution check using a persisted ContentJob as the personal material."""
    refs = [job.id, *job.source_card_ids]
    return assess_contribution(
        post,
        facts=job.facts,
        core_message=job.core_message,
        observation=job.observation,
        interpretation=job.interpretation,
        contribution_basis_refs=refs if (job.facts or job.core_message) else [],
    )


def lived_claim_allowed(job: ContentJob | None) -> bool:
    """True only when the idea is marked observed (first-person practice OK)."""
    if job is None:
        return False
    return job.evidence_status == "observed" and bool(job.facts or job.observation)
