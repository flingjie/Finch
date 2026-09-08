"""FragmentService：把用户片段 / 已验证 ConversationEvidence 结构化为一 IdeaCandidate。

（idea-discovery 的 user / conversation 来源；opportunity 来源在 Task 4 加入。）

本模块是纯领域逻辑：不访问 DB、不落库。``IdeaService.create_candidate`` 负责后续幂等落库。
"""

import hashlib
from typing import Literal, cast

from pydantic import BaseModel, Field

from finch.content.jobs import AuthorPosition
from finch.engagement.models import ConversationEvidence
from finch.ideas.models import IdeaBoundaries, IdeaCandidate, IdeaGenerator, SourceRef
from finch.llm.base import StructuredInferenceRunner

_GENERATOR_SKILL = "idea-discovery"
_GENERATOR_VERSION = "1.0.0"

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


def _to_candidate(
    out: "IdeaDraftOutput",
    *,
    origin: Literal["commit", "search", "user", "conversation"],
    source_refs: list[SourceRef],
) -> IdeaCandidate:
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
        """已验证 ConversationEvidence → IdeaCandidate，origin=conversation。"""
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
