# community-scout 薄 Loop + 反馈闭环 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 community-scout 补一个有界、确定性的 Python 编排 loop（`finch community run`）与可复盘的 per-step 决策记录，并把 `CommunityFeedback` 确定性回灌到下一趟推荐。

**Architecture:** `CommunityLoop` 是一个有界状态机，固定序执行 `search → inspect → propose → finish`；LLM（`StructuredInferenceRunner`）只在 `inspect`（逐候选判定证据/可参与性）与 `propose`（写 ≤3 张卡）两个判断点单发调用，不选动作。`search` 依赖一个窄接口 `CommunitySearchSource`（协议），真实实现先做 WebFetcher 最小版，多源映射延后。反馈回灌由纯函数 `derive_feedback_facts` 完成（硬门禁 + 软排序摘要）。决策记录 `CommunityRun` + `RunStep` 落 append-only JSONL。

**Tech Stack:** Python 3.12, Pydantic 2, typer, pytest (TDD), 文件 Workspace（JSONL 原子追加）。

## Global Constraints

- Python 3.12+; Pydantic 2 (`BaseModel`/`Field`/`StrEnum`); Ruff selects `E,F,I,B,UP`, line-length 100, py312; alembic 脚本排除在 lint 外。
- **确定性总量**：预算、去重、门禁、状态、trace 全在 Python；LLM 输出不带 `total`、不选动作。
- **不自动加入/发言/外发**：`search` 只读；`finish` 只 `save` 建议卡，用户亲自执行下一步。
- **不恢复 Graph Runtime / 不做通用 agent loop**：loop 固定序，Python 决定下一步。
- Bilingual (Chinese/English) docstrings 常见；匹配周围文件。
- 命令：`uv run pytest`、`uv run ruff check .`、`uv run mypy src`。
- **延后（非本 plan）**：twitter/reddit/v2ex/GitHub-Discussions 的多源社区发现（现有 `sources/` 层投影到 peers 而非 communities，映射非平凡）；本 plan 的 `search` 真实实现是 WebFetcher 最小版（见 Task 7），其余如实记 `gap_note`。

---

### Task 1: 配置 `community_scout`

**Files:**
- Modify: `src/finch/settings.py`（新增 `CommunityScoutSettings` + `Settings.community_scout` 字段）
- Modify: `finch.yaml`（新增 `community_scout` 段）
- Test: `tests/unit/test_settings.py`（若不存在则新建 `tests/unit/test_community_settings.py`）

**Interfaces:**
- Produces: `CommunityScoutSettings`（字段 `max_candidates=20` / `inspect_batch=6` / `max_cards=3` / `max_reinspect_rounds=1` / `suppress_window_weeks=4` / `search_urls=[]`），挂在 `Settings.community_scout`。

- [ ] **Step 1: 写失败测试**

新建 `tests/unit/test_community_settings.py`：

```python
from finch.settings import CommunityScoutSettings, Settings


def test_community_scout_defaults():
    s = CommunityScoutSettings()
    assert s.max_candidates == 20
    assert s.inspect_batch == 6
    assert s.max_cards == 3
    assert s.max_reinspect_rounds == 1
    assert s.suppress_window_weeks == 4
    assert s.search_urls == []


def test_settings_carries_community_scout():
    settings = Settings()
    assert settings.community_scout.max_cards == 3
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/test_community_settings.py -v`
Expected: FAIL — `CommunityScoutSettings` 未定义（ImportError）。

- [ ] **Step 3: 实现**

在 `src/finch/settings.py` 的 `ExtractionSettings` 之后（`QualityGates` 之前或之后均可，放 `QualityGates` 之后）加：

```python
class CommunityScoutSettings(BaseModel):
    """community-scout 薄 loop 预算（把 SKILL 散文里的「试运行参数」提升为可配）。"""

    max_candidates: int = Field(default=20, ge=1)
    inspect_batch: int = Field(default=6, ge=1)
    max_cards: int = Field(default=3, ge=1)
    max_reinspect_rounds: int = Field(default=1, ge=0)
    suppress_window_weeks: int = Field(default=4, ge=0)
    search_urls: list[str] = Field(default_factory=list)
```

并在 `Settings` 里加字段（紧跟 `quality_gates` 之后）：

```python
    community_scout: CommunityScoutSettings = Field(default_factory=CommunityScoutSettings)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/unit/test_community_settings.py -v`
Expected: PASS。

- [ ] **Step 5: 在 `finch.yaml` 加配置段**

在文件末尾追加：

```yaml
community_scout:
  max_candidates: 20
  inspect_batch: 6
  max_cards: 3
  max_reinspect_rounds: 1
  suppress_window_weeks: 4
  search_urls: []
```

- [ ] **Step 6: Commit**

```bash
git add src/finch/settings.py finch.yaml tests/unit/test_community_settings.py
git commit -m "feat(community-scout): add community_scout budget settings"
```

---

### Task 2: 领域模型（loop / trace / feedback facts）

**Files:**
- Modify: `src/finch/communities/models.py`（新增 6 个模型）
- Test: `tests/unit/test_communities.py`（追加）

**Interfaces:**
- Produces: `RunIntent`（`WEEKLY/QUESTION/REVISIT`）、`ScoutAction`（`SEARCH/INSPECT/PROPOSE/FINISH`）、`CommunityCandidate`、`ScoutObservation`、`RunStep`、`CommunityRun`、`FeedbackFacts`。后文 Task 3–8 全部消费这些名字。

- [ ] **Step 1: 写失败测试**

在 `tests/unit/test_communities.py` 追加：

