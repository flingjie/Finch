"""IdeaDiverger：发散（LLM）→ 核验（确定性）→ 可选方法辅助 → 收敛（LLM）。

把一束可追溯事实（``FactBundle``）发散成 2–4 个角度（``IdeaAngle``），逐条核验
（来源可解析 / 证据强度不越级 / 去重），可选地用表达方法匹配与展开，再收敛出
一个推荐角度。核验与淘汰理由在确定性 Python 里决定；收敛只回
``recommended_index`` + 理由，不产数值分。

本模块持有一个 ``StructuredInferenceRunner``；调用方保证传入的事实已通过安全扫描
（commit 路径的安全扫描发生在 ``CommitService``，先于本模块的一切 LLM 调用）。
"""

from __future__ import annotations

import hashlib
from typing import cast

from pydantic import BaseModel, Field, model_validator

from finch.expression_methods.models import ExpressionMethod
from finch.ideas.models import (
    DraftTechniqueNote,
    EvidenceSupport,
    FactBundle,
    IdeaAngle,
    IdeaExploration,
    IdeaGenerator,
    MethodSelection,
    MethodUseAs,
    RejectedAngle,
    SourceRef,
    TakeawayKind,
)
from finch.llm.base import StructuredInferenceRunner

_GENERATOR_SKILL = "idea-discovery"
_GENERATOR_VERSION = "2.1.0"

_SEP = "\x1f"

_DIVERGE_PROMPT = """\
You turn one bundle of traceable engineering facts into 2–4 candidate angles for a
publishable engineering idea. Do NOT settle for a single decision; look separately for:

- an anomaly or counter-intuitive result;
- a failure and its correction;
- a design tradeoff;
- a reusable method;
- an as-yet-unsolved problem.

Rules:
- Produce 0 angles when the material has nothing worth discussing; never force an insight.
- Each angle is ONE central claim (core_point). Do not fold two claims into one angle.
- reader_situation: the concrete situation outside this project where someone would meet it.
- reader_takeaway: what they gain — a diagnostic step, a decision criterion, a tryable
  method, or an error worth avoiding. takeaway_kind says which.
- evidence_support: how far the current evidence actually reaches —
  "observed_this_run" (seen in this run), "inferred_cause" (a plausible explanation),
  "unverified_general" (not yet shown to generalize). Do not over claim.
- counterexample_or_limit: when it may NOT hold — a boundary or a counterexample.
- source_ref_indices: 0-based indices into the source list that back this angle. Each
  index must correspond to a real source; leave empty only if nothing backs it.

## Facts

{facts_text}

## Sources

{sources_text}

Return JSON matching the schema.
"""

_CONVERGE_PROMPT = """\
You pick the single most worth-discussing angle from a shortlist of verified candidates.
Judge by transferability, not abstraction: how clearly a peer could use it, not how
general it sounds.

Rules:
- recommended_index: the 1-based index of the best angle, or null if none is worth
  discussing.
- Prefer at most 1–2 distinct ideas when methods were used; if several angles share the
  same core claim, pick one.
- reason: one or two sentences on WHY it is most worth discussing, naming the transfer
  (a diagnostic step, decision criterion, tryable method, or error to avoid). Do NOT
  give a numeric score.

## Candidates

{angles_text}

Return JSON matching the schema.
"""

_METHOD_ASSIST_PROMPT = """\
You match expression methods to verified idea angles grounded in the user's facts.
Methods propose questions and angles; they do NOT invent user experience or project facts.

Rules:
- Return at most 3 method_selections.
- use_as=idea_angle only when the method changes the CORE CLAIM or reader problem.
  Rhythm, short sentences, rhetorical openings → use_as=draft_technique (list in
  draft_techniques; do not invent a new idea).
- Attach method fields to existing angle_index when the method refines that angle;
  add new_angles only when the method reveals a genuinely different core_point.
- missing_requirements: what material is still needed. Never fabricate that material.
- material_refs: short quotes or fact snippets from the Facts list that support the match.
- Do not copy cases from the method's source article into user facts.

## Facts

{facts_text}

## Verified angles

{angles_text}

## Method cards

{methods_text}

Return JSON matching the schema.
"""


