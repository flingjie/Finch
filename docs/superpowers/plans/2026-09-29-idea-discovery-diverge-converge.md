# Idea-discovery 发散→核验→收敛 改造 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `idea-discovery` 的 commit 路径从「提取一个明确决策」改为「发散出 2–4 个角度 → 确定性核验 → 收敛到一个可迁移观点」，持久化整个探索过程并支持改选。

**Architecture:** 新增 `IdeaDiverger`（发散 LLM + 确定性核验 + 收敛 LLM）与 `IdeaExploration` 持久化模型；`CommitService` 改为产出 `FactBundle`（保留噪声过滤/安全扫描），CLI `finch ideas commit` 走「to_facts → explore → persist → create_from_angle」，新命令 `finch ideas choose` 改选角度。fragment/conversation/signals 本阶段不动。

**Tech Stack:** Python 3.12, Pydantic 2, typer, `StructuredInferenceRunner`（CodexRunner / OpenAICompatibleRunner 双后端）。

## Global Constraints

- Python 3.12+；Pydantic 2 模型；`Literal`/`Field`；`ruff check .`（E,F,I,B,UP，行长 100）、`mypy src`、`pytest` 全绿。
- 安全扫描（`scan_cards`）必须**先于一切发散/收敛 LLM 调用**。
- LLM 永不产出数值 `total`；收敛只回 `recommended_index` + 理由；核验/淘汰理由在确定性 Python 决定。
- 自动生成立场一律 `proposed`；`confirmed` 只来自 `finch ideas confirm`。
- `ContentJob` 持久化 schema **不变**；新字段只加在 `IdeaCandidate`（落库前）与 `IdeaExploration`（可追溯）。
- 旧 YAML 继续可读：所有新字段可空/有默认值。
- commit 路径 `evidence_status` 恒 `observed`（作者亲历）；`evidence_support`（主张强度）独立字段。

---

### Task 1: 数据模型与枚举（`ideas/models.py`）

**Files:**
- Modify: `src/finch/ideas/models.py`
- Test: `tests/unit/test_idea_exploration_models.py`（新建）

**Interfaces:**
- Consumes: `finch.content.jobs` 的 `EvidenceStatus` / `IdeaOrigin` / `SourceKind`（已导入）；`IdeaBoundaries` / `SourceRef` / `IdeaGenerator`（本文件）。
- Produces: `TakeawayKind`、`EvidenceSupport`、`IdeaAngle`、`RejectedAngle`、`Selection`、`IdeaExploration`、`FactBundle`，以及 `IdeaCandidate` 的 5 个新可选字段（后续 Task 2–6 依赖）。

- [ ] **Step 1: 写失败测试**

新建 `tests/unit/test_idea_exploration_models.py`：

```python
"""Tests for 发散相关数据模型（IdeaAngle / IdeaExploration / FactBundle）。"""

from datetime import datetime

from finch.ideas.models import (
    FactBundle,
    IdeaAngle,
    IdeaBoundaries,
    IdeaExploration,
    IdeaGenerator,
    RejectedAngle,
    Selection,
    SourceRef,
)


def _angle(index: int = 1, **overrides) -> IdeaAngle:
    data = dict(
        index=index,
        core_point="小型重复提取应考虑批处理",
        reader_situation="有人把一批小提取任务分发给多个进程",
        reader_takeaway="可尝试批处理降低进程启动开销",
        takeaway_kind="method",
        evidence_support="observed_this_run",
        counterexample_or_limit="批处理会放大单次失败影响",
        source_refs=[SourceRef(type="commit", ref="https://github.com/a/b/commit/x", summary="m")],
    )
    data.update(overrides)
    return IdeaAngle(**data)


def _bundle() -> FactBundle:
    return FactBundle(
        facts=["启动开销使整体耗时很长"],
        source_refs=[SourceRef(type="commit", ref="https://github.com/a/b/commit/x", summary="m")],
        boundaries=IdeaBoundaries(),
        evidence_status="observed",
        origin="practice",
        source_kind="commit",
    )


def test_fact_bundle_round_trip():
    b = _bundle()
    assert FactBundle.model_validate(b.model_dump(mode="json")) == b


def test_idea_angle_round_trip():
    a = _angle()
    assert IdeaAngle.model_validate(a.model_dump(mode="json")) == a


def test_exploration_round_trip_with_selection():
    e = IdeaExploration(
        id="expl_abc12345",
        origin="practice",
        source_kind="commit",
        evidence_status="observed",
        facts=["启动开销使整体耗时很长"],
        source_refs=[SourceRef(type="commit", ref="https://github.com/a/b/commit/x", summary="m")],
        boundaries=IdeaBoundaries(known=["启动开销使整体耗时很长"]),
        angles=[_angle()],
        rejected_angles=[RejectedAngle(index=2, core_point="并发先拆推理时间", reason="无来源")],
        recommended_index=1,
        recommendation_reason="给处理批量任务的人一个可验证的改法",
        selections=[Selection(index=1, job_id="idea_x", at=datetime(2026, 9, 29))],
        generator=IdeaGenerator(skill="idea-discovery", version="2.0.0"),
    )
    assert IdeaExploration.model_validate(e.model_dump(mode="json")) == e
    assert e.angles[0].takeaway_kind == "method"


def test_idea_candidate_accepts_new_optional_fields():
    from finch.content.jobs import AuthorPosition
    from finch.content.models import RecommendedFormat
    from finch.ideas.models import IdeaCandidate

    c = IdeaCandidate(
        id="idea_x",
        origin="practice",
        core_point="小型重复提取应考虑批处理",
        reader_problem="有人把一批小提取任务分发给多个进程",
        why_worth_saying="可尝试批处理降低进程启动开销",
        author_position=AuthorPosition(claim="c", decision="d", tradeoff="t"),
        source_refs=[],
        boundaries=IdeaBoundaries(),
        recommended_format=RecommendedFormat.SHORT_POST,
        generator=IdeaGenerator(skill="idea-discovery", version="2.0.0"),
        reader_situation="s",
        reader_takeaway="t",
        takeaway_kind="method",
        evidence_support="observed_this_run",
        counterexample_or_limit="limit",
    )
    assert c.evidence_support == "observed_this_run"
    assert c.takeaway_kind == "method"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/test_idea_exploration_models.py -v`
Expected: FAIL（`ImportError: cannot import name 'IdeaAngle'` 等）

- [ ] **Step 3: 实现模型**

