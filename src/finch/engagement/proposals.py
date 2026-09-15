"""互动策略与草稿/提纲生成（执行计划 Phase 4 + Build-in-Public P1）。

把已排序、过阈值的 ``ScoredPost`` 转成 ``InteractionProposal``：
1. 用纯函数 ``choose_action`` 确定性选择动作（bookmark / observe_author / 草稿类，无 LLM）；
2. 对草稿类动作批量调用一次 Codex：默认产出 outline（2–3 条）+ value_added；
   ``full_draft=True`` 时才填完整 ``draft``；
3. 用 ``max_bookmarks`` / ``max_reply_drafts`` 确定性封顶，LLM 返回再多也不会超限。

本轮仍只读：只产出提案，不做审批/执行。互动轨道不携带作者个人证据时，草稿
只允许提问或明确标注推测，禁止虚构案例/代码/实验。
"""

import hashlib
import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from pydantic import BaseModel, Field

from finch.content.checkers.safety import scan_secrets
from finch.content.jobs import ContentJob
from finch.peers.service import peer_id_for

from ..codex.runner import CodexRunner
from ..settings import EngagementSettings
from .contribution import lived_claim_allowed
from .models import ConversationScore, ExternalPost, InteractionAction, InteractionProposal
from .scoring import ScoredPost

_PROMPT_PATH = Path("prompts/propose-engagement.md")
_OUTLINE_PROMPT_PATH = Path("prompts/propose-engagement-outline.md")

# 提案 prompt 版本：generation_key 的一部分，升级 prompt 后旧 key 失效，允许重新生成。
_PROMPT_VERSION = "2"

# 确定性动作选择阈值（choose_action 的唯一事实来源，可单元测试）。
_REPLY_MIN_DISCUSSABILITY = 0.60
_REPLY_MIN_NOVELTY = 0.50
_QUOTE_MIN_EVIDENCE = 0.60
_OBSERVE_MIN_RELATIONSHIP = 0.80
_BOOKMARK_MIN_RELEVANCE = 0.60

_FABRICATED_EXPERIENCE = re.compile(
    r"(我测试过|我也遇到过|I(?:'| ha)?ve tested|I also (?:ran into|hit|saw))",
    re.IGNORECASE,
)


def context_version_for(
    *,
    practice_refs: Sequence[str] | None = None,
    current_questions: Sequence[str] | None = None,
) -> str:
    """Short fingerprint of user practice/questions for generation_key invalidation."""
    parts = [
        ",".join(sorted(x.strip().casefold() for x in (practice_refs or []) if x.strip())),
        ",".join(
            sorted(x.strip().casefold() for x in (current_questions or []) if x.strip())
        ),
    ]
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:12]


def generation_key_for(
    *,
    peer_id: str,
    post_id: str,
    action: InteractionAction,
    context_version: str = "",
) -> str:
    """幂等键 ``peer + source + action + prompt_version [+ context]``。"""
    base = f"{peer_id}:{post_id}:{action.value}:{_PROMPT_VERSION}"
    if context_version:
        return f"{base}:{context_version}"
    return base


def blocks_fabricated_experience(draft: str, *, has_basis: bool) -> bool:
    """True when draft claims personal experience without contribution_basis_refs."""
    if has_basis:
        return False
    return bool(_FABRICATED_EXPERIENCE.search(draft or ""))


def rewrite_fabricated_to_question(draft: str, opening: str = "") -> str:
    """Replace fabricated experience claims with a concrete question."""
    if opening.strip():
        return opening.strip()
    return (
        "想请教一下：你在实践里最先踩到的具体卡点是什么？"
        "我还没亲自跑过，想先对齐假设。"
    )


def ready_gate_blocks(
    *,
    body: str,
    job: ContentJob | None = None,
    has_basis: bool = False,
) -> list[str]:
    """Reasons a proposal/outline must not be treated as ready to publish.

    - Secrets in body → block.
    - Lived-experience claims without observed facts → block.
    """
    reasons: list[str] = []
    if scan_secrets(body):
        reasons.append("secret_detected")
    lived_ok = lived_claim_allowed(job) if job is not None else has_basis
    if blocks_fabricated_experience(body, has_basis=lived_ok):
        reasons.append("lived_claim_without_observed_facts")
    return reasons


