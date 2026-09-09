"""FragmentService：把用户片段 / 完整对话线索 / 已验证证据 结构化为 IdeaCandidate。

（idea-discovery 的 user / conversation 来源。）

本模块是纯领域逻辑：不访问 DB、不落库。``IdeaService.create_candidate`` 负责后续幂等落库。
"""

import hashlib
import json
from typing import Literal, cast

from pydantic import BaseModel, Field

from finch.content.jobs import AuthorPosition, CommunicationGoal, IdeaOrigin
from finch.content.models import RecommendedFormat
from finch.conversations.models import ConversationThread
from finch.engagement.models import ConversationEvidence, InteractionRecord
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
- recommended_format: reply | quote | short_post | thread | dm | do_not_publish.
- communication_goal: continue_discussion | invite_counterexample | summarize_practice |
  find_collaborators (how this content should advance the conversation; null if none applies).

## Source type

{source_type}

## Source text

{source_text}
"""


_THREAD_PROMPT = """\
You turn a full conversation thread into a single Idea candidate. The conversation is the
user's own ongoing exchange, so verified conclusions (agreements, experiments) may form the
user's viewpoint; open questions and the other party's claims must stay attributed and not
be rewritten as the user's own experience.

Rules:
- Only form a stance from the user's own experience, the user's explicit judgments, or
  verified conversation conclusions. External author viewpoints stay third-person.
- boundaries.known only holds verified conclusions; unresolved disagreements go to
  boundaries.unknown.
- communication_goal: continue_discussion | invite_counterexample | summarize_practice |
  find_collaborators.

## Conversation thread

{thread}

Return JSON matching the schema (core_point / observation / reader_problem / why_worth_saying /
intent / open_question / author_position / boundaries / recommended_format / communication_goal).
"""


def _to_candidate(
    out: "IdeaDraftOutput",
    *,
    origin: IdeaOrigin,
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
        communication_goal=out.communication_goal,
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
    recommended_format: RecommendedFormat = RecommendedFormat.SHORT_POST
    communication_goal: CommunicationGoal | None = None


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
        return _to_candidate(out, origin="practice", source_refs=[])

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

    def from_thread(
        self,
        thread: ConversationThread,
        *,
        interactions: list[InteractionRecord] | None = None,
    ) -> IdeaCandidate:
        """完整 ConversationThread → IdeaCandidate，origin=conversation。

        与 ``from_conversation``（单条已验证证据）不同：读取完整对话线索（open_questions /
        agreements / disagreements / possible_experiments），来源可追溯到 thread 与原始互动。
        """
        interactions = interactions or []
        thread_text = json.dumps(
            {
                "topic": thread.topic,
                "open_questions": thread.open_questions,
                "agreements": thread.agreements,
                "disagreements": thread.disagreements,
                "possible_experiments": thread.possible_experiments,
            },
            ensure_ascii=False,
        )
        out = cast(
            IdeaDraftOutput,
            self.runner.run(_THREAD_PROMPT.format(thread=thread_text), IdeaDraftOutput),
        )
        source_refs = [SourceRef(type="conversation", ref=thread.id, summary=thread.topic)]
        source_refs.extend(
            SourceRef(type="conversation", ref=rec.id, summary=rec.source_url)
            for rec in interactions
        )
        return _to_candidate(out, origin="conversation", source_refs=source_refs)
