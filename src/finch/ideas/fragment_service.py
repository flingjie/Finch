"""FragmentService：把用户片段 / 已验证 ConversationEvidence 结构化为一 IdeaCandidate。

（idea-discovery 的 user / conversation 来源；opportunity 来源在 Task 4 加入。）

本模块是纯领域逻辑：不访问 DB、不落库。``IdeaService.create_candidate`` 负责后续幂等落库。
"""

import hashlib
import re
from typing import Literal, cast

from pydantic import BaseModel, Field

from finch.content.jobs import AuthorPosition
from finch.engagement.models import ConversationEvidence
from finch.ideas.models import IdeaBoundaries, IdeaCandidate, IdeaGenerator, SourceRef
from finch.ideas.opportunity import Opportunity
from finch.llm.base import StructuredInferenceRunner

_GENERATOR_SKILL = "idea-discovery"
_GENERATOR_VERSION = "1.0.0"

# 第一人称代词（英文 + 中文）：外部作者亲历不得被采纳为作者亲历，提炼时剥离。
_FIRST_PERSON_RE = re.compile(
    r"\b(i|i'm|i've|i'd|i'll|we|we're|we've|we'd|we'll|my|our|mine|ours|me|us|"
    r"myself|ourselves)\b",
    re.IGNORECASE,
)
_CN_FIRST_PERSON_RE = re.compile(r"(我|我们|我的|我们的|咱|咱们)")


def _neutralize(text: str) -> str:
    """剥离第一人称代词，得到中性的问题/主张陈述（不采纳外部亲历）。"""
    neutral = _FIRST_PERSON_RE.sub(" ", text)
    neutral = _CN_FIRST_PERSON_RE.sub(" ", neutral)
    return " ".join(neutral.split())

_IDEA_DRAFT_PROMPT = """\
You turn a raw fragment (or a verified conversation signal) into a single publishable
engineering Idea candidate. Return JSON matching the schema.

Rules:
- core_point is ONE central claim. Split multiple claims into multiple candidates.
- observation states what was actually observed; do not invent personal experience.
- intent is "stance" (you have a clear position) or "exploration" (open-ended, no
  complete conclusion yet). Choose "exploration" when there is no complete conclusion.
- author_position carries claim / decision / tradeoff; mark nothing as confirmed.
- boundaries: known (verified), inferred (with hedging), unknown (do not assert).
- recommended_format: original | reply | thread.

## Source type

{source_type}

## Source text

{source_text}
"""


_FROM_OPPORTUNITY_PROMPT = """\
You turn a scouted conversation opportunity into a single Idea candidate. The opportunity
came from external public discussion, so it must stay external: never write the external
author's experience as the user's own first-person experience.

Rules:
- boundaries.known must be empty (external signal, not verified personal evidence).
- Put the external signal (third-person, no first-person) into boundaries.inferred.

## Opportunity
{opportunity}

Return JSON matching the same schema as before (core_point / observation / reader_problem /
why_worth_saying / intent / open_question / author_position / boundaries / recommended_format).
"""


def _to_candidate(
    out: "IdeaDraftOutput",
    *,
    origin: Literal["commit", "search", "user", "conversation"],
    source_refs: list[SourceRef],
) -> IdeaCandidate:
    # 注意：此处的 ``id`` 只是候选自带的展示性 id；真正的 ``ContentJob.id`` 由
    # ``IdeaService.create_candidate`` 按同一公式（sha256(core_point)）重算并落库，
    # 因此这里的 id 公式与 create_candidate 必须保持一致（不要单独改动其一）。
    core = out.core_point
    return IdeaCandidate(
        id=f"idea_{hashlib.sha256(core.encode('utf-8')).hexdigest()[:8]}",
        origin=origin,
        core_point=core,
        observation=out.observation,
        reader_problem=out.reader_problem,
        why_worth_saying=out.why_worth_saying,
        intent=out.intent,
        open_question=out.open_question,
        author_position=out.author_position,
        source_refs=source_refs,
        boundaries=out.boundaries,
        recommended_format=out.recommended_format,
        generator=IdeaGenerator(skill=_GENERATOR_SKILL, version=_GENERATOR_VERSION),
    )