在 `src/finch/ideas/models.py` 顶部加 `from datetime import datetime`（`from typing import Literal` 之后），并在 `IdeaBoundaries` 之后、`IdeaGenerator` 之前插入新类型；`IdeaCandidate` 末尾加 5 个可选字段。

```python
TakeawayKind = Literal["diagnosis", "decision_criteria", "method", "pitfall"]
EvidenceSupport = Literal["observed_this_run", "inferred_cause", "unverified_general"]


class IdeaAngle(BaseModel):
    """发散后通过核验的一个候选角度（选中后映射为 IdeaCandidate）。"""

    index: int
    core_point: str
    reader_situation: str
    reader_takeaway: str
    takeaway_kind: TakeawayKind
    evidence_support: EvidenceSupport
    counterexample_or_limit: str
    source_refs: list[SourceRef]


class RejectedAngle(BaseModel):
    """被淘汰的角度及其理由（核验或收敛阶段）。"""

    index: int
    core_point: str
    reason: str


class Selection(BaseModel):
    """一次选择（自动推荐或改选），可追溯。"""

    index: int
    job_id: str
    at: datetime


class IdeaExploration(BaseModel):
    """一次发散的中间结果：多角度 + 淘汰理由 + 选择历史（持久化，便于改选）。"""

    id: str
    origin: IdeaOrigin
    source_kind: SourceKind | None = None
    evidence_status: EvidenceStatus | None = None
    facts: list[str] = Field(default_factory=list)
    source_refs: list[SourceRef] = Field(default_factory=list)
    boundaries: IdeaBoundaries = Field(default_factory=IdeaBoundaries)
    angles: list[IdeaAngle] = Field(default_factory=list)
    rejected_angles: list[RejectedAngle] = Field(default_factory=list)
    recommended_index: int | None = None
    recommendation_reason: str = ""
    selections: list[Selection] = Field(default_factory=list)
    generator: IdeaGenerator


class FactBundle(BaseModel):
    """进入发散前的一束可追溯事实（commit 路径产出；后续 fragment 来源复用）。"""

    facts: list[str]
    source_refs: list[SourceRef]
    boundaries: IdeaBoundaries
    evidence_status: EvidenceStatus
    origin: IdeaOrigin
    source_kind: SourceKind
```

在 `IdeaCandidate` 的 `limitations: str = ""` 之后追加：

```python
    reader_situation: str = ""
    reader_takeaway: str = ""
    takeaway_kind: TakeawayKind | None = None
    evidence_support: EvidenceSupport | None = None
    counterexample_or_limit: str = ""
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/unit/test_idea_exploration_models.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/finch/ideas/models.py tests/unit/test_idea_exploration_models.py
git commit -m "feat(ideas): add IdeaAngle/IdeaExploration/FactBundle models"
```

---

### Task 2: 发散服务 `IdeaDiverger`（新 `ideas/divergence.py`）

**Files:**
- Create: `src/finch/ideas/divergence.py`
- Test: `tests/unit/test_divergence.py`（新建）

**Interfaces:**
- Consumes: `StructuredInferenceRunner.run(prompt, output_model)`；`FactBundle`、`IdeaAngle`、`IdeaExploration`、`IdeaGenerator`、`RejectedAngle`、`SourceRef`（Task 1）。
- Produces: `IdeaDiverger.explore(bundle: FactBundle) -> IdeaExploration`（Task 5 依赖）；`DivergeOutput`、`ConvergeOutput` 两个输出模型。

- [ ] **Step 1: 写失败测试**

新建 `tests/unit/test_divergence.py`：

```python
"""Tests for IdeaDiverger（发散 → 确定性核验 → 收敛）。"""

from finch.ideas.divergence import ConvergeOutput, DivergeOutput, IdeaDiverger
from finch.ideas.models import FactBundle, IdeaBoundaries, SourceRef


class FakeRunner:
    def __init__(self, diverge_out: DivergeOutput, converge_out: ConvergeOutput):
        self.diverge_out = diverge_out
        self.converge_out = converge_out
        self.calls: list[type] = []

    def run(self, prompt, output_model, **kw):
        self.calls.append(output_model)
        if output_model is DivergeOutput:
            return self.diverge_out
        if output_model is ConvergeOutput:
            return self.converge_out
        raise AssertionError(f"unexpected model {output_model}")


def _bundle() -> FactBundle:
    return FactBundle(
        facts=["并发任务慢", "启动开销使整体耗时很长"],
        source_refs=[
            SourceRef(type="commit", ref="https://github.com/a/b/commit/1", summary="c1"),
            SourceRef(type="commit", ref="https://github.com/a/b/commit/2", summary="c2"),
        ],
        boundaries=IdeaBoundaries(),
        evidence_status="observed",
        origin="practice",
        source_kind="commit",
    )


def _angle(**kw) -> dict:
    data = dict(
        core_point="小型重复提取应考虑批处理",
        reader_situation="有人把一批小提取任务分发给多个进程",
        reader_takeaway="可尝试批处理降低进程启动开销",
        takeaway_kind="method",
        evidence_support="observed_this_run",
        counterexample_or_limit="批处理会放大单次失败影响",
        source_ref_indices=[0, 1],
    )
    data.update(kw)
    return data


def _diverge(*angles) -> DivergeOutput:
    from finch.ideas.divergence import DivergeAngle

    return DivergeOutput(angles=[DivergeAngle(**a) for a in angles])


def test_explore_resolves_sources_and_recommends():
    d = _diverge(_angle(), _angle(core_point="并发任务慢先拆推理时间", source_ref_indices=[0]))
    svc = IdeaDiverger(FakeRunner(d, ConvergeOutput(recommended_index=1, reason="可验证的改法")))
    e = svc.explore(_bundle())
    assert len(e.angles) == 2
    assert e.angles[0].index == 1
    assert [s.ref for s in e.angles[0].source_refs] == [
        "https://github.com/a/b/commit/1", "https://github.com/a/b/commit/2",
    ]
    assert e.recommended_index == 1
    assert e.recommendation_reason == "可验证的改法"
    assert e.id.startswith("expl_")


def test_verify_rejects_unresolvable_source():
    d = _diverge(_angle(source_ref_indices=[9]))  # 越界
    svc = IdeaDiverger(FakeRunner(d, ConvergeOutput(recommended_index=1, reason="")))
    e = svc.explore(_bundle())
    assert e.angles == []
    assert e.recommended_index is None
    assert e.rejected_angles[0].reason == "引用来源不存在"


def test_verify_rejects_no_source():
    d = _diverge(_angle(source_ref_indices=[]))
    svc = IdeaDiverger(FakeRunner(d, ConvergeOutput(recommended_index=1, reason="")))
    e = svc.explore(_bundle())
    assert e.angles == []
    assert e.rejected_angles[0].reason == "无来源"


def test_verify_rejects_duplicate_core_point():
    d = _diverge(_angle(), _angle())  # 同 core_point
    svc = IdeaDiverger(FakeRunner(d, ConvergeOutput(recommended_index=1, reason="")))
    e = svc.explore(_bundle())
    assert len(e.angles) == 1
    assert e.rejected_angles[0].reason == "与另一角度重复"


def test_verify_demotes_observed_when_externally_reported():
    bundle = _bundle().model_copy(update={"evidence_status": "externally_reported"})
    d = _diverge(_angle(evidence_support="observed_this_run"))
    svc = IdeaDiverger(FakeRunner(d, ConvergeOutput(recommended_index=1, reason="")))
    e = svc.explore(bundle)
    assert e.angles[0].evidence_support == "unverified_general"


def test_converge_rejects_all_when_no_index():
    d = _diverge(_angle())
    svc = IdeaDiverger(FakeRunner(d, ConvergeOutput(recommended_index=None, reason="无迁移价值")))
    e = svc.explore(_bundle())
    assert e.angles == []
    assert e.recommended_index is None
    assert e.rejected_angles[0].reason == "无可迁移价值"


def test_explore_same_input_same_id():
    d = _diverge(_angle())
    out = ConvergeOutput(recommended_index=1, reason="r")
    a = IdeaDiverger(FakeRunner(d, out)).explore(_bundle())
    b = IdeaDiverger(FakeRunner(d, out)).explore(_bundle())
    assert a.id == b.id
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/test_divergence.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'finch.ideas.divergence'`）