class DivergeAngle(BaseModel):
    """发散 LLM 输出的原始角度：``source_ref_indices`` 指向输入 source_refs。"""

    core_point: str
    reader_situation: str
    reader_takeaway: str
    takeaway_kind: TakeawayKind
    evidence_support: EvidenceSupport
    counterexample_or_limit: str
    source_ref_indices: list[int] = Field(default_factory=list)


class DivergeOutput(BaseModel):
    """发散 LLM 结构化输出：2–4 个候选角度（index 由 Python 按顺序重排）。"""

    angles: list[DivergeAngle]

    @model_validator(mode="before")
    @classmethod
    def _wrap_bare_array(cls, data: object) -> object:
        """兼容 LLM 直接返回角度数组（而非 ``{"angles": [...]}``）的情况。"""
        if isinstance(data, list):
            return {"angles": data}
        return data


class ConvergeOutput(BaseModel):
    """收敛 LLM 结构化输出：推荐角度 + 理由（无数值分）。"""

    recommended_index: int | None = None
    reason: str = ""


class MethodAssistAnglePatch(BaseModel):
    """把方法挂到已有核验角度上。"""

    angle_index: int
    method_id: str
    use_as: MethodUseAs
    fit_reason: str = ""
    missing_requirements: list[str] = Field(default_factory=list)


class MethodAssistNewAngle(BaseModel):
    """方法展开出的新角度（仍须引用既有 source_ref_indices）。"""

    core_point: str
    reader_situation: str
    reader_takeaway: str
    takeaway_kind: TakeawayKind
    evidence_support: EvidenceSupport
    counterexample_or_limit: str
    source_ref_indices: list[int] = Field(default_factory=list)
    method_id: str
    use_as: MethodUseAs = "idea_angle"
    fit_reason: str = ""
    missing_requirements: list[str] = Field(default_factory=list)


class MethodAssistSelection(BaseModel):
    method_id: str
    fit_reason: str
    material_refs: list[str] = Field(default_factory=list)
    missing_requirements: list[str] = Field(default_factory=list)
    use_as: MethodUseAs


class MethodAssistDraftNote(BaseModel):
    method_id: str
    note: str


class MethodAssistOutput(BaseModel):
    method_selections: list[MethodAssistSelection] = Field(
        default_factory=list, max_length=3
    )
    angle_patches: list[MethodAssistAnglePatch] = Field(default_factory=list)
    new_angles: list[MethodAssistNewAngle] = Field(default_factory=list)
    draft_techniques: list[MethodAssistDraftNote] = Field(default_factory=list)