```python
from finch.communities.models import (
    CommunityCandidate,
    CommunityRun,
    FeedbackFacts,
    RunIntent,
    RunStep,
    ScoutAction,
    ScoutObservation,
)


def test_run_step_roundtrip():
    step = RunStep(
        run_id="r1",
        action=ScoutAction.SEARCH,
        observation=ScoutObservation(
            candidates=[CommunityCandidate(name="Temporal", canonical_url="https://temporal.io")]
        ),
        decision="kept 1 after excluding 0",
        outcome="candidates_found=1",
        at=datetime(2026, 9, 29, tzinfo=UTC),
        llm_calls=0,
    )
    back = RunStep.model_validate(step.model_dump(mode="json"))
    assert back.action == ScoutAction.SEARCH
    assert back.observation.candidates[0].name == "Temporal"
    assert back.llm_calls == 0


def test_community_run_roundtrip():
    run = CommunityRun(
        run_id="r1",
        intent=RunIntent.WEEKLY,
        goal="找 agent reliability 社区",
        week="2026-W40",
        status="done",
        candidates_found=5,
        cards_proposed=2,
        budget_used=5,
        started_at=datetime(2026, 9, 29, tzinfo=UTC),
        finished_at=datetime(2026, 9, 29, tzinfo=UTC),
    )
    assert CommunityRun.model_validate(run.model_dump(mode="json")) == run


def test_feedback_facts_defaults():
    f = FeedbackFacts()
    assert f.excluded == {}
    assert f.continue_framing == []
    assert f.summaries == {}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/test_communities.py -k "run_step or community_run or feedback_facts" -v`
Expected: FAIL — ImportError（模型未定义）。

- [ ] **Step 3: 实现**

在 `src/finch/communities/models.py` 的 `identity_key` 之后追加：

```python
class RunIntent(StrEnum):
    """community-scout 三种入口（对齐 SKILL）。"""

    WEEKLY = "weekly"
    QUESTION = "question"
    REVISIT = "revisit"


class ScoutAction(StrEnum):
    """薄 loop 的动作集（固定序；Python 决定下一步，LLM 不选动作）。"""

    SEARCH = "search"
    INSPECT = "inspect"
    PROPOSE = "propose"
    FINISH = "finish"


class CommunityCandidate(BaseModel):
    """search 动作产出的一个候选社区（尚未经 inspect 核验）。"""

    name: str
    canonical_url: str = ""
    source_note: str = ""
    evidence_text: str = ""


class ScoutObservation(BaseModel):
    """一个动作的结构化观察（按 action 不同只填相关字段）。"""

    candidates: list[CommunityCandidate] = Field(default_factory=list)
    verified: list[CommunityProfile] = Field(default_factory=list)
    rejected: list[dict] = Field(default_factory=list)  # {name, reason}
    cards: list[CommunityProfile] = Field(default_factory=list)
    gap_note: str = ""


class RunStep(BaseModel):
    """决策记录一行：goal/action/observation/decision/outcome + 耗时/调用次数。"""

    run_id: str
    action: ScoutAction
    observation: ScoutObservation = Field(default_factory=ScoutObservation)
    decision: str = ""
    outcome: str = ""
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    elapsed_ms: int = 0
    llm_calls: int = 0


class CommunityRun(BaseModel):
    """一次 run 头：intent/goal/状态/预算消耗/卡数。"""

    run_id: str
    intent: RunIntent
    goal: str
    week: str
    status: str  # running | done | failed | stopped
    candidates_found: int = 0
    cards_proposed: int = 0
    budget_used: int = 0
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None


class FeedbackFacts(BaseModel):
    """从 feedback 派生的确定性事实（硬门禁 + 软排序摘要）。"""

    excluded: dict[str, str] = Field(default_factory=dict)  # identity_key -> 原因
    continue_framing: list[str] = Field(default_factory=list)  # 回访须「继续」框架的 identity
    summaries: dict[str, str] = Field(default_factory=dict)  # identity_key -> 摘要
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/unit/test_communities.py -k "run_step or community_run or feedback_facts" -v`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add src/finch/communities/models.py tests/unit/test_communities.py
git commit -m "feat(community-scout): add loop/run/feedback-facts models"
```

---

### Task 3: 仓库持久化（runs / steps JSONL）

**Files:**
- Modify: `src/finch/communities/repository.py`
- Test: `tests/unit/test_communities.py`（追加）

**Interfaces:**
- Consumes: `CommunityRun`、`RunStep`（Task 2）。
- Produces: `CommunityRepository.append_run(run) / list_runs() / get_run(run_id) / append_step(step) / list_steps(run_id)`。

- [ ] **Step 1: 写失败测试**

在 `tests/unit/test_communities.py` 追加：

```python
def test_runs_and_steps_roundtrip(tmp_path):
    from finch.communities.repository import CommunityRepository

    repo = CommunityRepository(Workspace(tmp_path))
    run = CommunityRun(
        run_id="r1", intent=RunIntent.WEEKLY, goal="g", week="2026-W40", status="done"
    )
    repo.append_run(run)
    assert repo.get_run("r1") == run
    assert [r.run_id for r in repo.list_runs()] == ["r1"]

    step = RunStep(run_id="r1", action=ScoutAction.SEARCH, decision="d")
    repo.append_step(step)
    repo.append_step(RunStep(run_id="r1", action=ScoutAction.FINISH, decision="f"))
    assert [s.action for s in repo.list_steps("r1")] == [
        ScoutAction.SEARCH,
        ScoutAction.FINISH,
    ]