- [ ] **Step 3: 实现 `IdeaDiverger`**

新建 `src/finch/ideas/divergence.py`：

```python
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
  "unverified_general" (not yet shown to generalize). Do not overclaim.
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
        facts_fp = hashlib.sha256("\n".join(bundle.facts).encode("utf-8")).hexdigest()
        return f"expl_{hashlib.sha256(f'{canonical}{_SEP}{facts_fp}'.encode('utf-8')).hexdigest()[:8]}"
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/unit/test_divergence.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/finch/ideas/divergence.py tests/unit/test_divergence.py
git commit -m "feat(ideas): add IdeaDiverger (diverge/verify/converge)"
```

---

### Task 3: `IdeaService.create_from_angle`

**Files:**
- Modify: `src/finch/ideas/service.py`
- Test: `tests/unit/test_ideas_service.py`（追加测试）

**Interfaces:**
- Consumes: `IdeaAngle`、`FactBundle`、`IdeaGenerator`、`IdeaBoundaries`、`IdeaCandidate`（Task 1）；既有 `create_candidate` / `_input_fingerprint`（本文件）。
- Produces: `IdeaService.create_from_angle(angle: IdeaAngle, *, bundle: FactBundle, generator: IdeaGenerator) -> ContentJob`（Task 5/6 依赖）。

- [ ] **Step 1: 写失败测试**

在 `tests/unit/test_ideas_service.py` 顶部 import 里追加 `FactBundle, IdeaAngle`，并在文件末尾追加：

```python
# ---- create_from_angle ----

def _bundle() -> FactBundle:
    return FactBundle(
        facts=["graph 支持重放"],
        source_refs=[SourceRef(type="commit", ref="abc123", summary="引入 graph 运行时")],
        boundaries=IdeaBoundaries(known=["graph 支持重放"], inferred=[], unknown=[]),
        evidence_status="observed",
        origin="practice",
        source_kind="commit",
    )


def _angle(**overrides) -> IdeaAngle:
    data = dict(
        index=1,
        core_point="Graph 的价值是恢复与重放",
        reader_situation="很多人只把 Graph 当可视化",
        reader_takeaway="用可恢复性评价 Graph",
        takeaway_kind="decision_criteria",
        evidence_support="observed_this_run",
        counterexample_or_limit="需要持久化状态",
        source_refs=[SourceRef(type="commit", ref="abc123", summary="引入 graph 运行时")],
    )
    data.update(overrides)
    return IdeaAngle(**data)


def test_create_from_angle_maps_fields():
    svc, _ = _service()
    job = svc.create_from_angle(
        _angle(), bundle=_bundle(), generator=IdeaGenerator(skill="idea-discovery", version="2.0.0")
    )
    assert job.status == ContentJobStatus.PROPOSED
    assert job.core_message == "Graph 的价值是恢复与重放"
    assert job.reader_problem == "很多人只把 Graph 当可视化"
    assert job.why_now == "用可恢复性评价 Graph"
    assert job.author_position is not None
    assert job.author_position.claim == "Graph 的价值是恢复与重放"
    assert job.author_position.decision == "用可恢复性评价 Graph"
    assert job.author_position.tradeoff == "需要持久化状态"
    assert job.facts == ["graph 支持重放"]
    assert job.evidence_status == "observed"
    assert job.generator_name == "idea-discovery"
    assert job.generator_version == "2.0.0"


def test_create_from_angle_second_angle_makes_second_job():
    svc, repo = _service()
    first = svc.create_from_angle(
        _angle(), bundle=_bundle(), generator=IdeaGenerator(skill="idea-discovery", version="2.0.0")
    )
    second = svc.create_from_angle(
        _angle(core_point="另一个角度"), bundle=_bundle(),
        generator=IdeaGenerator(skill="idea-discovery", version="2.0.0"),
    )
    assert first.id != second.id
    assert len(repo._by_id) == 2
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/test_ideas_service.py -k create_from_angle -v`
Expected: FAIL（`AttributeError: 'IdeaService' object has no attribute 'create_from_angle'`）

- [ ] **Step 3: 实现 `create_from_angle`**

在 `src/finch/ideas/service.py` 的 import 块中，把 `from finch.ideas.models import IdeaCandidate` 改为：

```python
from finch.content.models import RecommendedFormat
from finch.ideas.models import (
    FactBundle,
    IdeaAngle,
    IdeaBoundaries,
    IdeaCandidate,
    IdeaGenerator,
)
```

并在 `create_candidate` 方法之后插入：

