# Co-Exploration Fit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Inject `ActiveProblem` into opportunity assessment and render a unified three-question output so each recommendation answers "他解决什么 / 与你什么相关 / 你能贡献什么".

**Architecture:** `ActiveProblem` (already built) becomes the structured "what am I solving" input to `assess_opportunity`; `Fit.problem_refs` back-links the match; `render_active_problems` and `render_opportunity_questions` are deterministic, testable render helpers. No deterministic 50-person tiering change; no feedback loop.

**Tech Stack:** Python 3.12, Pydantic 2, Typer, pytest, ruff (E,F,I,B,UP, line-length 100), mypy.

## Global Constraints

- Python 3.12+; Pydantic 2 (`BaseModel`/`Field`); deterministic domain services, no LLM agent loop, LLM output never carries a total.
- Evidence first; `gh`/`opencli` stay read-only; nothing auto-publishes.
- Bilingual (Chinese/English) docstrings; match surrounding files.
- New fields default `[]`/`None` → existing YAML loads unchanged.
- `render_active_problems` mirrors `profile/render.py::render_user_practices` (returns `"(none)"` when empty; only renders `status == "open"`).
- Commands stay green: `uv run pytest`, `uv run ruff check .`, `uv run mypy src`.

---

### Task 1: `Fit.problem_refs` field

**Files:**
- Modify: `src/finch/opportunities/models.py:83-87` (`Fit`)
- Test: `tests/unit/test_opportunity_fit.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `Fit.problem_refs: list[str]` (default empty list).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_opportunity_fit.py`:

```python
"""Fit.problem_refs：活跃问题回链（默认空、旧数据可加载）。"""

from finch.opportunities.models import Fit


def test_fit_problem_refs_defaults_empty():
    fit = Fit(reason="r")
    assert fit.problem_refs == []
    assert fit.practice_refs == []


def test_fit_problem_refs_roundtrip():
    fit = Fit(reason="r", practice_refs=["agent-100-days"], problem_refs=["problem_abc"])
    assert fit.problem_refs == ["problem_abc"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_opportunity_fit.py -q`
Expected: `ValidationError` / `TypeError` — `Fit` has no `problem_refs`.

- [ ] **Step 3: Add the field**

Modify `src/finch/opportunities/models.py`, `class Fit` (line 83):

```python
class Fit(BaseModel):
    """为什么与用户有关：reason + 已确认实践引用 + 活跃问题引用。"""

    reason: str
    practice_refs: list[str] = Field(default_factory=list)
    problem_refs: list[str] = Field(default_factory=list)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_opportunity_fit.py -q`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/finch/opportunities/models.py tests/unit/test_opportunity_fit.py
git commit -m "feat(opportunities): add Fit.problem_refs for active-problem backlinks"
```

---

### Task 2: `render_active_problems` + `assess_opportunity` injection + prompt signal

**Files:**
- Create: `src/finch/problems/render.py`
- Modify: `src/finch/opportunities/assess.py` (`assess_opportunity` gains `active_problems`)
- Modify: `prompts/opportunity.md` (add active-problems block + problem-advance signal + `fit.problem_refs` instruction)
- Test: `tests/unit/test_problems_render.py`, extend `tests/unit/test_opportunity_assess.py`

**Interfaces:**
- Consumes: `ActiveProblem` (from `finch.problems.models`), `NONE_MARKER` (from `finch.profile.render`).
- Produces:
  - `render_active_problems(problems: list[ActiveProblem] | None) -> str`
  - `assess_opportunity(..., active_problems: str = "") -> OpportunityDraft`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_problems_render.py`:

```python
"""render_active_problems：只渲染 open 问题，空/None → (none)。"""

from finch.problems.models import ActiveProblem
from finch.problems.render import render_active_problems
from datetime import UTC, datetime


def _p(title, status="open"):
    return ActiveProblem(
        id=f"problem_{title[:4]}", title=title, status=status,
        created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
    )


def test_renders_open_only():
    out = render_active_problems([_p("重试何时值得"), _p("已关闭", status="closed")])
    assert "重试何时值得" in out
    assert "已关闭" not in out


def test_empty_returns_none_marker():
    assert render_active_problems([]) == "(none)"
    assert render_active_problems(None) == "(none)"
    assert render_active_problems([_p("x", status="closed")]) == "(none)"
```

Extend `tests/unit/test_opportunity_assess.py` (the existing `FakeRunner` captures `last_prompt`; add):

