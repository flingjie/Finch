"""MaterialPromotionService：素材 → IdeaCandidate（明确要求「提炼成 idea」时）。

纯领域映射，不联网、不调 LLM：素材是输入，``IdeaCandidate`` 是待用户取舍的候选。
自动生成立场一律 ``proposed``（经 ``IdeaService.create_candidate`` 落库为 ``ContentJob``）。
外部素材不得写成亲历：``facts=[]``、``evidence_status="unverified"``。
"""

from __future__ import annotations

import hashlib
from typing import Literal

from finch.content.jobs import AuthorPosition
from finch.content.models import RecommendedFormat
from finch.ideas.models import IdeaBoundaries, IdeaCandidate, IdeaGenerator, SourceRef
from finch.materials.models import DiscussionRecord, MaterialSnapshot


class MaterialPromotionService:
    def from_material(
        self,
        snapshot: MaterialSnapshot,
        *,
        core_point: str,
        reader_problem: str = "",
        why_worth_saying: str = "",
        intent: Literal["stance", "exploration"] = "stance",
        discussion: DiscussionRecord | None = None,
    ) -> IdeaCandidate:
        """素材 → IdeaCandidate（source_refs 保留 Notion 页引用，幂等由 create_candidate 保证）。"""
        interpretation = snapshot.user_reflection or ""
        if not interpretation and discussion is not None:
            interpretation = discussion.user_judgment
        open_question = ""
        unknown = list(discussion.open_questions) if discussion is not None else []
        if discussion is not None and discussion.open_questions:
            open_question = " ".join(discussion.open_questions)
        return IdeaCandidate(
            id=f"idea_{hashlib.sha256(core_point.encode('utf-8')).hexdigest()[:8]}",
            origin="synthesis",
            core_point=core_point,
            observation=snapshot.extractable_text,
            reader_problem=reader_problem,
            why_worth_saying=why_worth_saying,
            intent=intent,
            open_question=open_question,
            author_position=AuthorPosition(claim=core_point, decision="", tradeoff=""),
            source_refs=[SourceRef(type="notion", ref=snapshot.page_url, summary=snapshot.title)],
            boundaries=IdeaBoundaries(known=[], inferred=[], unknown=unknown),
            recommended_format=RecommendedFormat.SHORT_POST,
            generator=IdeaGenerator(skill="material-library", version="1.0.0"),
            source_kind="note",
            facts=[],
            interpretation=interpretation,
            evidence_status="unverified",
            limitations="",
        )