```python
    def create_from_angle(
        self,
        angle: IdeaAngle,
        *,
        bundle: FactBundle,
        generator: IdeaGenerator,
    ) -> ContentJob:
        """把一个发散角度映射为 IdeaCandidate 并幂等落库（复用 create_candidate）。"""
        candidate = IdeaCandidate(
            id=f"idea_{hashlib.sha256(angle.core_point.encode('utf-8')).hexdigest()[:8]}",
            origin=bundle.origin,
            core_point=angle.core_point,
            observation="\n".join(bundle.facts),
            reader_problem=angle.reader_situation,
            why_worth_saying=angle.reader_takeaway,
            intent="stance",
            author_position=AuthorPosition(
                claim=angle.core_point,
                decision=angle.reader_takeaway,
                tradeoff=angle.counterexample_or_limit,
            ),
            source_refs=list(bundle.source_refs),
            boundaries=self._boundaries_for(angle, bundle),
            recommended_format=RecommendedFormat.SHORT_POST,
            generator=generator,
            source_kind=bundle.source_kind,
            facts=list(bundle.facts),
            interpretation=angle.core_point,
            evidence_status=bundle.evidence_status,
            limitations=angle.counterexample_or_limit,
            reader_situation=angle.reader_situation,
            reader_takeaway=angle.reader_takeaway,
            takeaway_kind=angle.takeaway_kind,
            evidence_support=angle.evidence_support,
            counterexample_or_limit=angle.counterexample_or_limit,
        )
        return self.create_candidate(candidate)

    def _boundaries_for(self, angle: IdeaAngle, bundle: FactBundle) -> IdeaBoundaries:
        """事实按 bundle 已有置信度归类；角度主张按 evidence_support 归类。"""
        known = list(bundle.boundaries.known)
        inferred = list(bundle.boundaries.inferred)
        unknown = list(bundle.boundaries.unknown)
        claim_bucket = {
            "observed_this_run": "known",
            "inferred_cause": "inferred",
            "unverified_general": "unknown",
        }[angle.evidence_support]
        bucket = {"known": known, "inferred": inferred, "unknown": unknown}[claim_bucket]
        if angle.core_point not in bucket:
            bucket.append(angle.core_point)
        return IdeaBoundaries(known=known, inferred=inferred, unknown=unknown)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/unit/test_ideas_service.py -v`
Expected: PASS（含既有测试）

- [ ] **Step 5: 提交**

```bash
git add src/finch/ideas/service.py tests/unit/test_ideas_service.py
git commit -m "feat(ideas): IdeaService.create_from_angle maps angle to candidate"
```

---

### Task 4: `IdeaExplorationRepository`

**Files:**
- Modify: `src/finch/storage/repositories.py`
- Test: `tests/unit/test_exploration_repository.py`（新建）

**Interfaces:**
- Consumes: `Workspace`、`_write` / `_read` / `_list_all`（本文件顶部已定义）；`IdeaExploration`（Task 1）。
- Produces: `IdeaExplorationRepository.upsert` / `.get` / `.list_all`（Task 5/6 依赖）。

- [ ] **Step 1: 写失败测试**

新建 `tests/unit/test_exploration_repository.py`：

```python
"""Tests for IdeaExplorationRepository（文件工作区持久化）。"""

from finch.ideas.models import IdeaExploration, IdeaGenerator
from finch.storage.repositories import IdeaExplorationRepository
from finch.storage.workspace import Workspace


def _exploration(id: str = "expl_abc12345") -> IdeaExploration:
    return IdeaExploration(
        id=id,
        origin="practice",
        source_kind="commit",
        facts=["f"],
        source_refs=[],
        generator=IdeaGenerator(skill="idea-discovery", version="2.0.0"),
    )


def test_upsert_and_get_round_trip(tmp_path):
    ws = Workspace(tmp_path)
    repo = IdeaExplorationRepository(ws)
    repo.upsert(_exploration())
    got = repo.get("expl_abc12345")
    assert got is not None
    assert got.facts == ["f"]


def test_get_missing_returns_none(tmp_path):
    repo = IdeaExplorationRepository(Workspace(tmp_path))
    assert repo.get("expl_nope") is None


def test_list_all(tmp_path):
    ws = Workspace(tmp_path)
    repo = IdeaExplorationRepository(ws)
    repo.upsert(_exploration("expl_a"))
    repo.upsert(_exploration("expl_b"))
    ids = sorted(e.id for e in repo.list_all())
    assert ids == ["expl_a", "expl_b"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/test_exploration_repository.py -v`
Expected: FAIL（`ImportError: cannot import name 'IdeaExplorationRepository'`）

- [ ] **Step 3: 实现仓储**

在 `src/finch/storage/repositories.py` 顶部 import 区（`from finch.ideas.models import ...` 尚不存在，新增一行；`IdeaExploration` 从 `finch.ideas.models` 导入）加：

```python
from finch.ideas.models import IdeaExploration
```

并在 `ContentJobRepository` 类之后、`DraftVersionRepository` 之前插入：

```python
class IdeaExplorationRepository:
    """发散探索结果仓储：``<var>/explorations/<id>.yaml``（同 ContentJob 的文件布局）。"""

    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def upsert(self, exploration: IdeaExploration) -> None:
        _write(self.ws, "explorations", exploration.id, exploration)

    def get(self, exploration_id: str) -> IdeaExploration | None:
        return _read(self.ws, "explorations", exploration_id, IdeaExploration)

    def list_all(self) -> list[IdeaExploration]:
        return _list_all(self.ws, "explorations", IdeaExploration)
```

注意：`repositories.py` 已有 `from finch.ideas.models import ...`？目前没有（该文件 import 的是 `finch.content.jobs` 等）。确认用新增 import 行即可，避免与现有 `from finch.content.jobs import ContentJob` 冲突。

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/unit/test_exploration_repository.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/finch/storage/repositories.py tests/unit/test_exploration_repository.py
git commit -m "feat(ideas): IdeaExplorationRepository"
```

---

### Task 5: `CommitService.to_facts` + 门禁放宽 + `ideas_commit` 重接

**Files:**
- Modify: `src/finch/ideas/commit_service.py`
- Modify: `src/finch/cli.py`（`ideas_commit`）
- Test: `tests/unit/test_commit_service.py`（改写）、`tests/unit/test_cli_ideas.py`（改写 commit 相关测试）

**Interfaces:**
- Consumes: `FactBundle`（Task 1）、`IdeaDiverger.explore`（Task 2）、`IdeaService.create_from_angle`（Task 3）、`IdeaExplorationRepository`（Task 4）。
- Produces: `CommitService.to_facts(commits, *, repo, repo_is_private=False) -> list[FactBundle]`；`ideas_commit` 走新链路。

> 说明：`to_ideas` 更名为 `to_facts` 后，CLI 是唯一消费者，故两者在**同一任务**内改，保证仓库随时可运行。`IdeaAngle` 的 `index` 由 `IdeaDiverger` 按 1-based 分配；`ideas_commit` 用 `recommended_index` 取角度。

- [ ] **Step 1: 改写 `test_commit_service.py`**

把 `tests/unit/test_commit_service.py` 中所有 `to_ideas` 调用改为 `to_facts`，并把断言从 `IdeaCandidate` 字段改为 `FactBundle` 字段。完整新文件：

```python
"""Tests for CommitService（commit → FactBundle，发散前置）。"""

