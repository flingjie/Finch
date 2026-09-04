"""互动策略与草稿生成（执行计划 Phase 4）。

把已排序、过阈值的 ``ScoredPost`` 转成 ``InteractionCandidate``：
1. 用纯函数 ``choose_action`` 确定性选择动作（bookmark / observe_author / 草稿类，无 LLM）；
2. 对草稿类动作批量调用一次 Codex，生成有实质增量的草稿（draft + intent + source_summary
   + factual_risks）；
3. 用 ``max_bookmarks`` / ``max_reply_drafts`` 确定性封顶，LLM 返回再多也不会超限。

本轮仍只读：只产出提案，不做审批/执行（Phase 5）。互动轨道不携带作者个人证据，因此草稿
只允许提问或明确标注推测，禁止虚构案例/代码/实验。
"""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from pydantic import BaseModel, Field

from ..codex.runner import CodexRunner
from ..settings import EngagementSettings
from .models import ConversationScore, ExternalPost, InteractionAction, InteractionCandidate
from .scoring import ScoredPost

_PROMPT_PATH = Path("prompts/propose-engagement.md")

# 确定性动作选择阈值（choose_action 的唯一事实来源，可单元测试）。
_REPLY_MIN_DISCUSSABILITY = 0.60
_REPLY_MIN_NOVELTY = 0.50
_QUOTE_MIN_EVIDENCE = 0.60
_OBSERVE_MIN_RELATIONSHIP = 0.80
_BOOKMARK_MIN_RELEVANCE = 0.60


class ProposalItem(BaseModel):
    """单条草稿类提案的模型输出；不含 action（action 由 choose_action 确定性决定）。"""

    post_id: str
    draft: str
    intent: str
    source_summary: str
    factual_risks: list[str] = Field(default_factory=list)


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


def generate_proposals(
    runner: CodexRunner,
    scored: list[ScoredPost],
    engagement: EngagementSettings,
) -> list[InteractionCandidate]:
    """把排序后的 ``ScoredPost`` 转为 ``InteractionCandidate``（只读提案）。

    - 空输入 → 空输出，0 次 LLM 调用。
    - 动作由 ``choose_action`` 确定性决定；``total`` 低于 ``min_candidate_score`` 的帖子保守
      置为 ``IGNORE``（防御性重复门槛，正常流程已被 ``rank_candidates`` 过滤）。
    - ``max_reply_drafts`` 封顶草稿类动作数、``max_bookmarks`` 封顶 bookmark 数；超限帖子直接
      丢弃，无论 LLM 返回多少条草稿都不会超限。
    - 草稿类帖子一次性 batch 调用 LLM；模型漏掉或给出空草稿时保守丢弃该草稿候选（不伪造草稿）。
    """
    if not scored:
        return []

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
        prompt = _PROMPT_PATH.read_text().format(posts=_to_json_text(reply_posts))
        output = cast(ProposalBatchOutput, runner.run(prompt, ProposalBatchOutput))
        reply_ids = {p.id for p in reply_posts}
        for item in output.items:
            if item.post_id in reply_ids:
                drafts[item.post_id] = item

    # 4) 组装候选；草稿类若无对应草稿则丢弃。
    by_id = {sp.post.id: sp for sp in scored}
    candidates: list[InteractionCandidate] = []
    for post_id in keep_ids:
        sp = by_id[post_id]
        action = actions[post_id]
        if action in (InteractionAction.DRAFT_REPLY, InteractionAction.DRAFT_QUOTE):
            proposal = drafts.get(post_id)
            if proposal is None or not proposal.draft.strip():
                continue
            candidates.append(
                InteractionCandidate(
                    id=f"{sp.post.platform}:{sp.post.id}:{action.value}",
                    post=sp.post,
                    score=sp.score,
                    action=action,
                    draft=proposal.draft,
                    intent=proposal.intent,
                    source_summary=proposal.source_summary,
                    factual_risks=proposal.factual_risks,
                    approval_required=True,
                )
            )
        else:
            candidates.append(
                InteractionCandidate(
                    id=f"{sp.post.platform}:{sp.post.id}:{action.value}",
                    post=sp.post,
                    score=sp.score,
                    action=action,
                    approval_required=False,
                )
            )
    return candidates