class ProposalItem(BaseModel):
    """单条草稿类提案的模型输出；不含 action（action 由 choose_action 确定性决定）。"""

    post_id: str
    draft: str = ""
    intent: str = ""
    source_summary: str = ""
    factual_risks: list[str] = Field(default_factory=list)
    outline: str = ""
    value_added: str = ""


class ProposalBatchOutput(BaseModel):
    """一次 batch 草稿提案返回。"""

    items: list[ProposalItem]


def choose_action(score: ConversationScore) -> InteractionAction:
    """确定性动作选择（无 LLM）。

    规则（按优先级，阈值见模块常量）：
    1. 高可交流性 + 高观点增量 → 草稿类：实践证据强则 ``DRAFT_QUOTE``（值得带评论引用扩写），
       否则 ``DRAFT_REPLY``（提问/追问/指出假设）；
    2. 关系价值高但当前无可直接回复的切入点 → ``OBSERVE_AUTHOR``；
    3. 主题相关但讨论价值不高（低可交流性或偏资讯）→ ``BOOKMARK``；
    4. 其余 → ``IGNORE``（低于行动门槛）。
    """
    if (
        score.discussability >= _REPLY_MIN_DISCUSSABILITY
        and score.novelty >= _REPLY_MIN_NOVELTY
    ):
        return (
            InteractionAction.DRAFT_QUOTE
            if score.practical_evidence >= _QUOTE_MIN_EVIDENCE
            else InteractionAction.DRAFT_REPLY
        )
    if score.relationship_value >= _OBSERVE_MIN_RELATIONSHIP:
        return InteractionAction.OBSERVE_AUTHOR
    if score.relevance >= _BOOKMARK_MIN_RELEVANCE:
        return InteractionAction.BOOKMARK
    return InteractionAction.IGNORE


def _to_json_text(posts: Sequence[ExternalPost]) -> str:
    return json.dumps([p.model_dump(mode="json") for p in posts])


def _material_context(
    *,
    facts: Sequence[str] | None = None,
    core_message: str = "",
    interpretation: str = "",
) -> str:
    parts: list[str] = []
    if core_message.strip():
        parts.append(f"core_point: {core_message.strip()}")
    if facts:
        parts.append("facts:\n- " + "\n- ".join(f.strip() for f in facts if f.strip()))
    if interpretation.strip():
        parts.append(f"interpretation: {interpretation.strip()}")
    return "\n".join(parts) if parts else "(no personal material)"