def test_get_run_missing(tmp_path):
    from finch.communities.repository import CommunityRepository

    assert CommunityRepository(Workspace(tmp_path)).get_run("nope") is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/test_communities.py -k "runs_and_steps or get_run_missing" -v`
Expected: FAIL — `CommunityRepository` 无这些方法。

- [ ] **Step 3: 实现**

在 `src/finch/communities/repository.py` 的 import 里把 `CommunityRun`、`RunStep` 加进
`from finch.communities.models import (...)`，并在 `report_path` 之后追加：

```python
    # ---- runs / steps (append-only JSONL) ----
    def append_run(self, run: CommunityRun) -> None:
        self.ws.append_jsonl(self._dir / "runs.jsonl", run.model_dump(mode="json"))

    def list_runs(self) -> list[CommunityRun]:
        out: list[CommunityRun] = []
        for row in self.ws.read_jsonl(self._dir / "runs.jsonl"):
            try:
                out.append(CommunityRun.model_validate(row))
            except ValidationError:
                continue
        return out

    def get_run(self, run_id: str) -> CommunityRun | None:
        return next((r for r in reversed(self.list_runs()) if r.run_id == run_id), None)

    def append_step(self, step: RunStep) -> None:
        self.ws.append_jsonl(self._dir / "steps.jsonl", step.model_dump(mode="json"))

    def list_steps(self, run_id: str) -> list[RunStep]:
        out: list[RunStep] = []
        for row in self.ws.read_jsonl(self._dir / "steps.jsonl"):
            try:
                s = RunStep.model_validate(row)
            except ValidationError:
                continue
            if s.run_id == run_id:
                out.append(s)
        return out
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/unit/test_communities.py -k "runs_and_steps or get_run_missing" -v`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add src/finch/communities/repository.py tests/unit/test_communities.py
git commit -m "feat(community-scout): persist runs and steps as append-only JSONL"
```

---

### Task 4: 反馈回灌纯函数 `derive_feedback_facts`

**Files:**
- Create: `src/finch/communities/scout.py`（本 task 只放 `derive_feedback_facts`，Task 5/6 再扩展）
- Test: `tests/unit/test_community_scout.py`（新建）

**Interfaces:**
- Consumes: `CommunityFeedback`、`CommunityResult`、`FeedbackFacts`（Task 2）。
- Produces: `derive_feedback_facts(latest_by_identity: dict[str, CommunityFeedback], *, now=None, suppress_window=timedelta(weeks=4)) -> FeedbackFacts`。Task 6 的 loop 消费。

- [ ] **Step 1: 写失败测试**

新建 `tests/unit/test_community_scout.py`：

```python
from datetime import UTC, datetime, timedelta

from finch.communities.models import CommunityFeedback, CommunityResult, FeedbackFacts
from finch.communities.scout import derive_feedback_facts


def _fb(result: CommunityResult, *, days_ago: int = 0, reason: str = "", note: str = ""):
    return CommunityFeedback(
        community_id="comm_x",
        result=result,
        reason_kind=reason,
        note=note,
        at=datetime(2026, 9, 29, tzinfo=UTC) - timedelta(days=days_ago),
    )


def test_ignored_within_window_is_excluded():
    now = datetime(2026, 9, 29, tzinfo=UTC)
    facts = derive_feedback_facts({"k": _fb(CommunityResult.IGNORED, days_ago=7)}, now=now)
    assert "k" in facts.excluded
    assert facts.excluded["k"] == "ignored 1w ago"


def test_ignored_beyond_window_not_excluded():
    now = datetime(2026, 9, 29, tzinfo=UTC)
    facts = derive_feedback_facts(
        {"k": _fb(CommunityResult.IGNORED, days_ago=35)}, now=now
    )
    assert "k" not in facts.excluded


def test_no_time_never_excluded():
    now = datetime(2026, 9, 29, tzinfo=UTC)
    facts = derive_feedback_facts(
        {"k": _fb(CommunityResult.SAVED, days_ago=1, reason="no_time")}, now=now
    )
    assert "k" not in facts.excluded


def test_engaged_results_enter_continue_framing():
    now = datetime(2026, 9, 29, tzinfo=UTC)
    facts = derive_feedback_facts({"k": _fb(CommunityResult.INTERACTED, days_ago=2)}, now=now)
    assert facts.continue_framing == ["k"]


def test_summaries_carry_reason_and_note():
    now = datetime(2026, 9, 29, tzinfo=UTC)
    facts = derive_feedback_facts(
        {"k": _fb(CommunityResult.SAVED, days_ago=3, reason="deep_but_later", note="先观察")},
        now=now,
    )
    assert "deep_but_later" in facts.summaries["k"]
    assert "先观察" in facts.summaries["k"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/test_community_scout.py -v`
Expected: FAIL — `derive_feedback_facts` 未定义。

- [ ] **Step 3: 实现**

新建 `src/finch/communities/scout.py`：

```python
"""community-scout 薄 loop：反馈回灌纯函数 + 编排器（见 CommunityLoop）。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from finch.communities.models import (
    CommunityFeedback,
    CommunityResult,
    FeedbackFacts,
)

_ENGAGED = {
    CommunityResult.JOINED,
    CommunityResult.INTERACTED,
    CommunityResult.REPEATED,
    CommunityResult.CONTRIBUTED,
}


def derive_feedback_facts(
    latest_by_identity: dict[str, CommunityFeedback],
    *,
    now: datetime | None = None,
    suppress_window: timedelta = timedelta(weeks=4),
) -> FeedbackFacts:
    """由「每个 identity 的最新一条 feedback」派生出确定性事实。

    硬门禁（Python 强制）：
    - ``ignored`` 在 suppress_window 内 → 排除；
    - ``no_time`` 永不排除（当次约束，不永久过滤）；
    - 已发生互动（joined/interacted/repeated/contributed）→ 进入「继续」框架。
    软排序：每个 identity 的「result + reason_kind + note」摘要，注入 judge。
    """
    clock = now or datetime.now(UTC)
    facts = FeedbackFacts()
    for identity, fb in latest_by_identity.items():
        if fb.result == CommunityResult.IGNORED:
            age = clock - fb.at
            if age < suppress_window:
                weeks = max(1, int(age.days / 7))
                facts.excluded[identity] = f"ignored {weeks}w ago"
        if fb.result in _ENGAGED:
            facts.continue_framing.append(identity)
        summary = " ".join(
            p for p in [fb.result.value, fb.reason_kind, fb.note] if p
        ).strip()
        facts.summaries[identity] = summary
    return facts
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/unit/test_community_scout.py -v`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add src/finch/communities/scout.py tests/unit/test_community_scout.py
git commit -m "feat(community-scout): derive feedback facts for hard-gate + soft-rank"
```

---

### Task 5: search 源协议 + 候选 identity 助手 + Fake

**Files:**
- Modify: `src/finch/communities/scout.py`（新增 `CommunitySearchSource` 协议 + `candidate_identity`）
- Test: `tests/unit/test_community_scout.py`（追加）

**Interfaces:**
- Consumes: `CommunityCandidate`、`RunIntent`、`community_id_for`（models.py）。
- Produces: `CommunitySearchSource.search(intent, goal, limit) -> list[CommunityCandidate]`（协议）、`candidate_identity(c) -> str`。Task 6 loop 与 Task 7 真实源消费。

- [ ] **Step 1: 写失败测试**

在 `tests/unit/test_community_scout.py` 追加：

```python
from finch.communities.models import CommunityCandidate, RunIntent
from finch.communities.scout import candidate_identity