from finch.evidence.models import Claim, ClaimConfidence, EngineeringEvent
from finch.github.models import CommitDetail, CommitFile
from finch.ideas.commit_service import CommitService
from finch.ideas.models import SourceRef

SHA = "a" * 40
COMMIT_URL = f"https://github.com/acme/proj/commit/{SHA}"


class FakeCommitReader:
    def __init__(self, *, noise: bool = False) -> None:
        self._noise = noise

    def filter_noise(self, commits: list[CommitDetail]) -> list[CommitDetail]:
        return [] if self._noise else list(commits)


class FakeExtractor:
    def __init__(self, events: list[EngineeringEvent]) -> None:
        self._events = list(events)
        self.calls: list[tuple[list[CommitDetail], str]] = []

    def extract(self, commits: list[CommitDetail], repo: str) -> list[EngineeringEvent]:
        self.calls.append((list(commits), repo))
        return list(self._events)


def _commit() -> CommitDetail:
    return CommitDetail(
        sha=SHA,
        message="feat: node-ize orchestrator",
        author_date="2026-09-01T00:00:00Z",
        html_url=COMMIT_URL,
        parents=[],
        files=[CommitFile(filename="src/graph/runtime.ts", status="modified",
                          additions=10, deletions=4, patch="+export function run")],
        stats={},
    )


def _event(**overrides) -> EngineeringEvent:
    data = dict(
        id="evt_acme_proj_node_runtime",
        repository="acme/proj",
        commits=[SHA],
        problem=Claim(statement="orchestrator was hard to rerun", confidence=ClaimConfidence.SUPPORTED),
        decision=Claim(statement="make the orchestrator a deterministic graph", confidence=ClaimConfidence.INFERRED),
        result=Claim(statement="failures can now be replayed", confidence=ClaimConfidence.SUPPORTED),
        missing_context=[],
        topics=["deterministic execution"],
    )
    data.update(overrides)
    return EngineeringEvent(**data)


def _service(events: list[EngineeringEvent], *, noise: bool = False) -> CommitService:
    return CommitService(FakeCommitReader(noise=noise), FakeExtractor(events))


def test_clear_material_yields_one_bundle():
    svc = _service([_event()])
    bundles = svc.to_facts([_commit()], repo="acme/proj")
    assert len(bundles) == 1
    b = bundles[0]
    assert b.origin == "practice"
    assert b.source_kind == "commit"
    assert b.evidence_status == "observed"
    assert b.facts == [
        "orchestrator was hard to rerun",
        "make the orchestrator a deterministic graph",
        "failures can now be replayed",
    ]


def test_source_refs_use_commit_url():
    svc = _service([_event()])
    b = svc.to_facts([_commit()], repo="acme/proj")[0]
    assert b.source_refs == [
        SourceRef(type="commit", ref=COMMIT_URL, summary="feat: node-ize orchestrator")
    ]


def test_mechanical_commit_yields_nothing():
    svc = _service([_event()], noise=True)
    assert svc.to_facts([_commit()], repo="acme/proj") == []


def test_private_repo_yields_nothing():
    svc = _service([_event()])
    assert svc.to_facts([_commit()], repo="acme/proj", repo_is_private=True) == []


def test_unknown_decision_still_yields_if_result_present():
    # 门禁放宽：decision UNKNOWN 但 result 有内容 → 仍进入发散
    svc = _service([_event(decision=Claim(statement="unclear why", confidence=ClaimConfidence.UNKNOWN))])
    bundles = svc.to_facts([_commit()], repo="acme/proj")
    assert len(bundles) == 1


def test_all_unknown_yields_nothing():
    svc = _service([_event(
        problem=Claim(statement="", confidence=ClaimConfidence.UNKNOWN),
        decision=Claim(statement="", confidence=ClaimConfidence.UNKNOWN),
        result=Claim(statement="", confidence=ClaimConfidence.UNKNOWN),
    )])
    assert svc.to_facts([_commit()], repo="acme/proj") == []


def test_boundaries_map_confidence():
    svc = _service([_event(
        problem=Claim(statement="hard to rerun", confidence=ClaimConfidence.VERIFIED),
        decision=Claim(statement="use a deterministic graph", confidence=ClaimConfidence.INFERRED),
        result=Claim(statement="replay works", confidence=ClaimConfidence.SUPPORTED),
    )])
    b = svc.to_facts([_commit()], repo="acme/proj")[0]
    assert b.boundaries.known == ["hard to rerun", "replay works"]
    assert b.boundaries.inferred == ["use a deterministic graph"]
    assert b.boundaries.unknown == []


def test_same_input_yields_same_output():
    svc = _service([_event()])
    commits = [_commit()]
    first = svc.to_facts(commits, repo="acme/proj")
    second = svc.to_facts(commits, repo="acme/proj")
    assert first == second
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/test_commit_service.py -v`
Expected: FAIL（`AttributeError: 'CommitService' object has no attribute 'to_facts'`）

- [ ] **Step 3: 改写 `commit_service.py`**

把 `_has_clear_decision` 换成 `_has_traceable_material`，`_event_to_idea` 换成 `_event_to_facts`，`to_ideas` 换成 `to_facts`。相关 import 增删：

```python
from finch.ideas.models import (
    FactBundle,
    IdeaBoundaries,
    IdeaGenerator,
    SourceRef,
)
```

（`IdeaCandidate`、`AuthorPosition`、`RecommendedFormat` 不再需要，删除相关 import；`IdeaGenerator` 与 `_GENERATOR_*` 常量也删除，因为 generator 现在由 `IdeaDiverger`/`create_from_angle` 负责。）

替换门禁函数：

```python
def _has_traceable_material(event: EngineeringEvent) -> bool:
    """problem/decision/result 任一语句非空且置信度非 UNKNOWN 即视为可追溯材料。"""
    for claim in (event.problem, event.decision, event.result):
        statement = (claim.statement or "").strip()
        if statement and claim.confidence is not ClaimConfidence.UNKNOWN:
            return True
    return False