def generate_proposals(
    runner: CodexRunner,
    scored: list[ScoredPost],
    engagement: EngagementSettings,
    *,
    context_version: str = "",
    contribution_basis_refs: Sequence[str] | None = None,
    full_draft: bool = True,
    facts: Sequence[str] | None = None,
    core_message: str = "",
    interpretation: str = "",
    job: ContentJob | None = None,
) -> list[InteractionProposal]:
    """把排序后的 ``ScoredPost`` 转为 ``InteractionProposal``（只读提案）。

    - 空输入 → 空输出，0 次 LLM 调用。
    - 动作由 ``choose_action`` 确定性决定；``total`` 低于 ``min_candidate_score`` 的帖子保守
      置为 ``IGNORE``（防御性重复门槛，正常流程已被 ``rank_candidates`` 过滤）。
    - ``max_reply_drafts`` 封顶草稿类动作数、``max_bookmarks`` 封顶 bookmark 数；超限帖子直接
      丢弃，无论 LLM 返回多少条草稿都不会超限。
    - ``full_draft=True``（库默认，兼容旧调用）填完整 draft；连接 CLI 传
      ``full_draft=False`` 时只保留 outline / value_added，``draft`` 为空。
    - 无实践依据时禁止「我测试过」类虚构亲历；命中则改写为提问。
    """
    if not scored:
        return []

    basis_refs = list(contribution_basis_refs or [])
    if job is not None and job.id not in basis_refs:
        basis_refs.append(job.id)
    # Lived claims need observed facts on the job; bare refs are not enough.
    if job is not None:
        has_basis = lived_claim_allowed(job)
    else:
        has_basis = bool(basis_refs)

    # 1) 确定性动作 + 门槛防御。
    actions: dict[str, InteractionAction] = {}
    for sp in scored:
        if sp.score.total < engagement.min_candidate_score:
            actions[sp.post.id] = InteractionAction.IGNORE
        else:
            actions[sp.post.id] = choose_action(sp.score)

    # 2) 按优先级封顶（scored 已是排序后的顺序）。
    bookmarks = 0
    replies = 0
    keep_ids: list[str] = []
    reply_posts: list[ExternalPost] = []
    for sp in scored:
        action = actions[sp.post.id]
        if action == InteractionAction.BOOKMARK:
            if bookmarks >= engagement.max_bookmarks:
                continue
            bookmarks += 1
            keep_ids.append(sp.post.id)
        elif action in (InteractionAction.DRAFT_REPLY, InteractionAction.DRAFT_QUOTE):
            if replies >= engagement.max_reply_drafts:
                continue
            replies += 1
            keep_ids.append(sp.post.id)
            reply_posts.append(sp.post)
        elif action == InteractionAction.OBSERVE_AUTHOR:
            keep_ids.append(sp.post.id)
        # IGNORE 不进入候选列表。

    # 3) 草稿类一次性 batch 调用 LLM。
    drafts: dict[str, ProposalItem] = {}
    if reply_posts:
        material = _material_context(
            facts=facts or (job.facts if job else None),
            core_message=core_message or (job.core_message if job else ""),
            interpretation=interpretation or (job.interpretation if job else ""),
        )
        if full_draft:
            prompt = _PROMPT_PATH.read_text().format(
                posts=_to_json_text(reply_posts),
                material=material,
            )
        else:
            prompt = _OUTLINE_PROMPT_PATH.read_text().format(
                posts=_to_json_text(reply_posts),
                material=material,
            )
        output = cast(ProposalBatchOutput, runner.run(prompt, ProposalBatchOutput))
        reply_ids = {p.id for p in reply_posts}
        for item in output.items:
            if item.post_id in reply_ids:
                drafts[item.post_id] = item

    # 4) 组装候选；草稿类若无对应提纲/草稿则丢弃。
    by_id = {sp.post.id: sp for sp in scored}
    candidates: list[InteractionProposal] = []
    for post_id in keep_ids:
        sp = by_id[post_id]
        action = actions[post_id]
        peer_id = peer_id_for(sp.post.platform, sp.post.author_id)
        generation_key = generation_key_for(
            peer_id=peer_id,
            post_id=sp.post.id,
            action=action,
            context_version=context_version,
        )
        if action in (InteractionAction.DRAFT_REPLY, InteractionAction.DRAFT_QUOTE):
            proposal = drafts.get(post_id)
            if proposal is None:
                continue
            outline = (proposal.outline or "").strip()
            draft = (proposal.draft or "").strip() if full_draft else ""
            if full_draft:
                if not draft:
                    continue
            elif not outline:
                # Fall back: if model only returned draft, treat first lines as outline.
                if draft:
                    outline = draft
                    draft = ""
                else:
                    continue
            body_for_check = draft or outline
            risks = list(proposal.factual_risks)
            if blocks_fabricated_experience(body_for_check, has_basis=has_basis):
                if full_draft:
                    draft = rewrite_fabricated_to_question(draft)
                    body_for_check = draft
                else:
                    outline = rewrite_fabricated_to_question(outline)
                    body_for_check = outline
                risks.append("rewrote fabricated personal experience to a question")
            gate = ready_gate_blocks(body=body_for_check, job=job, has_basis=has_basis)
            if gate:
                risks.extend(gate)
            value_added = (proposal.value_added or proposal.intent or "").strip()
            candidates.append(
                InteractionProposal(
                    id=f"{sp.post.platform}:{sp.post.id}:{action.value}",
                    post=sp.post,
                    score=sp.score,
                    action=action,
                    draft=draft or None,
                    intent=value_added or proposal.intent or None,
                    source_summary=proposal.source_summary,
                    factual_risks=risks,
                    approval_required=True,
                    peer_id=peer_id,
                    generation_key=generation_key,
                    outline=outline,
                    value_added=value_added,
                    contribution_basis_refs=list(basis_refs),
                )
            )
        else:
            candidates.append(
                InteractionProposal(
                    id=f"{sp.post.platform}:{sp.post.id}:{action.value}",
                    post=sp.post,
                    score=sp.score,
                    action=action,
                    approval_required=False,
                    peer_id=peer_id,
                    generation_key=generation_key,
                    contribution_basis_refs=list(basis_refs),
                )
            )
    return candidates
