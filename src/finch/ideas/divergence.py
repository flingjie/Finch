"""IdeaDiverger：发散（LLM）→ 核验（确定性）→ 收敛（LLM）的 idea 候选探索。

把一束可追溯事实（``FactBundle``）发散成 2–4 个角度（``IdeaAngle``），逐条核验
（来源可解析 / 证据强度不越级 / 去重），再收敛出一个推荐角度。核验与淘汰理由在
确定性 Python 里决定；收敛只回 ``recommended_index`` + 理由，不产数值分。

本模块持有一个 ``StructuredInferenceRunner``；调用方保证传入的事实已通过安全扫描
（commit 路径的安全扫描发生在 ``CommitService``，先于本模块的一切 LLM 调用）。
"""

import hashlib
from typing import cast

from pydantic import BaseModel, Field

from finch.ideas.models import (
    EvidenceSupport,
    FactBundle,
    IdeaAngle,
    IdeaExploration,
    IdeaGenerator,
    RejectedAngle,
    SourceRef,
    TakeawayKind,
)
from finch.llm.base import StructuredInferenceRunner

_GENERATOR_SKILL = "idea-discovery"
_GENERATOR_VERSION = "2.0.0"

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
- reason: one or two sentences on WHY it is most worth discussing, naming the transfer
  (a diagnostic step, decision criterion, tryable method, or error to avoid). Do NOT
  give a numeric score.

## Candidates

{angles_text}

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


class ConvergeOutput(BaseModel):
    """收敛 LLM 结构化输出：推荐角度 + 理由（无数值分）。"""

    recommended_index: int | None = None
    reason: str = ""


class IdeaDiverger:
    """发散 → 核验 → 收敛：一束事实 → 一个可持久化的 IdeaExploration。"""

    def __init__(self, runner: StructuredInferenceRunner) -> None:
        self.runner = runner

    def explore(self, bundle: FactBundle) -> IdeaExploration:
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
                rejected.append(RejectedAngle(index=i, core_point=ra.core_point, reason=reason))
            else:
                angles.append(angle)
                seen.add(ra.core_point.strip().lower())

        # 3) 收敛
        recommended_index: int | None = None
        reason = ""
        if angles:
            converge = cast(
                ConvergeOutput,
                self.runner.run(
                    _CONVERGE_PROMPT.format(angles_text=self._render_angles(angles)),
                    ConvergeOutput,
                ),
            )
            if (
                converge.recommended_index is not None
                and 1 <= converge.recommended_index <= len(angles)
            ):
                recommended_index = converge.recommended_index
                reason = converge.reason or ""
            if recommended_index is None:
                for a in angles:
                    rejected.append(
                        RejectedAngle(index=a.index, core_point=a.core_point, reason="无可迁移价值")
                    )
                angles = []
                reason = ""

        return IdeaExploration(
            id=self._exploration_id(bundle),
            origin=bundle.origin,
            source_kind=bundle.source_kind,
            evidence_status=bundle.evidence_status,
            facts=list(bundle.facts),
            source_refs=list(bundle.source_refs),
            boundaries=bundle.boundaries,
            angles=angles,
            rejected_angles=rejected,
            recommended_index=recommended_index,
            recommendation_reason=reason,
            selections=[],
            generator=IdeaGenerator(skill=_GENERATOR_SKILL, version=_GENERATOR_VERSION),
        )

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
            lines.append(
                f"{a.index}. {a.core_point}\n"
                f"   situation: {a.reader_situation}\n"
                f"   takeaway({a.takeaway_kind}): {a.reader_takeaway}\n"
                f"   evidence: {a.evidence_support}\n"
                f"   limit: {a.counterexample_or_limit}"
            )
        return "\n".join(lines)

    def _exploration_id(self, bundle: FactBundle) -> str:
        canonical = ",".join(sorted(f"{s.type}:{s.ref}" for s in bundle.source_refs))
        facts_fp = hashlib.sha256("\n".join(bundle.facts).encode()).hexdigest()
        encoded = f"{canonical}{_SEP}{facts_fp}".encode()
        return f"expl_{hashlib.sha256(encoded).hexdigest()[:8]}"
