"""ComparisonService：选题比较领域服务（生成方向 → 取舍 → 材料检查 → 建任务）。

复用：
- ``SourceResolver`` 归一化输入（text/file/url → ResolvedSource）；
- ``IdeaService.create_candidate`` 幂等落库 ContentJob；
- 证据纪律见 ``skills/_shared/evidence-policy.md``：外部素材≠个人证据，不自动标 observed。
"""

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from pydantic import BaseModel, Field

from finch.comparison.models import (
    ComparisonOutput,
    ComparisonRecommendation,
    ComparisonRecord,
    ComparisonSelection,
    Decision,
    ReadinessCheck,
    TopicDirection,
)
from finch.comparison.repository import ComparisonRepository
from finch.content.jobs import (
    AuthorPosition,
    ContentJob,
    EvidenceStatus,
    IdeaOrigin,
    SourceKind,
)
from finch.content.models import RecommendedFormat
from finch.ideas.models import IdeaBoundaries, IdeaCandidate, IdeaGenerator, SourceRef
from finch.ideas.service import IdeaService
from finch.ingest.resolver import ResolvedSource
from finch.llm.base import StructuredInferenceRunner
from finch.storage.repositories import ContentJobRepository

_COMPARE_VERSION = "1.0.0"
_GENERATOR_SKILL = "idea-discovery"
_PROMPT_PATH = Path("prompts/compare-topics.md")
_READINESS_PROMPT_PATH = Path("prompts/check-readiness.md")
_RECOMMEND_PROMPT_PATH = Path("prompts/compare-recommend.md")


def comparison_id(content_hash: str, context: "ComparisonContext | None" = None) -> str:
    """内容寻址比较 id：同素材 + 同上下文 → 同 id（幂等）。"""
    ctx = context or ComparisonContext()
    ctx_fp = hashlib.sha256(
        "\x1f".join([ctx.reader, ctx.reader_problem, ctx.goal, *ctx.practice_refs]).encode()
    ).hexdigest()
    raw = hashlib.sha256(f"{content_hash}:{ctx_fp}:{_COMPARE_VERSION}".encode()).hexdigest()
    return f"cmp_{raw[:16]}"


class ComparisonContext(BaseModel):
    """可选上下文：读者 / 读者问题 / 写作目标 / 已确认实践引用（材料，非证明）。"""

    reader: str = ""
    reader_problem: str = ""
    goal: str = ""
    practice_refs: list[str] = Field(default_factory=list)


def _fmt(items: list[str]) -> str:
    return "\n".join(f"- {it}" for it in items) if items else "（未提供）"


def _source_refs_for(source: ResolvedSource) -> list[SourceRef]:
    if source.source_type == "url" and source.source_ref:
        return [SourceRef(type="post", ref=source.source_ref, summary=source.source_ref)]
    return []


def _materials(directions: list[TopicDirection]) -> str:
    parts = [
        d.available_materials
        for d in directions
        if d.available_materials and d.available_materials != "暂无"
    ]
    return "\n".join(parts)


def _reader_value(directions: list[TopicDirection]) -> str:
    return " / ".join(d.reader_value for d in directions)


def _gaps_list(directions: list[TopicDirection]) -> list[str]:
    return [d.material_gaps for d in directions if d.material_gaps]


def _tradeoff(directions: list[TopicDirection]) -> str:
    return "\n".join(d.tradeoff for d in directions if d.tradeoff)


def _render_directions(directions: list[TopicDirection]) -> str:
    blocks = []
    for d in directions:
        blocks.append(
            "\n".join(
                [
                    f"方向 {d.angle_id}（{d.kind or '未标注'}）",
                    f"核心问题：{d.question}",
                    f"读者所得：{d.reader_value}",
                    f"已有材料：{d.available_materials or '暂无'}",
                    f"材料缺口：{d.material_gaps or '无'}",
                    f"取舍：{d.tradeoff or '无'}",
                ]
            )
        )
    return "\n\n".join(blocks)


def _evidence_for(record: ComparisonRecord) -> tuple[IdeaOrigin, SourceKind, EvidenceStatus]:
    if record.source_type == "url":
        return "synthesis", "post", "externally_reported"
    return "practice", "note", "unverified"