```python
def test_assess_opportunity_renders_active_problems_in_prompt():
    runner = FakeRunner(_draft())
    assess_opportunity(
        runner, **_kwargs(),
        active_problems="- [problem_abc] 重试何时值得",
    )
    p = runner.last_prompt or ""
    assert "## User active problems" in p
    assert "problem_abc" in p
    assert "重试何时值得" in p
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_problems_render.py -q`
Expected: `ModuleNotFoundError: No module named 'finch.problems.render'`.

- [ ] **Step 3: Write `render_active_problems`**

Create `src/finch/problems/render.py`:

```python
"""把活跃问题渲染成 prompt 文本块（镜像 ``profile/render.py::render_user_practices``）。"""

from finch.problems.models import ActiveProblem
from finch.profile.render import NONE_MARKER


def render_active_problems(problems: list[ActiveProblem] | None) -> str:
    """只渲染 ``status == "open"`` 的活跃问题；空/None → ``(none)``。"""
    if not problems:
        return NONE_MARKER
    open_problems = [p for p in problems if p.status == "open"]
    if not open_problems:
        return NONE_MARKER
    lines: list[str] = []
    for p in open_problems:
        head = f"- [{p.id}] {p.title}"
        if p.why_it_matters:
            head += f" — {p.why_it_matters}"
        lines.append(head)
    return "\n".join(lines)
```

- [ ] **Step 4: Add `active_problems` to `assess_opportunity`**

Modify `src/finch/opportunities/assess.py`. Add the parameter to the signature and the `.format(...)` call:

```python
def assess_opportunity(
    runner: StructuredInferenceRunner,
    *,
    peer_id: str,
    display_name: str,
    platform: str,
    current_work: str,
    why_relevant: str,
    their_artifacts_json: str,
    user_context: str = "",
    user_practices: str = "",
    active_problems: str = "",
) -> OpportunityDraft:
```

In the `_PROMPT.read_text().format(...)` call, add `active_problems=active_problems or "(none)"`.

- [ ] **Step 5: Update `prompts/opportunity.md`**

Add a block after `## User context (current questions / explorations)` and before `## User real practices`:

```markdown
## User active problems (≤3 open, the user is actively trying to solve)

Each line is an open research problem the user is currently working on. It is a stronger
fit signal than a generic topic match. `(none)` means no active problem is declared.

{active_problems}
```

In `## Signals to look for`, add a 4th signal:

```markdown
4. problem-advance — the content explains, challenges, or verifies one of the user's
   active problems above. This is a stronger fit than a generic topic overlap.
```

In field 15 (`fit`), add a `problem_refs` bullet after `practice_refs`:

```markdown
    - problem_refs: list of the active-problem ids (e.g. ["problem_xxx"]) that this
      opportunity advances, from the "User active problems" block. May be empty.
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_problems_render.py tests/unit/test_opportunity_assess.py -q`
Expected: all pass. If a prompt-contract test exists (checks `opportunity.md` placeholders vs `.format()` args), confirm it still passes with the new `{active_problems}` placeholder.

- [ ] **Step 7: Commit**

```bash
git add src/finch/problems/render.py src/finch/opportunities/assess.py prompts/opportunity.md \
        tests/unit/test_problems_render.py tests/unit/test_opportunity_assess.py
git commit -m "feat(opportunities): inject active problems into assessment with problem-advance signal"
```

---

### Task 3: Wire `active_problems` through discover / daily / from_url

**Files:**
- Modify: `src/finch/opportunities/discover.py` (`opportunity_context_fingerprint` + both discover functions)
- Modify: `src/finch/opportunities/from_url.py` (`assess_from_url` + fingerprint)
- Modify: `src/finch/discovery/daily.py` (load `ProblemRepository`, render, pass)
- Test: `tests/unit/test_opportunity_discover.py` (or wherever the fingerprint/assess-pass-through is tested)

**Interfaces:**
- Consumes: `render_active_problems` (Task 2), `ProblemRepository` (Task 1 of learning-loop, in `storage/repositories.py`).
- Produces: `discover_preferred_opportunity_outcome(..., active_problems="")`, `discover_preferred_opportunity(..., active_problems="")`, `assess_from_url(..., active_problems="")`.

- [ ] **Step 1: Write the failing test**

Extend the discover test to assert that `active_problems` changes the fingerprint (so a previously-skipped opportunity re-evaluates when active problems change), and that `assess_opportunity` receives `active_problems` (via a FakeRunner that captures the prompt).