def _sanitize_external(out: "IdeaDraftOutput", signal: str) -> "IdeaDraftOutput":
    """外部机会信号 → 确定性中性化（外部帖 ≠ 个人证据，不信任 LLM 边界）。

    剥离作者字段（core_point/observation/reader_problem/open_question + 立场的
    claim/decision/tradeoff）里的第一人称，并把 boundaries 强制为 known 空、
    inferred 承载中性化信号。原文只保留在 source_refs（外部、可追溯）。
    """
    position = out.author_position
    return out.model_copy(
        update={
            "core_point": _neutralize(out.core_point),
            "observation": _neutralize(out.observation),
            "reader_problem": _neutralize(out.reader_problem),
            "why_worth_saying": _neutralize(out.why_worth_saying),
            "open_question": _neutralize(out.open_question),
            "author_position": AuthorPosition(
                claim=_neutralize(position.claim),
                decision=_neutralize(position.decision),
                tradeoff=_neutralize(position.tradeoff),
                change_mind_if=position.change_mind_if,
            ),
            "boundaries": IdeaBoundaries(
                known=[],
                inferred=[_neutralize(signal)],
                unknown=out.boundaries.unknown,
            ),
        }
    )


class IdeaDraftOutput(BaseModel):
    """LLM 结构化输出：IdeaCandidate 中非 id/origin/source_refs/generator 的部分。"""

    core_point: str
    observation: str = ""
    reader_problem: str
    why_worth_saying: str
    intent: Literal["stance", "exploration"] = "stance"
    open_question: str = ""
    author_position: AuthorPosition
    boundaries: IdeaBoundaries = Field(default_factory=IdeaBoundaries)
    recommended_format: Literal["original", "reply", "thread"] = "original"


class FragmentService:
    """把用户输入 / ConversationEvidence 提炼为 IdeaCandidate（纯领域逻辑）。"""

    def __init__(self, runner: StructuredInferenceRunner) -> None:
        self.runner = runner

    def from_text(self, text: str) -> IdeaCandidate:
        """用户片段（一句话/模糊判断）→ IdeaCandidate，origin=user，无外部 source_refs。"""
        out = cast(
            IdeaDraftOutput,
            self.runner.run(
                _IDEA_DRAFT_PROMPT.format(source_type="user fragment", source_text=text),
                IdeaDraftOutput,
            ),
        )
        return _to_candidate(out, origin="user", source_refs=[])

    def from_conversation(self, evidence: ConversationEvidence) -> IdeaCandidate:
        """已验证 ConversationEvidence → IdeaCandidate，origin=conversation。

        未验证（verified=False）的证据拒绝提炼（外部帖 ≠ 个人证据，防御性守卫；
        CLI 层已拦，这里再兜底）。
        """
        if not evidence.verified:
            raise ValueError(f"conversation evidence not verified: {evidence.id}")
        out = cast(
            IdeaDraftOutput,
            self.runner.run(
                _IDEA_DRAFT_PROMPT.format(
                    source_type=f"conversation evidence ({evidence.kind})",
                    source_text=evidence.statement,
                ),
                IdeaDraftOutput,
            ),
        )
        source_refs = [
            SourceRef(type="conversation", ref=evidence.id, summary=evidence.statement)
        ]
        return _to_candidate(out, origin="conversation", source_refs=source_refs)

    def from_opportunity(self, opportunity: Opportunity) -> IdeaCandidate:
        """scout 机会 → IdeaCandidate，origin=search。

        外部帖子只是信号、不是个人证据：LLM 输出经 ``_sanitize_external`` 确定性
        中性化（剥离作者字段第一人称 + boundaries.known 恒空），不信任 prompt 级约束。
        source_refs 保留原文（外部、可追溯）。
        """
        out = cast(
            IdeaDraftOutput,
            self.runner.run(
                _FROM_OPPORTUNITY_PROMPT.format(opportunity=opportunity.model_dump_json()),
                IdeaDraftOutput,
            ),
        )
        out = _sanitize_external(out, opportunity.source_post.text)
        return _to_candidate(
            out, origin="search",
            source_refs=[
                SourceRef(
                    type="post", ref=opportunity.source_post.url,
                    summary=opportunity.source_post.text,
                )
            ],
        )