class IdeaDiverger:
    """发散 → 核验 → 可选方法辅助 → 收敛：一束事实 → 一个可持久化的 IdeaExploration。"""

    def __init__(self, runner: StructuredInferenceRunner) -> None:
        self.runner = runner

    def explore(
        self,
        bundle: FactBundle,
        *,
        methods: list[ExpressionMethod] | None = None,
    ) -> IdeaExploration:
        # 1) 发散
        raw = cast(
            DivergeOutput,
            self.runner.run(
                _DIVERGE_PROMPT.format(
                    facts_text="\n".join(f"- {f}" for f in bundle.facts),
                    sources_text="\n".join(
                        f"{i}: {s.type}:{s.ref} — {s.summary}"
                        for i, s in enumerate(bundle.source_refs)
                    ),
                ),
                DivergeOutput,
            ),
        )

        # 2) 核验（确定性）
        angles: list[IdeaAngle] = []
        rejected: list[RejectedAngle] = []
        seen: set[str] = set()
        for i, ra in enumerate(raw.angles, start=1):
            angle, reason = self._verify(ra, i, bundle, seen)
            if angle is None:
                rejected.append(
                    RejectedAngle(index=i, core_point=ra.core_point, reason=reason)
                )
            else:
                angles.append(angle)
                seen.add(ra.core_point.strip().lower())

        method_list = list(methods or [])
        method_selections: list[MethodSelection] = []
        draft_techniques: list[DraftTechniqueNote] = []

        # 2b) 方法辅助（可选，一次 LLM）
        if method_list:
            angles, method_selections, draft_techniques, rejected = self._assist_methods(
                bundle, angles, method_list, seen, rejected
            )

        # 3) 收敛（仅 idea_angle；draft_technique 记在 draft_techniques，不进 angles）
        idea_angles = [a for a in angles if a.use_as != "draft_technique"]
        recommended_index: int | None = None
        reason = ""
        if idea_angles:
            converge = cast(
                ConvergeOutput,
                self.runner.run(
                    _CONVERGE_PROMPT.format(angles_text=self._render_angles(idea_angles)),
                    ConvergeOutput,
                ),
            )
            if converge.recommended_index is not None and any(
                a.index == converge.recommended_index for a in idea_angles
            ):
                recommended_index = converge.recommended_index
                reason = converge.reason or ""
            if recommended_index is None:
                for a in idea_angles:
                    rejected.append(
                        RejectedAngle(
                            index=a.index, core_point=a.core_point, reason="无可迁移价值"
                        )
                    )
                idea_angles = []
                reason = ""

        return IdeaExploration(
            id=self._exploration_id(bundle, method_list),
            origin=bundle.origin,
            source_kind=bundle.source_kind,
            evidence_status=bundle.evidence_status,
            facts=list(bundle.facts),
            source_refs=list(bundle.source_refs),
            boundaries=bundle.boundaries,
            angles=idea_angles,
            rejected_angles=rejected,
            recommended_index=recommended_index,
            recommendation_reason=reason,
            selections=[],
            method_selections=method_selections,
            draft_techniques=draft_techniques,
            generator=IdeaGenerator(skill=_GENERATOR_SKILL, version=_GENERATOR_VERSION),
        )

    def _assist_methods(
        self,
        bundle: FactBundle,
        angles: list[IdeaAngle],
        methods: list[ExpressionMethod],
        seen: set[str],
        rejected: list[RejectedAngle],
    ) -> tuple[
        list[IdeaAngle],
        list[MethodSelection],
        list[DraftTechniqueNote],
        list[RejectedAngle],
    ]:
        method_ids = {m.id for m in methods}
        assist = cast(
            MethodAssistOutput,
            self.runner.run(
                _METHOD_ASSIST_PROMPT.format(
                    facts_text="\n".join(f"- {f}" for f in bundle.facts),
                    angles_text=self._render_angles(angles) or "(none yet)",
                    methods_text=self._render_methods(methods),
                ),
                MethodAssistOutput,
            ),
        )

        selections: list[MethodSelection] = []
        for raw_sel in assist.method_selections[:3]:
            if raw_sel.method_id not in method_ids:
                continue
            selections.append(
                MethodSelection(
                    method_id=raw_sel.method_id,
                    fit_reason=raw_sel.fit_reason,
                    material_refs=list(raw_sel.material_refs),
                    missing_requirements=list(raw_sel.missing_requirements),
                    use_as=raw_sel.use_as,
                )
            )

        by_index = {a.index: a for a in angles}
        for patch in assist.angle_patches:
            if patch.method_id not in method_ids:
                continue
            if patch.use_as == "draft_technique":
                continue  # handled via draft_techniques / selections
            target = by_index.get(patch.angle_index)
            if target is None:
                continue
            by_index[patch.angle_index] = target.model_copy(
                update={
                    "method_id": patch.method_id,
                    "use_as": "idea_angle",
                    "fit_reason": patch.fit_reason,
                    "missing_requirements": list(patch.missing_requirements),
                }
            )

        next_index = max(by_index.keys(), default=0) + 1
        for na in assist.new_angles:
            if na.method_id not in method_ids:
                continue
            if na.use_as == "draft_technique":
                continue
            ra = DivergeAngle(
                core_point=na.core_point,
                reader_situation=na.reader_situation,
                reader_takeaway=na.reader_takeaway,
                takeaway_kind=na.takeaway_kind,
                evidence_support=na.evidence_support,
                counterexample_or_limit=na.counterexample_or_limit,
                source_ref_indices=list(na.source_ref_indices),
            )
            angle, reason = self._verify(ra, next_index, bundle, seen)
            if angle is None:
                rejected.append(
                    RejectedAngle(index=next_index, core_point=na.core_point, reason=reason)
                )
                next_index += 1
                continue
            angle = angle.model_copy(
                update={
                    "method_id": na.method_id,
                    "use_as": "idea_angle",
                    "fit_reason": na.fit_reason,
                    "missing_requirements": list(na.missing_requirements),
                }
            )
            # Soft hedge: if missing requirements and evidence overclaims, demote limit text.
            if angle.missing_requirements and angle.evidence_support == "observed_this_run":
                limit = angle.counterexample_or_limit
                hedge = "待验证：" + "；".join(angle.missing_requirements)
                angle = angle.model_copy(
                    update={
                        "evidence_support": "unverified_general",
                        "counterexample_or_limit": f"{limit} {hedge}".strip(),
                    }
                )
            by_index[next_index] = angle
            seen.add(angle.core_point.strip().lower())
            next_index += 1

        draft_notes = [
            DraftTechniqueNote(method_id=d.method_id, note=d.note)
            for d in assist.draft_techniques
            if d.method_id in method_ids
        ]
        # Also promote draft_technique selections into draft notes if not already.
        for sel in selections:
            if sel.use_as == "draft_technique" and not any(
                d.method_id == sel.method_id for d in draft_notes
            ):
                draft_notes.append(
                    DraftTechniqueNote(
                        method_id=sel.method_id,
                        note=sel.fit_reason or "draft technique",
                    )
                )

        return list(by_index.values()), selections, draft_notes, rejected

    def _verify(
        self,
        ra: DivergeAngle,
        index: int,
        bundle: FactBundle,
        seen: set[str],
    ) -> tuple[IdeaAngle | None, str]:
        core = (ra.core_point or "").strip()
        if not core:
            return None, "核心主张为空"
        if core.lower() in seen:
            return None, "与另一角度重复"
        resolved: list[SourceRef] = []
        for si in ra.source_ref_indices:
            if 0 <= si < len(bundle.source_refs):
                resolved.append(bundle.source_refs[si])
            else:
                return None, "引用来源不存在"
        if not resolved:
            return None, "无来源"
        support = ra.evidence_support
        if bundle.evidence_status != "observed" and support == "observed_this_run":
            support = "unverified_general"
        return IdeaAngle(
            index=index,
            core_point=core,
            reader_situation=ra.reader_situation,
            reader_takeaway=ra.reader_takeaway,
            takeaway_kind=ra.takeaway_kind,
            evidence_support=support,
            counterexample_or_limit=ra.counterexample_or_limit,
            source_refs=resolved,
        ), ""

    def _render_angles(self, angles: list[IdeaAngle]) -> str:
        lines: list[str] = []
        for a in angles:
            method_bit = ""
            if a.method_id:
                method_bit = f"\n   method: {a.method_id} ({a.use_as or 'idea_angle'})"
                if a.fit_reason:
                    method_bit += f"\n   fit: {a.fit_reason}"
                if a.missing_requirements:
                    method_bit += f"\n   missing: {'; '.join(a.missing_requirements)}"
            lines.append(
                f"{a.index}. {a.core_point}\n"
                f"   situation: {a.reader_situation}\n"
                f"   takeaway({a.takeaway_kind}): {a.reader_takeaway}\n"
                f"   evidence: {a.evidence_support}\n"
                f"   limit: {a.counterexample_or_limit}"
                f"{method_bit}"
            )
        return "\n".join(lines)

    def _render_methods(self, methods: list[ExpressionMethod]) -> str:
        lines: list[str] = []
        for m in methods:
            tags = ",".join(m.purpose_tags) if m.purpose_tags else "(none)"
            lines.append(
                f"- id={m.id}\n"
                f"  title: {m.title}\n"
                f"  why_effective: {m.why_effective}\n"
                f"  when_to_use: {m.when_to_use}\n"
                f"  boundaries: {m.boundaries}\n"
                f"  required_material: {m.required_material or '(unspecified)'}\n"
                f"  purpose_tags: {tags}\n"
                f"  mini_exercise: {m.mini_exercise}"
            )
        return "\n".join(lines)

    def _exploration_id(
        self,
        bundle: FactBundle,
        methods: list[ExpressionMethod] | None = None,
    ) -> str:
        canonical = ",".join(sorted(f"{s.type}:{s.ref}" for s in bundle.source_refs))
        facts_fp = hashlib.sha256("\n".join(bundle.facts).encode()).hexdigest()
        encoded = f"{canonical}{_SEP}{facts_fp}"
        if methods:
            method_fp = ",".join(
                sorted(f"{m.id}:{m.content_fingerprint()}" for m in methods)
            )
            encoded = f"{encoded}{_SEP}{method_fp}"
        return f"expl_{hashlib.sha256(encoded.encode()).hexdigest()[:8]}"