def test_candidate_identity_prefers_canonical_url():
    assert (
        candidate_identity(CommunityCandidate(name="Temporal", canonical_url="https://t.io"))
        == "https://t.io"
    )


def test_candidate_identity_falls_back_to_name_hash():
    c = CommunityCandidate(name="Temporal")
    assert candidate_identity(c) == candidate_identity(CommunityCandidate(name="Temporal"))
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/test_community_scout.py -k candidate_identity -v`
Expected: FAIL — `candidate_identity` 未定义。

- [ ] **Step 3: 实现**

在 `src/finch/communities/scout.py` 的 import 里加 `CommunityCandidate`、`RunIntent`、`community_id_for`，
并在文件里追加：

```python
def candidate_identity(c: CommunityCandidate) -> str:
    """候选的跨周去重键：有规范 URL 用 URL，否则回退 name-hash id（与 identity_key 一致）。"""
    return c.canonical_url or community_id_for(c.name)


class CommunitySearchSource(Protocol):
    """search 动作依赖的窄接口：给定意图与目标，返回有界候选列表（只读）。"""

    def search(self, intent: RunIntent, goal: str, limit: int) -> list[CommunityCandidate]: ...
```

顶部 import 补 `from typing import Protocol`。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/unit/test_community_scout.py -k candidate_identity -v`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add src/finch/communities/scout.py tests/unit/test_community_scout.py
git commit -m "feat(community-scout): add search-source protocol and candidate identity"
```

---

### Task 6: `CommunityLoop`（动作机 + inspect/propose 判断点）

**Files:**
- Modify: `src/finch/communities/scout.py`（新增 `InspectOutput` / `ProposeOutput` / `CommunityLoop`）
- Test: `tests/unit/test_community_scout.py`（追加；用 FakeRunner + FakeSearchSource）

**Interfaces:**
- Consumes: `StructuredInferenceRunner`（`finch.llm.base`）、`CommunityRepository`（Task 3）、`CommunitySearchSource`（Task 5）、`CommunityScoutSettings`（Task 1）、`derive_feedback_facts`（Task 4）、`CommunityProfile`/`RecommendationState`/`CommunityEvidence`/`EntryPoint`/`week_label`。
- Produces: `CommunityLoop(runner, search_source, repo, *, budget)`，方法 `run(intent, goal, *, now=None) -> CommunityRun`。Task 8 CLI 消费。

- [ ] **Step 1: 写失败测试**

在 `tests/unit/test_community_scout.py` 追加：

```python
from finch.communities.models import (
    CommunityCandidate,
    CommunityProfile,
    CommunityRun,
    RecommendationState,
    RunIntent,
    ScoutAction,
)
from finch.communities.repository import CommunityRepository
from finch.communities.scout import CommunityLoop
from finch.settings import CommunityScoutSettings
from finch.storage.workspace import Workspace


class FakeRunner:
    def __init__(self):
        self.calls = 0
        self.last_model = None

    def run(self, prompt, output_model, **kw):
        self.calls += 1
        self.last_model = output_model
        return _fake_output(output_model)


def _fake_output(model):
    # 按 output_model 类型回传对应结构（inspect → 一个 verified 候选；propose → 一张卡）。
    if model.__name__ == "InspectOutput":
        from finch.communities.scout import InspectOutput, InspectedCandidate

        return InspectOutput(
            candidates=[
                InspectedCandidate(
                    name="Temporal",
                    canonical_url="https://temporal.io/community",
                    recommendation_state=RecommendationState.ACTIONABLE,
                    why_fit=["durable execution"],
                    evidence_urls=["https://temporal.io/community"],
                )
            ]
        )
    from finch.communities.scout import ProposeOutput

    return ProposeOutput(
        cards=[
            CommunityProfile(
                name="Temporal",
                canonical_url="https://temporal.io/community",
                recommendation_state=RecommendationState.ACTIONABLE,
                why_fit=["durable execution"],
                evidence_urls=["https://temporal.io/community"],
            )
        ]
    )


class FakeSearchSource:
    def __init__(self, candidates):
        self.candidates = candidates
        self.calls = 0

    def search(self, intent, goal, limit):
        self.calls += 1
        return self.candidates


def _loop(tmp_path, candidates, **budget_overrides):
    repo = CommunityRepository(Workspace(tmp_path))
    budget = CommunityScoutSettings(**budget_overrides)
    return (
        CommunityLoop(FakeRunner(), FakeSearchSource(candidates), repo, budget=budget),
        repo,
    )