```python
def test_fingerprint_varies_with_active_problems():
    from finch.opportunities.discover import opportunity_context_fingerprint
    a = opportunity_context_fingerprint(
        person_ref="p", current_work="w", why_relevant="r", artifacts=[],
        user_context="q", user_practices="", active_problems="problem_a",
    )
    b = opportunity_context_fingerprint(
        person_ref="p", current_work="w", why_relevant="r", artifacts=[],
        user_context="q", user_practices="", active_problems="problem_b",
    )
    assert a != b
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_opportunity_discover.py -q`
Expected: `TypeError` — `opportunity_context_fingerprint` has no `active_problems`.

- [ ] **Step 3: Thread `active_problems` through `discover.py`**

Modify `src/finch/opportunities/discover.py`:

- `opportunity_context_fingerprint(...)` gains `active_problems: str = ""`; append to `parts` when non-empty and != `NONE_MARKER` (mirror the existing `user_practices` block):

```python
    parts = [person_ref, current_work, why_relevant, user_context]
    if user_practices and user_practices != NONE_MARKER:
        parts.append(user_practices)
    if active_problems and active_problems != NONE_MARKER:
        parts.append(active_problems)
```

- `discover_preferred_opportunity_outcome(...)` gains `active_problems: str = ""`; pass it to `opportunity_context_fingerprint(...)` and to `assess_opportunity(...)` (add `active_problems=active_problems`).
- `discover_preferred_opportunity(...)` gains the same param and passes it through.

- [ ] **Step 4: Thread `active_problems` through `from_url.py`**

Modify `src/finch/opportunities/from_url.py`: `assess_from_url(...)` gains `active_problems: str = ""`; include it in the fingerprint (`f"{url}\n{user_context}{practice_part}{active_problems_part}\n{body[:2000]}"`); pass to `assess_opportunity(...)`.

- [ ] **Step 5: Wire `daily.py`**

Modify `src/finch/discovery/daily.py`:
- Add imports: `from finch.problems.render import render_active_problems` and `from finch.storage.repositories import ProblemRepository`.
- After `user_practices = render_user_practices(...)` (around line 272), add:

```python
    active_problems = render_active_problems(ProblemRepository(ws).list_all())
```

- In the `discover_preferred_opportunity_outcome(...)` call (around line 379), add `active_problems=active_problems`.

- [ ] **Step 6: Run tests + full suite + lint + typecheck**

Run:
```bash
uv run pytest tests/unit/test_opportunity_discover.py -q
uv run pytest -q
uv run ruff check .
uv run mypy src
```
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add src/finch/opportunities/discover.py src/finch/opportunities/from_url.py \
        src/finch/discovery/daily.py tests/unit/test_opportunity_discover.py
git commit -m "feat(opportunities): wire active problems through discovery and url assessment"
```

---

### Task 4: `render_opportunity_questions` + CLI presentation

**Files:**
- Create: `src/finch/opportunities/render.py`
- Modify: `src/finch/cli.py` (`_render_preferred_opportunity`, around line 2522)
- Test: `tests/unit/test_opportunity_render.py`

**Interfaces:**
- Consumes: `Opportunity` (with `problem`/`fit`/`proposal`/`topic`/`why_me`).
- Produces: `render_opportunity_questions(opp: Opportunity) -> str` (three-question block).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_opportunity_render.py`:

```python
"""render_opportunity_questions：三问映射 + 字段缺失回退。"""

from finch.opportunities.models import (
    ContributionForm,
    Fit,
    Opportunity,
    Problem,
    Proposal,
)
from finch.opportunities.render import render_opportunity_questions


def _opp(**kw) -> Opportunity:
    base = dict(
        id="opp_1",
        topic="工具超时误报成功",
        problem=Problem(statement="工具超时后误报成功", evidence_status="author_stated"),
        fit=Fit(reason="你在 Agent-100-Days 也踩过", practice_refs=["agent-100-days"], problem_refs=["problem_abc"]),
        proposal=Proposal(contribution="一张错误分类后重试的方法卡", form=ContributionForm.METHOD_CARD, expected_output="决策表"),
    )
    base.update(kw)
    return Opportunity(**base)


def test_three_questions_rendered():
    out = render_opportunity_questions(_opp())
    assert "他解决什么" in out and "工具超时后误报成功" in out
    assert "与你什么相关" in out and "agent-100-days" in out and "problem_abc" in out
    assert "你能贡献什么" in out and "方法卡" in out


def test_fallback_when_fields_missing():
    out = render_opportunity_questions(_opp(problem=None, fit=None, proposal=None))
    assert "他解决什么" in out and "工具超时误报成功" in out  # falls back to topic
    assert "与你什么相关" in out
    assert "你能贡献什么" in out and "待准备" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_opportunity_render.py -q`
