"""交流机会选择：质量准入 + 多标签软多样性（确定性，无 LLM）。

Opportunity 是发现结果；本模块只做组合选择，不生成回复草稿。
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime

from finch.engagement.models import ExternalPost, NextAction, Opportunity, SuggestedMode
from finch.engagement.scoring import ScoredPost
from finch.peers.models import EvidenceStatus
from finch.peers.service import peer_id_for

_ASSESSMENT_VERSION = "1"

# Soft diversity targets — only applied when qualified candidates exist (never pad).
_MIN_CROSS_DOMAIN = 2
_MAX_OPEN_EXPLORE = 1

_CROSS_MARKERS = (
    "cross",
    "adjacent",
    "compensation",
    "saga",
    "distributed",
    "跨",
    "补偿",
    "机制",
)
_EXPLORE_MARKERS = ("explore", "open", "探索", "开放")

_RT_ONLY = re.compile(
    r"^\s*(rt\s*@|转发|reposted|reposted from)\b", re.IGNORECASE
)
_BIO_THIN = re.compile(
    r"^\s*(i'?m|i am|我是|building|founder|engineer|developer)\b.{0,80}$",
    re.IGNORECASE,
)


def content_fingerprint(content: str) -> str:
    """Normalize whitespace then sha256 — format-only edits do not change the fingerprint."""
    normalized = " ".join(content.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def opportunity_id_for(peer_id: str, source_ref: str, fingerprint: str) -> str:
    raw = f"{peer_id}|{source_ref}|{fingerprint[:16]}"
    return f"opp_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}"


def context_fingerprint(
    *,
    long_term: list[str],
    questions: list[str],
    explore: list[str],
    excluded: list[str],
    assessment_version: str = _ASSESSMENT_VERSION,
) -> str:
    parts = [
        ",".join(sorted(x.strip().casefold() for x in long_term if x.strip())),
        ",".join(sorted(x.strip().casefold() for x in questions if x.strip())),
        ",".join(sorted(x.strip().casefold() for x in explore if x.strip())),
        ",".join(sorted(x.strip().casefold() for x in excluded if x.strip())),
        assessment_version,
    ]
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def looks_like_practice_release(content: str) -> bool:
    """实践发布帖信号：避免仅因「发布」一词被排除。"""
    text = content.casefold()
    signals = (
        "实验",
        "复盘",
        "failure",
        "checkpoint",
        "replay",
        "实测",
        "代码",
        "diff",
        "补偿",
        "experiment",
        "postmortem",
    )
    return any(s in text for s in signals)


def infer_evidence_status(
    content: str,
    *,
    practical_evidence: float = 0.0,
) -> EvidenceStatus:
    """Infer practice-evidence status from post text + LLM practical_evidence dim."""
    text = content.strip()
    if not text or _RT_ONLY.match(text) or (
        len(text) < 80 and _BIO_THIN.match(text) and practical_evidence < 0.4
    ):
        return EvidenceStatus.PENDING_REVIEW
    if practical_evidence >= 0.55 or looks_like_practice_release(text):
        sourced_markers = (
            "http://",
            "https://",
            "github.com",
            "demo",
            "diff",
            "pr #",
            "issue #",
            "commit",
            "复现",
            "实测",
            "日志",
        )
        lower = text.casefold()
        if any(m in lower for m in sourced_markers):
            return EvidenceStatus.SOURCED
        return EvidenceStatus.AUTHOR_STATED
    if practical_evidence >= 0.35:
        return EvidenceStatus.AUTHOR_STATED
    return EvidenceStatus.PENDING_REVIEW


def assign_next_action(
    mode: SuggestedMode,
    *,
    has_practice: bool,
    time_budget: int = 20,
    prefer_case: bool = False,
) -> tuple[NextAction, int]:
    """Derive display next_action + estimated_minutes from mode and user practice.

    Never invents personal experience; busy budgets shrink try/repro toward ask/observe.
    """
    budget = max(1, time_budget)
    if mode == SuggestedMode.LEARN:
        return "observe", min(5, budget)
    if mode == SuggestedMode.INVESTIGATE:
        if budget <= 10:
            return "ask", min(10, budget)
        action: NextAction = "repro" if prefer_case else "try"
        return action, min(20, budget)
    # discuss
    if has_practice:
        if prefer_case:
            return "case", min(15, budget)
        return "reply", min(15, budget)
    return "ask", min(10, budget)


def _has_real_source(post: ExternalPost) -> bool:
    return bool(post.url and post.url.strip().startswith(("http://", "https://")))


def _concrete_why(why: str) -> bool:
    text = why.strip()
    if len(text) < 12:
        return False
    vague = {"relevant", "interesting", "related", "相关", "有意思", "不错"}
    return text.casefold() not in vague


def quality_gate(opp: Opportunity) -> bool:
    """质量准入：真实来源、具体理由；discuss 要有切入点，learn 要有值得了解的内容。"""
    if opp.post is None or not _has_real_source(opp.post):
        return False
    if not _concrete_why(opp.why_relevant):
        return False
    if opp.suggested_mode == SuggestedMode.DISCUSS and not opp.opening.strip():
        return False
    if opp.suggested_mode == SuggestedMode.LEARN and not (
        opp.why_relevant.strip() or opp.novelty_reason.strip()
    ):
        return False
    return True


def _is_cross_domain(opp: Opportunity) -> bool:
    blob = " ".join([*opp.topic_tags, opp.discovered_via, opp.why_relevant]).casefold()
    return any(m in blob for m in _CROSS_MARKERS)


def _is_open_explore(opp: Opportunity) -> bool:
    blob = " ".join([*opp.topic_tags, opp.discovered_via, opp.suggested_mode.value]).casefold()
    return any(m in blob for m in _EXPLORE_MARKERS) or opp.suggested_mode == SuggestedMode.LEARN


def scored_post_to_opportunity(
    scored: ScoredPost,
    *,
    why_relevant: str = "",
    opening: str = "",
    suggested_mode: SuggestedMode = SuggestedMode.DISCUSS,
    topic_tags: list[str] | None = None,
    role_tags: list[str] | None = None,
    novelty_reason: str = "",
    uncertainty: str = "",
    shared_problem: str = "",
    contribution_basis_refs: list[str] | None = None,
    next_action: NextAction | None = None,
    estimated_minutes: int | None = None,
    evidence_status: EvidenceStatus | str | None = None,
    complementarity: float = 0.0,
    discovered_via: str = "topic_search",
    assessed_at: datetime | None = None,
) -> Opportunity:
    post = scored.post
    peer_id = peer_id_for(post.platform, post.author_id)
    fp = content_fingerprint(post.content)
    source_ref = post.url or f"{post.platform}:{post.id}"
    excerpt = " ".join(post.content.split())[:240]
    why = why_relevant.strip() or (
        "; ".join(r for r in scored.score.reasons if r.strip())
        or "concrete overlap with current practice topic"
    )
    mode = suggested_mode
    open_text = opening.strip()
    if not open_text and mode == SuggestedMode.DISCUSS:
        if scored.score.discussability >= 0.55:
            open_text = f"Ask about the concrete claim: {excerpt[:100]}"
        else:
            mode = SuggestedMode.LEARN
    status = evidence_status
    if status is None:
        status = infer_evidence_status(
            post.content, practical_evidence=scored.score.practical_evidence
        )
    status_value = status.value if isinstance(status, EvidenceStatus) else str(status)
    uncertainty_text = uncertainty
    if status_value == EvidenceStatus.PENDING_REVIEW.value and not uncertainty_text.strip():
        uncertainty_text = "待了解：仅有简介或转发，实践证据不足"
    elif status_value == EvidenceStatus.AUTHOR_STATED.value and not uncertainty_text.strip():
        uncertainty_text = "作者自述实践，未见独立来源核验"
    return Opportunity(
        id=opportunity_id_for(peer_id, source_ref, fp),
        peer_id=peer_id,
        source_refs=[source_ref],
        source_excerpt=excerpt,
        content_fingerprint=fp,
        discovered_via=discovered_via,
        topic_tags=topic_tags or list(post.matched_topics),
        role_tags=role_tags or [],
        tags_inferred=True,
        why_relevant=why,
        opening=open_text,
        suggested_mode=mode,
        novelty_reason=novelty_reason or "",
        uncertainty=uncertainty_text,
        shared_problem=shared_problem.strip(),
        contribution_basis_refs=list(contribution_basis_refs or []),
        next_action=next_action,
        estimated_minutes=estimated_minutes,
        evidence_status=status_value,
        assessed_at=assessed_at or datetime.now(UTC),
        assessment_version=_ASSESSMENT_VERSION,
        score_total=scored.score.total,
        complementarity=complementarity,
        post=post,
    )


def select_opportunity_set(
    candidates: list[Opportunity],
    *,
    limit: int = 10,
    seen_fingerprints: set[str] | None = None,
    familiar_peer_ids: set[str] | None = None,
) -> list[Opportunity]:
    """选择有差异的机会组合。

    - 质量准入；同一作者每批一条主机会（其余来源挂 related_source_refs）
    - pending_review 不占核心名额，仅当合格池不足时补入并保留 uncertainty
    - 已呈现相同内容指纹默认抑制
    - 熟悉作者无内容增量时软降权
    - 软多样性：尽量 ≥2 跨领域、≤1 开放探索（仅有合格候选才满足，不凑数）
    - 同分按稳定 id 排序
    """
    seen_fp = seen_fingerprints or set()
    familiar = familiar_peer_ids or set()

    qualified = [c for c in candidates if quality_gate(c)]
    core = [
        c
        for c in qualified
        if c.evidence_status != EvidenceStatus.PENDING_REVIEW.value
    ]
    pending = [
        c
        for c in qualified
        if c.evidence_status == EvidenceStatus.PENDING_REVIEW.value
    ]
    # Prefer core; only use pending_review to fill if core underfills.
    pool = core if core else pending
    by_fp: dict[str, Opportunity] = {}
    for opp in pool:
        if opp.content_fingerprint in seen_fp:
            continue
        prev = by_fp.get(opp.content_fingerprint)
        if prev is None or (opp.score_total, opp.id) > (prev.score_total, prev.id):
            by_fp[opp.content_fingerprint] = opp
    unique = list(by_fp.values())

    def sort_key(opp: Opportunity) -> tuple:
        penalty = 0.0
        if opp.peer_id in familiar and not opp.novelty_reason.strip():
            penalty = 0.15
        return (-(opp.score_total - penalty), -(opp.complementarity), opp.id)

    unique.sort(key=sort_key)

    by_author: dict[str, Opportunity] = {}
    extras: dict[str, list[str]] = {}
    for opp in unique:
        if opp.peer_id not in by_author:
            by_author[opp.peer_id] = opp
        else:
            extras.setdefault(opp.peer_id, []).extend(opp.source_refs)
    for peer_id, refs in extras.items():
        primary = by_author[peer_id]
        related = list(dict.fromkeys([*primary.related_source_refs, *refs]))
        by_author[peer_id] = primary.model_copy(update={"related_source_refs": related})
    primaries = list(by_author.values())
    primaries.sort(key=sort_key)

    if limit <= 0:
        return []

    selected: list[Opportunity] = []
    cross_count = 0
    explore_count = 0

    for opp in primaries:
        if len(selected) >= limit:
            break
        if _is_open_explore(opp) and explore_count >= _MAX_OPEN_EXPLORE:
            continue
        selected.append(opp)
        if _is_cross_domain(opp):
            cross_count += 1
        if _is_open_explore(opp):
            explore_count += 1

    if cross_count < _MIN_CROSS_DOMAIN and len(selected) < limit:
        selected_ids = {o.id for o in selected}
        for opp in primaries:
            if len(selected) >= limit or cross_count >= _MIN_CROSS_DOMAIN:
                break
            if opp.id in selected_ids:
                continue
            if _is_cross_domain(opp):
                selected.append(opp)
                selected_ids.add(opp.id)
                cross_count += 1

    if len(selected) < min(limit, len(primaries)):
        selected_ids = {o.id for o in selected}
        for opp in primaries:
            if len(selected) >= limit:
                break
            if opp.id not in selected_ids:
                selected.append(opp)
                selected_ids.add(opp.id)

    if len(selected) < limit and core:
        pending_by_fp: dict[str, Opportunity] = {}
        for opp in pending:
            if opp.content_fingerprint in seen_fp:
                continue
            if any(o.peer_id == opp.peer_id for o in selected):
                continue
            prev = pending_by_fp.get(opp.content_fingerprint)
            if prev is None or (opp.score_total, opp.id) > (prev.score_total, prev.id):
                pending_by_fp[opp.content_fingerprint] = opp
        pending_list = sorted(pending_by_fp.values(), key=sort_key)
        selected_ids = {o.id for o in selected}
        for opp in pending_list:
            if len(selected) >= limit:
                break
            if opp.id in selected_ids:
                continue
            labeled = opp
            if not labeled.uncertainty.strip():
                labeled = opp.model_copy(
                    update={"uncertainty": "待了解：仅有简介或转发，实践证据不足"}
                )
            selected.append(labeled)
            selected_ids.add(opp.id)

    selected.sort(key=sort_key)
    return selected[:limit]


_AMBIGUOUS_EXCLUDE_TOKENS = frozenset({"发布", "release", "ship", "shipping"})