```

替换 `_event_to_idea` 为：

```python
def _event_to_facts(
    event: EngineeringEvent,
    commits: list[CommitDetail],
) -> FactBundle | None:
    """单个事件 → 一束可追溯事实；无来源 commit 时返回 None（不可追溯）。"""
    sha_to_commit = {c.sha: c for c in commits}
    source_refs: list[SourceRef] = []
    for sha in event.commits:
        commit = sha_to_commit.get(sha)
        if commit is not None and commit.html_url:
            url = commit.html_url
        else:
            url = f"https://github.com/{event.repository}/commit/{sha}"
        summary = commit.message if commit is not None else ""
        source_refs.append(SourceRef(type="commit", ref=url, summary=summary))
    if not source_refs:
        return None
    facts: list[str] = []
    for claim in (event.problem, event.decision, event.result):
        statement = (claim.statement or "").strip()
        if statement:
            facts.append(statement)
    return FactBundle(
        facts=facts,
        source_refs=source_refs,
        boundaries=_boundaries_from_event(event),
        evidence_status="observed",
        origin="practice",
        source_kind="commit",
    )
```

`to_ideas` 改为 `to_facts`，末尾循环体改为：

```python
        bundles: list[FactBundle] = []
        for event in events:
            if event.id in blocked_event_ids:
                continue
            if not _has_traceable_material(event):
                continue
            bundle = _event_to_facts(event, filtered)
            if bundle is not None:
                bundles.append(bundle)
        return bundles
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/unit/test_commit_service.py -v`
Expected: PASS（此时 CLI 尚未重接，`test_cli_ideas.py` 会失败——见 Step 6 一起解决）

- [ ] **Step 5: 重接 `ideas_commit`**

在 `src/finch/cli.py` 顶部 import 区追加：

```python
from .ideas.divergence import IdeaDiverger
from .ideas.models import Selection
from .storage.repositories import IdeaExplorationRepository
```

把 `ideas_commit` 函数体从 `ideas = CommitService(...).to_ideas(...)` 之后的逻辑改为：

```python
    bundles = CommitService(reader, extractor).to_facts(details, repo=repo)
    diverge = IdeaDiverger(
        cast(CodexRunner, create_runner(settings.llm, "critique") or CodexRunner())
    )
    idea_service = IdeaService(ContentJobRepository(ws))
    exploration_repo = IdeaExplorationRepository(ws)
    jobs: list[ContentJob] = []
    explorations: list[object] = []
    for bundle in bundles:
        exploration = diverge.explore(bundle)
        job: ContentJob | None = None
        if exploration.recommended_index is not None:
            angle = next(
                a for a in exploration.angles if a.index == exploration.recommended_index
            )
            job = idea_service.create_from_angle(
                angle, bundle=bundle, generator=exploration.generator
            )
            exploration.selections.append(
                Selection(index=angle.index, job_id=job.id, at=datetime.now(UTC))
            )
            jobs.append(job)
        exploration_repo.upsert(exploration)
        explorations.append(exploration)
    if as_json:
        payload = [
            {
                "exploration_id": e.id,
                "recommended_index": e.recommended_index,
                "job_id": (e.selections[-1].job_id if e.selections else None),
            }
            for e in explorations
        ]
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        typer.echo("\n\n".join(
            _render_exploration(e, jobs[i] if i < len(jobs) else None)
            for i, e in enumerate(explorations)
        ))
```

（`datetime` / `UTC` 已在 cli.py 顶部导入；`ContentJob` 已导入。）

在 `_render_idea_cards` 附近新增渲染函数：

```python
def _render_exploration(exploration, job: "ContentJob | None") -> str:
    """发散结果的呈现：推荐角度 + 其余角度 + 淘汰角度 + 改选命令。"""
    if not exploration.angles:
        lines = [f"（无角度值得写；淘汰: "
                 + "; ".join(f"[{r.index}] {r.core_point}（{r.reason}）"
                             for r in exploration.rejected_angles) + "）"]
        return "\n".join(lines)
    rec = exploration.recommended_index
    lines = [f"我看出 {len(exploration.angles)} 个可能观点，推荐第 {rec}："]
    for a in exploration.angles:
        marker = "〔推荐〕" if a.index == rec else f"  {a.index}."
        lines.append(f"{marker} {a.core_point}")
        lines.append(f"      情境: {a.reader_situation}")
        lines.append(f"      所得({a.takeaway_kind}): {a.reader_takeaway}")
        lines.append(f"      证据: {a.evidence_support}")
        if a.counterexample_or_limit:
            lines.append(f"      边界: {a.counterexample_or_limit}")
    if exploration.recommendation_reason:
        lines.append(f"推荐理由: {exploration.recommendation_reason}")
    if exploration.rejected_angles:
        lines.append("淘汰: " + "; ".join(
            f"[{r.index}] {r.core_point}（{r.reason}）" for r in exploration.rejected_angles
        ))
    if job is not None:
        lines.append("")
        lines.append(f"已建候选: {job.id} ({job.status.value})")
        lines.append(f"uv run finch ideas confirm {job.id}")
        lines.append(f"改选: uv run finch ideas choose {exploration.id} <index>")
    return "\n".join(lines)
```

- [ ] **Step 6: 改写 `test_cli_ideas.py` 的 commit 测试**

将 `_FakeCommitService` 改为产出 `FactBundle` 并配合 `IdeaDiverger` 的 monkeypatch。把 commit 相关测试（`test_ideas_commit_*`）整体替换为走新链路的版本。关键改动：

```python
class _FakeDiverger:
    def __init__(self, runner):
        self.runner = runner

    def explore(self, bundle):
        from finch.ideas.models import IdeaAngle, IdeaExploration, IdeaGenerator
        angle = IdeaAngle(
            index=1, core_point="make the orchestrator a deterministic graph",
            reader_situation="orchestrator was hard to rerun",
            reader_takeaway="failures can now be replayed",
            takeaway_kind="method", evidence_support="observed_this_run",
            counterexample_or_limit="", source_refs=list(bundle.source_refs),
        )
        return IdeaExploration(
            id="expl_abc12345", origin="practice", source_kind="commit",
            evidence_status="observed", facts=list(bundle.facts),
            source_refs=list(bundle.source_refs), boundaries=bundle.boundaries,
            angles=[angle], rejected_angles=[], recommended_index=1,
            recommendation_reason="可验证", selections=[],
            generator=IdeaGenerator(skill="idea-discovery", version="2.0.0"),
        )