def test_loop_runs_fixed_action_order(tmp_path):
    loop, repo = _loop(
        tmp_path,
        [CommunityCandidate(name="Temporal", canonical_url="https://temporal.io/community")],
    )
    run = loop.run(RunIntent.WEEKLY, "找社区")
    actions = [s.action for s in repo.list_steps(run.run_id)]
    assert actions == [
        ScoutAction.SEARCH,
        ScoutAction.INSPECT,
        ScoutAction.PROPOSE,
        ScoutAction.FINISH,
    ]
    assert run.status == "done"
    assert run.cards_proposed == 1


def test_loop_excludes_ignored_candidate(tmp_path):
    from finch.communities.models import CommunityFeedback, CommunityResult

    loop, repo = _loop(
        tmp_path,
        [
            CommunityCandidate(name="Ignored Community", canonical_url="https://ig.io"),
            CommunityCandidate(name="Temporal", canonical_url="https://temporal.io/community"),
        ],
    )
    # 预置一条「ignored」反馈，使 https://ig.io 被硬门禁排除。
    repo.append_feedback(
        CommunityFeedback(community_id="comm_x", result=CommunityResult.IGNORED)
    )
    run = loop.run(RunIntent.WEEKLY, "找社区")
    search_step = repo.list_steps(run.run_id)[0]
    names = [c.name for c in search_step.observation.candidates]
    assert "Ignored Community" not in names
    assert "Temporal" in names


def test_loop_reinspects_once_when_all_rejected(tmp_path):
    from finch.communities.scout import InspectOutput, InspectedCandidate

    class RejectThenVerifyRunner(FakeRunner):
        def run(self, prompt, output_model, **kw):
            self.calls += 1
            if output_model.__name__ == "InspectOutput" and self.calls == 1:
                return InspectOutput(
                    candidates=[InspectedCandidate(name="X", reject_reason="无公开证据")]
                )
            return super().run(prompt, output_model, **kw)

    loop, repo = _loop(
        tmp_path,
        [
            CommunityCandidate(name="A"),
            CommunityCandidate(name="B"),
            CommunityCandidate(name="C"),
        ],
        inspect_batch=1,
    )
    loop.runner = RejectThenVerifyRunner()
    run = loop.run(RunIntent.WEEKLY, "找社区")
    inspects = [s for s in repo.list_steps(run.run_id) if s.action == ScoutAction.INSPECT]
    assert len(inspects) == 2  # 本批全淘汰 → 再 inspect 下一批一次
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/test_community_scout.py -k "loop_" -v`
Expected: FAIL — `CommunityLoop` / `InspectOutput` 未定义。

- [ ] **Step 3: 实现**

在 `src/finch/communities/scout.py` 追加（import 补 `CommunityProfile`、`RecommendationState`、`CommunityEvidence`、`EntryPoint`、`CommunityScoutSettings`、`CommunityRepository`、`week_label`，`from finch.llm.base import StructuredInferenceRunner`）：

```python
_INSPECT_PROMPT = """\
You are checking whether each candidate community is worth entering or observing for the user.

For each candidate, judge from the provided evidence_text whether:
- there is verifiable public evidence of recent, relevant discussion (not just a homepage);
- there is an open entry point (a still-open discussion) or only reading material;
- the community matches the goal.

If it cannot be verified (no public evidence, stale, or unverifiable), set reject_reason to a
short reason. Otherwise set recommendation_state (observe if no current entry point, actionable
if there is a usable one), why_fit, evidence_urls, and entry_point when available. Do not invent
URLs or evidence; leave fields empty when unknown. Never mark anything "confirmed" or a real
interaction.

Goal: {goal}
Feedback summaries (latest per community; for soft ranking only): {summaries}

## Candidates

{candidates}

Return JSON matching the schema.
"""

_PROPOSE_PROMPT = """\
You write up to {max_cards} community cards from verified candidates. Order by problem fit and
a concrete next step. Each card needs name, canonical_url, recommendation_state, why_fit,
evidence_urls, and (when actionable) entry_point. Do not invent first-person experience or
interaction facts; evidence_urls must be the real public URLs from the candidate. If a candidate
is in the continue-framing list, frame the next step as continuing the discussion, never "first
join".

Continue-framing identities: {continue_framing}

## Verified candidates

{verified}

Return JSON matching the schema.
"""


class InspectedCandidate(BaseModel):
    """inspect 判定后的一个候选：reject_reason 非空即淘汰。"""

    name: str
    canonical_url: str = ""
    recommendation_state: RecommendationState | None = None
    why_fit: list[str] = Field(default_factory=list)
    recent_evidence: list[CommunityEvidence] = Field(default_factory=list)
    entry_point: EntryPoint | None = None
    evidence_urls: list[str] = Field(default_factory=list)
    reject_reason: str = ""


class InspectOutput(BaseModel):
    candidates: list[InspectedCandidate] = Field(default_factory=list)


class ProposeOutput(BaseModel):
    cards: list[CommunityProfile] = Field(default_factory=list)


def _render_candidates(candidates: list[CommunityCandidate]) -> str:
    return "\n".join(
        f"- {c.name} | {c.canonical_url or '(no url)'} | {c.source_note}\n  {c.evidence_text[:400]}"
        for c in candidates
    )


def _render_verified(profiles: list[CommunityProfile]) -> str:
    return "\n".join(
        f"- {p.name} | {p.canonical_url} | {p.recommendation_state.value if p.recommendation_state else '-'}"
        for p in profiles
    )