class ComparisonService:
    """选题比较领域服务：只依赖注入的 runner 与仓储，不直接访问 DB。"""

    def __init__(
        self,
        runner: StructuredInferenceRunner,
        repo: ComparisonRepository,
        jobs: ContentJobRepository,
    ) -> None:
        self.runner = runner
        self.repo = repo
        self.jobs = jobs
        self.ideas = IdeaService(jobs)

    def generate(
        self, source: ResolvedSource, context: ComparisonContext | None = None
    ) -> ComparisonRecord:
        """生成可比较方向并落盘；同素材+同上下文重复调用命中已有记录直接返回。"""
        ctx = context or ComparisonContext()
        cid = comparison_id(source.content_hash, ctx)
        existing = self.repo.get(cid)
        if existing is not None:
            return existing
        prompt = _PROMPT_PATH.read_text().format(
            sample_size=source.sample_size,
            body=source.body,
            reader=ctx.reader or "（未提供）",
            reader_problem=ctx.reader_problem or "（未提供）",
            goal=ctx.goal or "（未提供）",
            practice_refs=_fmt(ctx.practice_refs),
        )
        raw = cast(ComparisonOutput, self.runner.run(prompt, ComparisonOutput))
        directions = [
            d.model_copy(update={"angle_id": f"d{i}"})
            for i, d in enumerate(raw.directions, start=1)
        ]
        record = ComparisonRecord(
            id=cid,
            source_type=source.source_type,
            source_ref=source.source_ref,
            content_hash=source.content_hash,
            source_refs=_source_refs_for(source),
            created_at=datetime.now(UTC),
            generation_note=raw.note,
            angles=directions,
        )
        self.repo.upsert(record)
        return record

    def select(
        self,
        record: ComparisonRecord,
        *,
        selected_angle_ids: list[str],
        final_question: str = "",
        user_reason: str = "",
        rejection_reasons: list[str] | None = None,
        decision: Decision = "selected",
    ) -> ComparisonRecord:
        """记录用户取舍（只存用户原话与明确选择）；校验 angle_id 存在与状态合法。"""
        valid = {d.angle_id for d in record.angles}
        for aid in selected_angle_ids:
            if aid not in valid:
                raise ValueError(f"angle {aid} not in comparison {record.id}")
        if decision == "selected":
            if not selected_angle_ids:
                raise ValueError("selected decision requires at least one angle")
            if not final_question:
                if len(selected_angle_ids) == 1:
                    final_question = next(
                        d.question for d in record.angles if d.angle_id == selected_angle_ids[0]
                    )
                else:
                    raise ValueError("merge requires final_question (single converged question)")
        selection = ComparisonSelection(
            selected_angle_ids=list(selected_angle_ids),
            final_question=final_question,
            user_reason=user_reason,
            rejection_reasons=list(rejection_reasons or []),
            decision=decision,
        )
        record = record.model_copy(update={"selection": selection})
        self.repo.upsert(record)
        return record

    def recommend(self, record: ComparisonRecord) -> ComparisonRecord:
        """「帮我选」：给出建议与依据/缺口，与用户选择分离存储。"""
        if not record.angles:
            raise ValueError(f"comparison {record.id} has no angles to recommend")
        prompt = _RECOMMEND_PROMPT_PATH.read_text().format(
            directions=_render_directions(record.angles)
        )
        rec = cast(ComparisonRecommendation, self.runner.run(prompt, ComparisonRecommendation))
        valid = {d.angle_id for d in record.angles}
        rec = rec.model_copy(update={"angle_ids": [a for a in rec.angle_ids if a in valid]})
        record = record.model_copy(update={"recommendation": rec})
        self.repo.upsert(record)
        return record

    def check_readiness(self, record: ComparisonRecord) -> ComparisonRecord:
        """材料检查（LLM 判断）：ready / needs_material / needs_validation / undecided。"""
        sel = record.selection
        if sel is None or not sel.selected_angle_ids:
            raise ValueError("cannot check readiness without a selection")
        chosen = [d for d in record.angles if d.angle_id in sel.selected_angle_ids]
        prompt = _READINESS_PROMPT_PATH.read_text().format(
            final_question=sel.final_question,
            directions=_render_directions(chosen),
        )
        check = cast(ReadinessCheck, self.runner.run(prompt, ReadinessCheck))
        next_action = ""
        if check.readiness == "needs_material":
            next_action = check.clarifying_question
        elif check.readiness == "needs_validation" and check.validation is not None:
            v = check.validation
            next_action = "\n".join(
                [
                    f"要回答的问题：{v.question}",
                    f"具体动作：{v.action}",
                    f"观察记录：{v.observation}",
                    f"何时完成：{v.when}",
                    f"不能据此推断：{v.cannot_conclude}",
                ]
            )
        record = record.model_copy(
            update={
                "readiness": check.readiness,
                "readiness_reason": check.reason,
                "next_action": next_action,
            }
        )
        self.repo.upsert(record)
        return record

    def materialize(self, record: ComparisonRecord) -> ContentJob:
        """ready 时把选中方向映射为 IdeaCandidate 并幂等建 ContentJob。"""
        if record.readiness != "ready":
            raise ValueError(
                f"comparison {record.id} not ready (readiness={record.readiness})"
            )
        sel = record.selection
        if sel is None or not sel.selected_angle_ids:
            raise ValueError("cannot materialize without a selection")
        if record.content_job_id is not None:
            existing = self.jobs.get_job(record.content_job_id)
            if existing is not None:
                return existing
        chosen = [d for d in record.angles if d.angle_id in sel.selected_angle_ids]
        origin, source_kind, evidence_status = _evidence_for(record)
        candidate = IdeaCandidate(
            id=f"idea_{hashlib.sha256(sel.final_question.encode()).hexdigest()[:8]}",
            origin=origin,
            core_point=sel.final_question,
            observation=_materials(chosen),
            reader_problem=_reader_value(chosen),
            why_worth_saying=_reader_value(chosen),
            intent="stance",
            open_question="\n".join(_gaps_list(chosen)),
            author_position=AuthorPosition(
                claim=sel.final_question,
                decision=_reader_value(chosen),
                tradeoff=_tradeoff(chosen),
            ),
            source_refs=list(record.source_refs),
            boundaries=IdeaBoundaries(known=[], inferred=[], unknown=_gaps_list(chosen)),
            recommended_format=RecommendedFormat.SHORT_POST,
            generator=IdeaGenerator(skill=_GENERATOR_SKILL, version=_COMPARE_VERSION),
            source_kind=source_kind,
            facts=[],
            interpretation=sel.final_question,
            evidence_status=evidence_status,
            limitations=_tradeoff(chosen),
        )
        job = self.ideas.create_candidate(candidate)
        self.repo.upsert(record.model_copy(update={"content_job_id": job.id}))
        return job