class _FakeCommitService:
    def __init__(self, reader, extractor):
        self.reader = reader
        self.extractor = extractor

    def to_facts(self, commits, *, repo, repo_is_private=False):
        from finch.ideas.models import FactBundle, IdeaBoundaries
        return [FactBundle(
            facts=["make the orchestrator a deterministic graph"],
            source_refs=[SourceRef(type="commit", ref=COMMIT_URL, summary="feat")],
            boundaries=IdeaBoundaries(), evidence_status="observed",
            origin="practice", source_kind="commit",
        )]
```

并更新 `_patch_cli` 加一行 `monkeypatch.setattr(cli, "IdeaDiverger", _FakeDiverger)`。断言改为：

```python
def test_ideas_commit_new_flow(monkeypatch, tmp_path):
    settings = _settings(tmp_path, ["acme/proj"])
    _patch_cli(monkeypatch, settings, [])
    r = CliRunner().invoke(app, ["ideas", "commit"])
    assert r.exit_code == 0, r.output
    assert "我看出 1 个可能观点" in r.output
    assert "make the orchestrator a deterministic graph" in r.output
    assert "uv run finch ideas choose expl_abc12345" in r.output
    jobs = ContentJobRepository(Workspace(settings.paths.var_dir)).list_jobs()
    assert len(jobs) == 1
    assert jobs[0].core_message == "make the orchestrator a deterministic graph"
```

其余旧 commit 测试（JSON 形状、repo 解析、`--repo` 必需、cwd origin 优先）改为按新 JSON 形状断言（`payload[0]["exploration_id"]`、`payload[0]["job_id"]`）。保留 `list/show/confirm/revise-position/skip/create/signals` 测试不变（它们不依赖 `CommitService.to_ideas`）。

- [ ] **Step 7: 运行测试确认通过**

Run: `uv run pytest tests/unit/test_commit_service.py tests/unit/test_cli_ideas.py -v`
Expected: PASS

- [ ] **Step 8: 全量校验**

Run: `uv run ruff check src/finch/ideas src/finch/cli.py tests/unit/test_commit_service.py tests/unit/test_cli_ideas.py && uv run mypy src/finch/ideas`
Expected: 无错误

- [ ] **Step 9: 提交**

```bash
git add src/finch/ideas/commit_service.py src/finch/cli.py tests/unit/test_commit_service.py tests/unit/test_cli_ideas.py
git commit -m "feat(ideas): widen commit gate and wire ideas commit through diverge"
```

---

### Task 6: `finch ideas choose` 命令

**Files:**
- Modify: `src/finch/cli.py`
- Test: `tests/unit/test_cli_ideas.py`（追加测试）

**Interfaces:**
- Consumes: `IdeaExplorationRepository.get`（Task 4）、`IdeaService.create_from_angle`（Task 3）、`FactBundle` / `Selection`（Task 1）。
- Produces: `finch ideas choose <exploration_id> <angle_index>`。

- [ ] **Step 1: 写失败测试**

在 `tests/unit/test_cli_ideas.py` 末尾追加：

```python
# ---- finch ideas choose ----

def _seed_exploration(ws) -> None:
    from finch.ideas.models import FactBundle, IdeaAngle, IdeaExploration, IdeaGenerator, IdeaBoundaries
    bundle = FactBundle(
        facts=["make the orchestrator a deterministic graph"],
        source_refs=[SourceRef(type="commit", ref=COMMIT_URL, summary="feat")],
        boundaries=IdeaBoundaries(), evidence_status="observed",
        origin="practice", source_kind="commit",
    )
    angle1 = IdeaAngle(
        index=1, core_point="make the orchestrator a deterministic graph",
        reader_situation="s", reader_takeaway="t", takeaway_kind="method",
        evidence_support="observed_this_run", counterexample_or_limit="",
        source_refs=list(bundle.source_refs),
    )
    angle2 = angle1.model_copy(update={"index": 2, "core_point": "batch small extracts"})
    IdeaExplorationRepository(Workspace(ws.paths.var_dir)).upsert(IdeaExploration(
        id="expl_abc12345", origin="practice", source_kind="commit",
        evidence_status="observed", facts=list(bundle.facts),
        source_refs=list(bundle.source_refs), boundaries=IdeaBoundaries(),
        angles=[angle1, angle2], rejected_angles=[], recommended_index=1,
        recommendation_reason="可验证", selections=[],
        generator=IdeaGenerator(skill="idea-discovery", version="2.0.0"),
    ))