class CommunityLoop:
    """有界确定性 loop：search → inspect → propose → finish。

    LLM 只在 inspect（核验/分层）与 propose（写卡）两个判断点被单发调用；Python 决定
    下一步、预算、去重、门禁与 trace。
    """

    def __init__(
        self,
        runner: StructuredInferenceRunner,
        search_source: CommunitySearchSource,
        repo: CommunityRepository,
        *,
        budget: CommunityScoutSettings,
    ) -> None:
        self.runner = runner
        self.search_source = search_source
        self.repo = repo
        self.budget = budget

    def run(self, intent: RunIntent, goal: str, *, now: datetime | None = None) -> CommunityRun:
        clock = now or datetime.now(UTC)
        run_id = f"run_{clock.strftime('%Y%m%d%H%M%S')}_{hashlib.sha256(goal.encode()).hexdigest()[:6]}"
        run = CommunityRun(
            run_id=run_id, intent=intent, goal=goal, week=week_label(clock), status="running"
        )
        self.repo.append_run(run)

        facts = derive_feedback_facts(
            self.repo.latest_feedback_by_identity(),
            now=clock,
            suppress_window=timedelta(weeks=self.budget.suppress_window_weeks),
        )

        # search
        raw = self.search_source.search(intent, goal, self.budget.max_candidates)
        kept = [c for c in raw if candidate_identity(c) not in facts.excluded]
        self._step(
            run_id,
            ScoutAction.SEARCH,
            ScoutObservation(
                candidates=kept,
                gap_note=f"excluded {len(raw) - len(kept)} by hard gate",
            ),
            decision=f"kept {len(kept)} after hard gate",
            outcome=f"candidates_found={len(kept)}",
            llm_calls=0,
        )
        run = run.model_copy(update={"candidates_found": len(kept)})

        # inspect（有界：最多 max_reinspect_rounds 次「再 inspect 下一批」）
        verified: list[CommunityProfile] = []
        rejected: list[dict] = []
        inspected = 0
        reinspect_rounds = 0
        while inspected < len(kept) and reinspect_rounds <= self.budget.max_reinspect_rounds:
            batch = kept[inspected : inspected + self.budget.inspect_batch]
            out = cast(InspectOutput, self.runner.run(
                _INSPECT_PROMPT.format(
                    goal=goal,
                    summaries="\n".join(f"{k}: {v}" for k, v in facts.summaries.items()) or "(none)",
                    candidates=_render_candidates(batch),
                ),
                InspectOutput,
            ))
            batch_verified: list[CommunityProfile] = []
            for ic in out.candidates:
                if ic.reject_reason:
                    rejected.append({"name": ic.name, "reason": ic.reject_reason})
                else:
                    batch_verified.append(
                        CommunityProfile(
                            name=ic.name,
                            canonical_url=ic.canonical_url,
                            recommendation_state=ic.recommendation_state,
                            why_fit=ic.why_fit,
                            recent_evidence=ic.recent_evidence,
                            entry_point=ic.entry_point,
                            evidence_urls=ic.evidence_urls,
                        )
                    )
            verified.extend(batch_verified)
            inspected += len(batch)
            self._step(
                run_id,
                ScoutAction.INSPECT,
                ScoutObservation(verified=list(batch_verified), rejected=[
                    r for r in rejected if r["name"] in {c.name for c in batch}
                ]),
                decision=f"verified {len(batch_verified)} / rejected {len(batch) - len(batch_verified)}",
                outcome=f"inspected={inspected}",
                llm_calls=1,
            )
            if batch_verified:
                break  # 有通过核验的候选，不再 re-inspect
            reinspect_rounds += 1

        # propose
        cards: list[CommunityProfile] = []
        if verified:
            out = cast(ProposeOutput, self.runner.run(
                _PROPOSE_PROMPT.format(
                    max_cards=self.budget.max_cards,
                    continue_framing=", ".join(facts.continue_framing) or "(none)",
                    verified=_render_verified(verified),
                ),
                ProposeOutput,
            ))
            cards = out.cards[: self.budget.max_cards]
        self._step(
            run_id,
            ScoutAction.PROPOSE,
            ScoutObservation(cards=list(cards)),
            decision=f"proposed {len(cards)} cards",
            outcome=f"cards_proposed={len(cards)}",
            llm_calls=1 if cards else 0,
        )

        # finish
        for card in cards:
            card = card.model_copy(update={"week": run.week})
            if not card.id:
                card = card.model_copy(update={"id": community_id_for(card.name)})
            self.repo.append_candidate(card)
        finished = run.model_copy(
            update={
                "status": "done",
                "cards_proposed": len(cards),
                "budget_used": inspected,
                "finished_at": datetime.now(UTC),
            }
        )
        self.repo.append_run(finished)
        self._step(
            run_id,
            ScoutAction.FINISH,
            ScoutObservation(),
            decision=f"saved {len(cards)} cards",
            outcome=f"status={finished.status}",
            llm_calls=0,
        )
        return finished

    def _step(self, run_id, action, observation, *, decision, outcome, llm_calls):
        self.repo.append_step(
            RunStep(
                run_id=run_id,
                action=action,
                observation=observation,
                decision=decision,
                outcome=outcome,
                llm_calls=llm_calls,
            )
        )
```

顶部 import 补：`import hashlib`、`from typing import Protocol, cast`、`from pydantic import BaseModel, Field`、
`from finch.communities.models import (CommunityCandidate, CommunityEvidence, CommunityProfile,
RecommendationState, EntryPoint, CommunityResult, CommunityFeedback, FeedbackFacts, RunIntent,
ScoutAction, ScoutObservation, RunStep, CommunityRun, community_id_for)`、
`from finch.communities.repository import CommunityRepository`、`from finch.communities.service import week_label`、
`from finch.llm.base import StructuredInferenceRunner`、`from finch.settings import CommunityScoutSettings`。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/unit/test_community_scout.py -k "loop_" -v`
Expected: PASS。若 `test_loop_reinspects_once_when_all_rejected` 因 `inspect_batch=1` 时 FakeSearchSource 只返回 3 个候选而 re-inspect 逻辑只多跑一批（3 个候选、每批 1 个 → 需 3 次），调整断言为「inspect 步数 > 1」或把候选改成 2 个（A 全淘汰 → 再 inspect B → 若 B 也全淘汰则 re-inspect 用尽仍停）。

- [ ] **Step 5: Commit**