Expected: `ModuleNotFoundError: No module named 'finch.opportunities.render'`.

- [ ] **Step 3: Write `render_opportunity_questions`**

Create `src/finch/opportunities/render.py`:

```python
"""机会三问统一渲染：他解决什么 / 与你什么相关 / 你能贡献什么（纯呈现层）。"""

from finch.opportunities.models import Opportunity


def render_opportunity_questions(opp: Opportunity) -> str:
    """把机会渲染成三问块；字段缺失时回退到旧自由文本，不丢信息。"""
    lines: list[str] = []

    if opp.problem is not None:
        status = "作者明说" if opp.problem.evidence_status == "author_stated" else "推测"
        lines.append(f"他解决什么：{opp.problem.statement}（{status}）")
    elif opp.topic:
        lines.append(f"他解决什么：{opp.topic}")
    else:
        lines.append("他解决什么：（未给出）")

    if opp.fit is not None:
        refs: list[str] = []
        if opp.fit.practice_refs:
            refs.append("实践 " + "、".join(opp.fit.practice_refs))
        if opp.fit.problem_refs:
            refs.append("问题 " + "、".join(opp.fit.problem_refs))
        suffix = f"（{'；'.join(refs)}）" if refs else ""
        lines.append(f"与你什么相关：{opp.fit.reason}{suffix}")
    elif opp.why_me:
        lines.append(f"与你什么相关：{opp.why_me}")
    else:
        lines.append("与你什么相关：（未给出）")

    if opp.proposal is not None and opp.proposal.contribution:
        p = opp.proposal
        tail = f"（{p.form.value}" + (f"，产出 {p.expected_output}" if p.expected_output else "") + "）"
        lines.append(f"你能贡献什么：{p.contribution}{tail}")
    else:
        lines.append("你能贡献什么：（待准备）")

    return "\n".join(lines)
```

- [ ] **Step 4: Wire into `_render_preferred_opportunity`**

Modify `src/finch/cli.py`. Import `render_opportunity_questions` (add to the existing `from .opportunities...` imports). In `_render_preferred_opportunity` (line 2522), after the `来源` line and before `话题`, insert the three-question block:

```python
    if source:
        lines.append(f"来源：{source}")
    lines.append(render_opportunity_questions(opp))
```

Update the `fit` rendering line to also show `problem_refs` (so the detailed view stays consistent):

```python
    if opp.fit is not None:
        prac = "、".join(opp.fit.practice_refs) if opp.fit.practice_refs else "无"
        prob = "、".join(opp.fit.problem_refs) if opp.fit.problem_refs else "无"
        lines.append(f"为什么与我有关：{opp.fit.reason}（实践 {prac}；问题 {prob}）")
```

(The three-question block adds a compact summary; the existing detail lines remain below it. If the implementer finds the duplication undesirable, keep the three-question block as the sole source of the three answers and drop the now-redundant `问题`/`为什么与我有关`/`最小贡献` detail lines — but do NOT drop `形式/可见结果/范围/成本`.)

- [ ] **Step 5: Run tests + full suite + lint + typecheck**

Run:
```bash
uv run pytest tests/unit/test_opportunity_render.py -q
uv run pytest -q
uv run ruff check .
uv run mypy src
```
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/finch/opportunities/render.py src/finch/cli.py tests/unit/test_opportunity_render.py
git commit -m "feat(opportunities): render unified three-question output"
```

---

## Self-Review Notes

- **Spec coverage:** §3 → Task 1; §4.1/§4.2 → Task 2; §4.3 → Task 3; §4.4/§4.5 → Task 4. §7 (feedback loop / tiering) out of scope.
- **Type consistency:** `Fit.problem_refs` (Task 1) consumed by `render_opportunity_questions` (Task 4) and the prompt instruction (Task 2). `render_active_problems` signature (Task 2) matches its call in `daily.py` (Task 3). `active_problems` param name identical across assess/discover/from_url/daily.
- **Backward compat:** `Fit.problem_refs` defaults `[]`; `active_problems` defaults `""`; fingerprint only appends when non-empty/non-`(none)`.