def test_ideas_choose_creates_second_job(monkeypatch, tmp_path):
    from finch.storage.repositories import IdeaExplorationRepository
    settings = _paths_settings(tmp_path)
    _patch_settings(monkeypatch, settings)
    _seed_exploration(settings)
    r = CliRunner().invoke(app, ["ideas", "choose", "expl_abc12345", "2", "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert payload["job_id"].startswith("idea_")
    jobs = ContentJobRepository(Workspace(settings.paths.var_dir)).list_jobs()
    assert len(jobs) == 1
    assert jobs[0].core_message == "batch small extracts"
    exp = IdeaExplorationRepository(Workspace(settings.paths.var_dir)).get("expl_abc12345")
    assert exp.selections[-1].index == 2


def test_ideas_choose_missing_exploration_exits(monkeypatch, tmp_path):
    settings = _paths_settings(tmp_path)
    _patch_settings(monkeypatch, settings)
    r = CliRunner().invoke(app, ["ideas", "choose", "expl_nope", "1", "--json"])
    assert r.exit_code == 1
    assert "not found" in r.output


def test_ideas_choose_bad_index_exits(monkeypatch, tmp_path):
    settings = _paths_settings(tmp_path)
    _patch_settings(monkeypatch, settings)
    _seed_exploration(settings)
    r = CliRunner().invoke(app, ["ideas", "choose", "expl_abc12345", "9", "--json"])
    assert r.exit_code == 1
    assert "not in exploration" in r.output
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/test_cli_ideas.py -k choose -v`
Expected: FAIL（typer 报「No such command 'choose'」）

- [ ] **Step 3: 实现 `ideas_choose`**

在 `src/finch/cli.py` 的 `ideas_signals` 之后插入（`IdeaExplorationRepository`、`Selection`、`FactBundle` 已由 Task 5 引入；再补 `FactBundle` import 到顶部）：

```python
@ideas_app.command("choose")
def ideas_choose(
    exploration_id: str = typer.Argument(..., help="exploration id（finch ideas commit 输出）"),
    angle_index: int = typer.Argument(..., help="要选择的角度序号（1-based）"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """从一次发散中选择（或改选）一个角度，为其创建 ContentJob。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    exploration_repo = IdeaExplorationRepository(ws)
    exploration = exploration_repo.get(exploration_id)
    if exploration is None:
        typer.echo(f"exploration not found: {exploration_id}")
        raise typer.Exit(code=1)
    angle = next((a for a in exploration.angles if a.index == angle_index), None)
    if angle is None:
        typer.echo(f"angle {angle_index} not in exploration {exploration_id}")
        raise typer.Exit(code=1)
    bundle = FactBundle(
        facts=list(exploration.facts),
        source_refs=list(exploration.source_refs),
        boundaries=exploration.boundaries,
        evidence_status=exploration.evidence_status or "observed",
        origin=exploration.origin,
        source_kind=exploration.source_kind or "commit",
    )
    job = IdeaService(ContentJobRepository(ws)).create_from_angle(
        angle, bundle=bundle, generator=exploration.generator
    )
    exploration.selections.append(
        Selection(index=angle.index, job_id=job.id, at=datetime.now(UTC))
    )
    exploration_repo.upsert(exploration)
    if as_json:
        typer.echo(json.dumps(
            {"exploration_id": exploration.id, "job_id": job.id, "status": job.status.value},
            ensure_ascii=False, indent=2,
        ))
    else:
        typer.echo(f"已选择角度 {angle.index}: {job.id} ({job.status.value})")
        typer.echo(f"uv run finch ideas confirm {job.id}")
```

顶部 import 追加 `from .ideas.models import FactBundle`（`Selection` 已在 Task 5 Step 5 引入，最终该行为 `from .ideas.models import FactBundle, Selection`）。

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/unit/test_cli_ideas.py -k choose -v`
Expected: PASS

- [ ] **Step 5: 全量校验与提交**

```bash
uv run ruff check src/finch/cli.py tests/unit/test_cli_ideas.py
git add src/finch/cli.py tests/unit/test_cli_ideas.py
git commit -m "feat(ideas): finch ideas choose to re-select an angle"
```

---

### Task 7: Skill 文档 + evals 更新

**Files:**
- Modify: `skills/idea-discovery/SKILL.md`
- Modify: `skills/idea-discovery/references/presentation.md`
- Modify: `skills/idea-discovery/references/commit-signals.md`
- Modify: `skills/idea-discovery/evals/cases.yaml`

**Interfaces:**
- Consumes: 无（纯文档）。
- Produces: 文档与 eval 契约一致，供后续回放对比。

- [ ] **Step 1: 更新 `SKILL.md`**

把「本 Skill … 提炼成一个值得继续发展的 Idea」的描述改为「先发散成 2–4 个角度，再收敛到有证据、能供别人使用的一个」；「四种来源」的 commit 条目注明「先发散」；「产出契约」补一句 commit 路径经 `IdeaDiverger` 发散、fragment/conversation/signals 仍单候选。具体替换：

- 第 12–14 行「三种来源共用一份判断：…→ 空。」改为：

```markdown
把个人证据、零散思考或真实交流发散成多个可讨论的角度，再收敛到**一个**有证据、
能供别人使用、可迁移的 `IdeaCandidate`。commit 来源先经 `IdeaDiverger` 发散；
fragment / conversation / signals 仍单候选。机械变化、新闻、纯情绪 → 空。
```

- 「四种来源」的 commit 条目（第 21–22 行）末尾追加一句：「发散后按 `references/presentation.md` 呈现 2–4 个角度与推荐，改选用 `finch ideas choose`。」

- [ ] **Step 2: 更新 `presentation.md`**

把「形状」代码块替换为新形状：

```markdown
```text
这段材料我看出 3 个可能观点；我推荐第 2 个，因为它能给同样处理批量任务的人一个
可验证的改法。不过，性能收益目前只由这次运行支持。

〔推荐〕2. 小型、重复的结构化提取可以考虑批处理
   情境 / 所得 / 证据 / 边界 …
  1. 并发任务慢，先拆分推理时间与进程启动时间
  3. 批处理会放大单次失败的影响，需要限定批大小
（淘汰：…）

回复「写 2」「改选 1」「展开 2」或「换一批」。
```
```

「用户下一轮 → CLI」映射表更新为：`写 N` → `uv run finch ideas confirm {id}`；`改选 N` → `uv run finch ideas choose {exploration_id} N`；`展开 N` → `uv run finch ideas show {id}`。

- [ ] **Step 3: 更新 `commit-signals.md`**

「三问」小节后补一句：「发散时不再要求三问都有明确答案；problem / decision / result 任一有可追溯内容即可进入发散，发散再分别寻找反常现象、失败与修正、设计取舍、可复用方法、未解决问题。」（对应门禁放宽。）

- [ ] **Step 4: 更新 `evals/cases.yaml`**

case 1 的 `expected_output.ideas` 语义改为「1 个 bundle → 发散 1 角度 → 1 job」不变，但补充注释说明新链路；其余 case 不变（机械/私有仍 0）。可选：新增 case 描述「decision UNKNOWN 但 result 有内容 → 仍发散」。

- [ ] **Step 5: 提交**

```bash
git add skills/idea-discovery/SKILL.md skills/idea-discovery/references/presentation.md skills/idea-discovery/references/commit-signals.md skills/idea-discovery/evals/cases.yaml
git commit -m "docs(idea-discovery): document diverge/verify/converge flow"
```

---

## 验收（回放对比，非自动评分）

在全部任务完成后，取近期 commit / 对话 / 社区信号各 2–3 条做**人工回放**，逐条比较新旧结果并记录（不落代码）：

1. 是否发现原「单决策」流程漏掉的好观点；
2. 推荐理由是否具体（说出可迁移情境与所得类型）；
3. 有没有把推测写成事实（`evidence_support` 与 `evidence_status` 是否诚实）；
4. 用户是否愿意拿它与同行讨论。

结论写入一次性的回放记录（如 `docs/superpowers/specs/` 下的 follow-up 笔记，或口头结论），据此决定第二阶段是否把 fragment / conversation / signals 接入共享发散接口。