```bash
git add src/finch/communities/scout.py tests/unit/test_community_scout.py
git commit -m "feat(community-scout): add bounded CommunityLoop with inspect/propose judgment"
```

---

### Task 7: 真实 search 源（WebFetcher 最小版）

**Files:**
- Modify: `src/finch/communities/scout.py`（新增 `WebFetcherSearchSource`）
- Test: `tests/unit/test_community_scout.py`（追加）

**Interfaces:**
- Consumes: `WebFetcher`（`finch.webfetch.fetcher.WebFetcher().fetch(url) -> str`）、`CommunityScoutSettings.search_urls`、`CommunitySearchSource`（Task 5）。
- Produces: `WebFetcherSearchSource(search_urls, fetcher=None).search(intent, goal, limit) -> list[CommunityCandidate]`。Task 8 CLI 用它做真实源。

- [ ] **Step 1: 写失败测试**

在 `tests/unit/test_community_scout.py` 追加：

```python
from finch.communities.scout import WebFetcherSearchSource


class _FakeFetcher:
    def fetch(self, url):
        return f"fetched: {url}"


def test_web_fetcher_source_yields_candidates():
    src = WebFetcherSearchSource(
        ["https://temporal.io/community", "https://example.org/forum"],
        fetcher=_FakeFetcher(),
    )
    out = src.search(RunIntent.WEEKLY, "找社区", limit=10)
    assert [c.canonical_url for c in out] == [
        "https://temporal.io/community",
        "https://example.org/forum",
    ]
    assert out[0].evidence_text == "fetched: https://temporal.io/community"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/test_community_scout.py -k web_fetcher -v`
Expected: FAIL — `WebFetcherSearchSource` 未定义。

- [ ] **Step 3: 实现**

在 `src/finch/communities/scout.py` 追加：

```python
class WebFetcherSearchSource:
    """最小真实 search 源：对配置的 search_urls 逐个 WebFetcher 抓取，产出候选（evidence_text 为抓取正文）。

    只做检索（raw fetch），判断交给 inspect 的 LLM。多源社区发现（twitter/reddit/v2ex/GitHub
    Discussions）延后：现有 sources 层投影到 peers 而非 communities。
    """

    def __init__(self, search_urls: list[str], fetcher: "WebFetcher | None" = None) -> None:
        self.search_urls = search_urls
        self._fetcher = fetcher

    def search(self, intent: RunIntent, goal: str, limit: int) -> list[CommunityCandidate]:
        from finch.webfetch.fetcher import WebFetcher

        fetcher = self._fetcher or WebFetcher()
        out: list[CommunityCandidate] = []
        for url in self.search_urls[:limit]:
            try:
                text = fetcher.fetch(url)
            except Exception:  # noqa: BLE001 — 单源失败不阻塞，如实留缺口
                continue
            out.append(
                CommunityCandidate(
                    name=url.rstrip("/").split("/")[-1] or url,
                    canonical_url=url,
                    source_note="webfetch",
                    evidence_text=text,
                )
            )
        return out
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/unit/test_community_scout.py -k web_fetcher -v`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add src/finch/communities/scout.py tests/unit/test_community_scout.py
git commit -m "feat(community-scout): add minimal WebFetcher-backed search source"
```

---

### Task 8: CLI（`run` / `runs` / `run-trace`）

**Files:**
- Modify: `src/finch/cli.py`（在 `community_list` 之后加 3 个命令）
- Test: `tests/unit/test_cli_community.py`（追加）

**Interfaces:**
- Consumes: `CommunityLoop`、`WebFetcherSearchSource`、`CommunityRepository`、`CodexRunner`、`create_runner`、`load_settings`、`Workspace`、`RunIntent`。
- Produces: CLI 命令 `community run --intent --goal [--json]`、`community runs [--json]`、`community run-trace <run_id> [--json]`。

- [ ] **Step 1: 写失败测试**

在 `tests/unit/test_cli_community.py` 追加（沿用该文件已有的 `CliRunner` + `_patch` 模式；若该文件用 `_patch_settings`，则复用）：

```python
def test_community_runs_empty(monkeypatch, tmp_path):
    settings = _paths_settings(tmp_path)  # 复用文件内既有 helper
    _patch_settings(monkeypatch, settings)
    r = CliRunner().invoke(app, ["community", "runs", "--json"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output) == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/test_cli_community.py -k community_runs -v`
Expected: FAIL — `community runs` 命令不存在（exit_code != 0 或 typer 报 unknown command）。

- [ ] **Step 3: 实现**

在 `src/finch/cli.py` 的 `community_list` 之后追加（`run` 命令用 `create_runner` 与 `CodexRunner`，与 `drafts_create` 一致；`run` 里真实源用 `WebFetcherSearchSource(settings.community_scout.search_urls)`）：

```python
@community_app.command("run")
def community_run(
    intent: str = typer.Option("weekly", "--intent", help="weekly|question|revisit"),
    goal: str = typer.Option("", "--goal", help="本次探索目标（问题模式/回访时必填）"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """跑一趟社区发现（有界 loop：search→inspect→propose→finish），写决策记录。"""
    from finch.codex.runner import CodexRunner
    from finch.communities.models import RunIntent
    from finch.communities.repository import CommunityRepository
    from finch.communities.scout import CommunityLoop, WebFetcherSearchSource

    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    try:
        run_intent = RunIntent(intent)
    except ValueError:
        typer.echo(f"invalid --intent: {intent} (weekly|question|revisit)")
        raise typer.Exit(code=1) from None
    runner = cast(CodexRunner, create_runner(settings.llm, "critique") or CodexRunner())
    repo = CommunityRepository(ws)
    loop = CommunityLoop(
        runner,
        WebFetcherSearchSource(settings.community_scout.search_urls),
        repo,
        budget=settings.community_scout,
    )
    run = loop.run(run_intent, goal)
    if as_json:
        typer.echo(json.dumps(run.model_dump(mode="json"), ensure_ascii=False, indent=2))
    else:
        typer.echo(f"{run.run_id}\t{run.status}\tcards={run.cards_proposed}\tcandidates={run.candidates_found}")


@community_app.command("runs")
def community_runs(as_json: bool = typer.Option(False, "--json", help="输出 JSON")) -> None:
    """列出历史 run（倒序）。"""
    from finch.communities.repository import CommunityRepository

    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    runs = list(reversed(CommunityRepository(ws).list_runs()))
    if as_json:
        typer.echo(json.dumps([r.model_dump(mode="json") for r in runs], ensure_ascii=False, indent=2))
        return
    for r in runs:
        typer.echo(f"{r.run_id}\t{r.intent.value}\t{r.status}\t{r.cards_proposed}")


@community_app.command("run-trace")
def community_run_trace(
    run_id: str = typer.Argument(..., help="run id"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """复盘一次 run 的 per-step 决策记录。"""
    from finch.communities.repository import CommunityRepository

    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    repo = CommunityRepository(ws)
    steps = repo.list_steps(run_id)
    if as_json:
        typer.echo(json.dumps([s.model_dump(mode="json") for s in steps], ensure_ascii=False, indent=2))
        return
    for s in steps:
        typer.echo(f"{s.action.value}\t{s.decision}\t{s.outcome}")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/unit/test_cli_community.py -k community_runs -v`
Expected: PASS（`runs` 空列表）。再跑既有 community CLI 测试确保无回归：
`uv run pytest tests/unit/test_cli_community.py -v`。

- [ ] **Step 5: Commit**

```bash
git add src/finch/cli.py tests/unit/test_cli_community.py
git commit -m "feat(community-scout): add run/runs/run-trace CLI"
```

---

### Task 9: 文档（SKILL.md + presentation.md 对齐）

**Files:**
- Modify: `skills/community-scout/SKILL.md`
- Modify: `skills/community-scout/references/presentation.md`

**Interfaces:**
- Consumes: 无（文档）。
- Produces: SKILL 里「搜索预算（试运行参数）」一节改为指向 `finch.yaml` 的 `community_scout`；「CLI」一节补 `run`/`runs`/`run-trace`；「边界」一节把「反馈→评分闭环留待验证后」改为指向 `derive_feedback_facts` 的硬门禁。

- [ ] **Step 1: 改 SKILL.md**

- 把 `## 搜索预算（试运行参数）` 标题下的第一段替换为：

```
搜索预算现在可配：`finch.yaml` 的 `community_scout`（max_candidates / inspect_batch / max_cards /
max_reinspect_rounds / suppress_window_weeks / search_urls）。周探索默认 20 → 6 → 3；
问题模式优先 1 + 备选 1。分源失败返回部分结果与缺口，不因一处超时伪造全网结论。
```

- 在 `## CLI` 列表末尾加：

```
- 跑一趟发现（有界 loop + 决策记录）：`finch community run --intent weekly|question|revisit [--goal …]`
- 列出历史 run：`finch community runs [--json]`
- 复盘一次 run 的 per-step 决策：`finch community run-trace <run_id> [--json]`
```

- 把 `## 边界` 里的 `- 跟进结果只记录，不自动改变下次评分（反馈→评分闭环留待验证后）。` 替换为：

```
- 跟进结果确定性回灌下一次推荐：`derive_feedback_facts` 只做硬门禁（ignored 在 suppress_window 内排除、
  no_time 永不过滤、已互动进入「继续」框架）与软排序摘要；不自动训练权重。
```

- [ ] **Step 2: 改 presentation.md**

在 `## 用户回复 → CLI 映射` 表之后加一段：

```
## run 产物

`finch community run` 落地决策记录（`runs.jsonl` + `steps.jsonl`）。复盘用 `finch community run-trace
<run_id>` 看每个动作（search/inspect/propose/finish）的 decision 与 outcome，区分「抓取范围太窄」
「判断标准有偏」「切入话题差」。
```

- [ ] **Step 3: 验证**

Run: `uv run pytest tests/unit/test_communities.py tests/unit/test_community_scout.py tests/unit/test_cli_community.py -v && uv run ruff check . && uv run mypy src`
Expected: 全 PASS / 无 lint / 类型通过（文档排除在 lint 外，但确认无破坏）。

- [ ] **Step 4: Commit**

```bash
git add skills/community-scout/SKILL.md skills/community-scout/references/presentation.md
git commit -m "docs(community-scout): align skill to loop command and feedback closure"
```

---

## Self-Review

**Spec coverage:** spec §3.1 组件 → Task 1/2/3/6；§3.2 动作机 → Task 6；§3.3 反馈闭环 → Task 4；§3.4 数据流 → Task 6；§4 数据模型 → Task 2；§5 错误处理/不变量 → Task 6（hard gate / gap_note / 预算）；§6 测试 → 各 Task 内；§7 search 真实能力 → Task 7（WebFetcher 最小版 + 缺口披露）；§8 文件范围 → 各 Task 的 Files 块。

**Placeholder scan:** 无 TBD/TODO；每个 code 步含完整代码；prompt 为真实文本。

**Type consistency:** `derive_feedback_facts(latest_by_identity, *, now, suppress_window) -> FeedbackFacts`（Task 4）在 Task 6 被 `CommunityLoop` 以 `derive_feedback_facts(self.repo.latest_feedback_by_identity(), now=clock, suppress_window=timedelta(weeks=self.budget.suppress_window_weeks))` 调用，签名一致。`CommunitySearchSource.search(intent, goal, limit)` 在 Task 5 定义、Task 6/7 实现/消费，签名一致。`append_run/list_runs/get_run/append_step/list_steps`（Task 3）在 Task 6/8 消费，名称一致。
