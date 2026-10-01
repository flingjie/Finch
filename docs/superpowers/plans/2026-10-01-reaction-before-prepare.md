# Reaction Before Prepare Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user's own words about a preferred opportunity enter `finch connect prepare`, and forbid a full conclusion-shaped contribution when the user has said nothing.

**Architecture:** Add an append-only `reactions` list to the `Opportunity` aggregate (service method + `reaction_recorded` event). `prepare_contribution` gains a `reaction` argument, records it, and applies one deterministic gate: no reactions → form forced to `clarifying_question`; otherwise `proposal.form` is kept and the writer prompt receives the verbatim reaction. CLI exposes `connect prepare --reaction`. Skills change the closing line of the preferred opportunity into a concrete question and map the user's answer to `--reaction`.

**Tech Stack:** Python 3.12, Pydantic 2, typer, pytest; file workspace (YAML + JSONL via `Workspace.atomic_write`). No new dependencies.

Spec: `docs/superpowers/specs/2026-10-01-reaction-before-prepare-design.md`.

## Global Constraints

- Python 3.12+; ruff `line-length 100`, selects `E,F,I,B,UP`; `uv run mypy src` must stay clean.
- Domain services are deterministic and single-threaded; LLM output never carries a `total`; LLM is called only inside `write_contribution`.
- First-person experience in the contribution may come only from `confirmed: true` practice-profile items (`[practice-id]`) or from the user's verbatim reaction (`[reaction]`, restated only, never extrapolated).
- No new CLI command, no new Skill, no change to `prompts/opportunity.md` / `OpportunityDraft`, no reaction-kind classification.
- Reactions are not written to `InteractionRecord`, `ConversationThread`, `Inspiration`, `DialogueNote`, or `practice-profile.yaml`.
- Existing artifact id for the no-reaction case stays `art_{opp.id}_{form}` (no migration of `var/`).
- Run `uv run pytest`, `uv run ruff check .`, `uv run mypy src` before every commit; distinguish pre-existing failures from new ones.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `src/finch/opportunities/models.py` | Aggregate models | Add `Reaction`; add `Opportunity.reactions` |
| `src/finch/opportunities/service.py` | State machine + events | Add `record_reaction` |
| `src/finch/opportunities/prepare.py` | Writer step + artifact registration | `effective_form`, `artifact_id_for`, `reaction=` on `prepare_contribution`, `user_reaction`/`form` on `write_contribution`, `reaction`/`form_forced` on `PreparedContribution` |
| `prompts/prepare-contribution.md` | Writer prompt | New `User reaction` block + three rules |
| `src/finch/cli.py` | `connect prepare` | `--reaction` option, `_prepare_new_opportunity(reaction=)`, text/JSON output |
| `tests/unit/test_opportunities.py` | Service tests | `record_reaction` tests |
| `tests/unit/test_opportunity_prepare.py` | Prepare tests | Gate, prompt slot, artifact id |
| `tests/unit/test_prompt_placeholders.py` | Prompt contract | Add `user_reaction` |
| `tests/unit/test_cli_connect.py` | CLI tests | `--reaction` behaviour |
| `skills/peer-discovery/SKILL.md`, `references/presentation.md`, `evals/cases.yaml` | Presentation | Closing question + mapping + eval cases |
| `skills/interaction-preparation/SKILL.md`, `references/presentation.md` | Presentation | Show "来自你的那句"; `--reaction` in mapping |
| `AGENTS.md`, `CLAUDE.md`, `README.md` | Docs | Mention `--reaction` and the gate |

---

### Task 1: `Reaction` model and `Opportunity.reactions`

**Files:**
- Modify: `src/finch/opportunities/models.py:71-92` (class `Opportunity`)
- Test: `tests/unit/test_opportunities.py`

**Interfaces:**
- Produces: `class Reaction(BaseModel)` with `seq: int`, `text: str`, `created_at: datetime`; `Opportunity.reactions: list[Reaction]` (default empty).

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_opportunities.py`:

```python
# ---- Reaction（用户对这条机会亲口说的话；append-only）----


def test_opportunity_reactions_default_empty_and_round_trip(ws):
    from finch.opportunities.models import Opportunity, Reaction

    repo = OpportunityRepository(ws)
    repo.save(Opportunity(id="opp_r", topic="t"))
    loaded = repo.get("opp_r")
    assert loaded is not None
    assert loaded.reactions == []

    repo.save(
        Opportunity(
            id="opp_r2",
            topic="t",
            reactions=[Reaction(seq=1, text="我当时最难的是不知道任务到底跑没跑")],
        )
    )
    loaded2 = repo.get("opp_r2")
    assert loaded2 is not None
    assert loaded2.reactions[0].seq == 1
    assert loaded2.reactions[0].text == "我当时最难的是不知道任务到底跑没跑"
    assert loaded2.reactions[0].created_at is not None


def test_legacy_opportunity_yaml_without_reactions_loads(ws):
    """旧快照没有 reactions 字段 → 默认空，行为与今天一致。"""
    from finch.opportunities.models import Opportunity

    path = ws.dir("opportunities") / "opp_old" / "opportunity.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "id: opp_old\nrevision: 1\ntopic: t\nstatus: proposed\n"
        "evidence_refs: []\nopen_questions: []\nartifact_refs: []\n",
        encoding="utf-8",
    )
    loaded = OpportunityRepository(ws).get("opp_old")
    assert loaded is not None
    assert isinstance(loaded, Opportunity)
    assert loaded.reactions == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_opportunities.py -k reactions -v`
Expected: FAIL with `ImportError: cannot import name 'Reaction'`.

- [ ] **Step 3: Add the model and field**

In `src/finch/opportunities/models.py`, insert before `class Opportunity(BaseModel):`:

```python
class Reaction(BaseModel):
    """用户对这条机会亲口说的话（原话，append-only）。

    不是 InteractionRecord（没有发生互动）、不是 ConversationThread 笔记（没有对方）、
    不是 PracticeItem（未经 confirm）；只在本聚合内有效。
    """

    seq: int  # 从 1 起
    text: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
```

In `class Opportunity`, after `artifact_refs: list[str] = Field(default_factory=list)` add:

```python
    # 用户对这条机会的反应（原话；append-only）。无反应 → prepare 只能出澄清问题。
    reactions: list[Reaction] = Field(default_factory=list)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_opportunities.py -v`
Expected: all PASS (including the two new tests).

- [ ] **Step 5: Commit**

```bash
git add src/finch/opportunities/models.py tests/unit/test_opportunities.py
git commit -m "feat(opportunities): Reaction model and Opportunity.reactions (append-only)"
```

---

### Task 2: `OpportunityService.record_reaction`

**Files:**
- Modify: `src/finch/opportunities/service.py` (imports at top; new method after `update_artifact`, before `select`)
- Test: `tests/unit/test_opportunities.py`

**Interfaces:**
- Consumes: `Reaction`, `Opportunity.reactions` from Task 1.
- Produces: `OpportunityService.record_reaction(opportunity_id: str, *, text: str, expected_revision: int | None = None, request_id: str | None = None) -> Opportunity`. Raises `KeyError` (unknown id), `ValueError` (blank text / closed), `OpportunityConflictError` (revision mismatch). Event type `"reaction_recorded"`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_opportunities.py`:

```python
def test_record_reaction_appends_seq_and_event(ws, service):
    service.create(opportunity_id="opp_abc", topic="t", proposal=_proposal())
    opp = service.record_reaction("opp_abc", text="  我当时最难的是不知道任务跑没跑  ")
    assert [r.seq for r in opp.reactions] == [1]
    assert opp.reactions[0].text == "我当时最难的是不知道任务跑没跑"
    assert opp.revision == 2
    assert opp.status == OpportunityStatus.PROPOSED  # 不改状态

    opp = service.record_reaction("opp_abc", text="后来我们加了幂等键")
    assert [r.seq for r in opp.reactions] == [1, 2]
    assert opp.revision == 3

    events = OpportunityRepository(ws).list_events("opp_abc")
    assert [e.event_type for e in events] == [
        "proposed",
        "reaction_recorded",
        "reaction_recorded",
    ]
    assert events[1].expected_revision == 1
    assert events[2].expected_revision == 2


def test_record_reaction_rejects_blank_text(service):
    service.create(opportunity_id="opp_abc", topic="t")
    with pytest.raises(ValueError, match="blank"):
        service.record_reaction("opp_abc", text="   ")


def test_record_reaction_rejects_closed(service):
    service.create(opportunity_id="opp_abc", topic="t")
    service.close("opp_abc")
    with pytest.raises(ValueError, match="closed"):
        service.record_reaction("opp_abc", text="x")


def test_record_reaction_allowed_in_selected_ready_parked(service):
    service.create(opportunity_id="opp_abc", topic="t")
    service.select("opp_abc")
    assert service.record_reaction("opp_abc", text="a").reactions[-1].seq == 1
    service.mark_ready("opp_abc")
    assert service.record_reaction("opp_abc", text="b").reactions[-1].seq == 2
    service.select("opp_abc")
    service.park("opp_abc")
    assert service.record_reaction("opp_abc", text="c").reactions[-1].seq == 3


def test_record_reaction_unknown_opportunity_raises(service):
    with pytest.raises(KeyError):
        service.record_reaction("nope", text="x")


def test_record_reaction_same_text_is_content_idempotent(ws, service):
    """LLM 失败后重跑同一条命令不重复记录。"""
    service.create(opportunity_id="opp_abc", topic="t")
    first = service.record_reaction("opp_abc", text="同一句话")
    second = service.record_reaction("opp_abc", text=" 同一句话 ")
    assert second == first
    assert len(second.reactions) == 1
    events = OpportunityRepository(ws).list_events("opp_abc")
    assert [e.event_type for e in events].count("reaction_recorded") == 1


def test_record_reaction_expected_revision_conflict(service):
    service.create(opportunity_id="opp_abc", topic="t")
    with pytest.raises(OpportunityConflictError):
        service.record_reaction("opp_abc", text="x", expected_revision=999)
    opp = service.record_reaction("opp_abc", text="x", expected_revision=1)
    assert opp.revision == 2


def test_record_reaction_request_id_is_idempotent(ws, service):
    service.create(opportunity_id="opp_abc", topic="t")
    first = service.record_reaction("opp_abc", text="x", request_id="req_r1")
    second = service.record_reaction("opp_abc", text="y", request_id="req_r1")
    assert second == first
    assert len(second.reactions) == 1
    events = OpportunityRepository(ws).list_events("opp_abc")
    assert [e.request_id for e in events] == [None, "req_r1"]


def test_record_reaction_replays_stale_snapshot_after_crash(ws, service):
    """事件已落盘、快照未推进 → 同 request_id 重放修复快照，不重复事件。"""
    service.create(opportunity_id="opp_abc", topic="t")
    service.repo.append_event(
        OpportunityEvent(
            event_id="opp_abc:r2",
            opportunity_id="opp_abc",
            event_type="reaction_recorded",
            expected_revision=1,
            request_id="req_r1",
        )
    )
    opp = service.record_reaction("opp_abc", text="x", request_id="req_r1")
    assert opp.revision == 2
    assert [r.text for r in opp.reactions] == ["x"]
    events = OpportunityRepository(ws).list_events("opp_abc")
    assert [e.event_type for e in events].count("reaction_recorded") == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_opportunities.py -k record_reaction -v`
Expected: FAIL with `AttributeError: 'OpportunityService' object has no attribute 'record_reaction'`.

- [ ] **Step 3: Implement `record_reaction`**

In `src/finch/opportunities/service.py`, add `Reaction` to the models import:

```python
from finch.opportunities.models import (
    Artifact,
    EntryKind,
    EvidenceRef,
    ExecutionStatus,
    MaterialOrigin,
    Opportunity,
    OpportunityEvent,
    OpportunityStatus,
    Proposal,
    Reaction,
)
```

Insert the method after `update_artifact` (before `def select`):

```python
    def record_reaction(
        self,
        opportunity_id: str,
        *,
        text: str,
        expected_revision: int | None = None,
        request_id: str | None = None,
    ) -> Opportunity:
        """记录用户对这条机会亲口说的话（append-only；不改状态）。

        - 空白文本拒绝；closed 拒绝（其余四态允许）。
        - 内容幂等：与最新一条 text 相同 → 不追加、不写事件（LLM 失败后重跑不重复记录）。
        - ``request_id`` 幂等与崩溃重放逻辑与 ``_transition`` 相同。
        """
        cleaned = text.strip()
        if not cleaned:
            raise ValueError("reaction text must not be blank")
        with self.repo.locked(opportunity_id):
            opp = self.repo.get(opportunity_id)
            if opp is None:
                raise KeyError(opportunity_id)
            if request_id is not None:
                applied = self._applied_event(opportunity_id, request_id)
                if applied is not None and opp.revision > applied.expected_revision:
                    return opp
            if opp.status == OpportunityStatus.CLOSED:
                raise ValueError(
                    f"cannot record reaction on closed opportunity {opportunity_id}"
                )
            if expected_revision is not None and expected_revision != opp.revision:
                raise OpportunityConflictError(
                    f"revision conflict: expected {expected_revision}, "
                    f"current {opp.revision}"
                )
            if opp.reactions and opp.reactions[-1].text == cleaned:
                return opp
            reaction = Reaction(seq=len(opp.reactions) + 1, text=cleaned)
            new_opp = opp.model_copy(
                update={
                    "reactions": [*opp.reactions, reaction],
                    "revision": opp.revision + 1,
                    "updated_at": datetime.now(UTC),
                }
            )
            self.repo.append_event(
                OpportunityEvent(
                    event_id=f"{opportunity_id}:r{new_opp.revision}",
                    opportunity_id=opportunity_id,
                    event_type="reaction_recorded",
                    expected_revision=opp.revision,
                    request_id=request_id,
                )
            )
            self.repo.save(new_opp)
            return new_opp
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_opportunities.py -v`
Expected: all PASS.

- [ ] **Step 5: Lint, type-check, commit**

Run: `uv run ruff check src/finch/opportunities tests/unit/test_opportunities.py && uv run mypy src`
Expected: no errors.

```bash
git add src/finch/opportunities/service.py tests/unit/test_opportunities.py
git commit -m "feat(opportunities): record_reaction (append-only, content-idempotent, no status change)"
```

---

### Task 3: Prepare gate, prompt slot, artifact id

**Files:**
- Modify: `src/finch/opportunities/prepare.py` (dataclass `PreparedContribution`, `write_contribution`, `prepare_contribution`; new helpers `effective_form`, `artifact_id_for`)
- Modify: `prompts/prepare-contribution.md`
- Modify: `tests/unit/test_prompt_placeholders.py:17-31`
- Test: `tests/unit/test_opportunity_prepare.py`

**Interfaces:**
- Consumes: `OpportunityService.record_reaction` (Task 2); `Reaction`, `Opportunity.reactions` (Task 1).
- Produces:
  - `effective_form(opportunity: Opportunity) -> ContributionForm`
  - `artifact_id_for(opportunity: Opportunity, form: ContributionForm) -> str`
  - `write_contribution(runner, opportunity, *, voice_profile=None, confirmed_jobs=None, practice_profile=None, user_reaction: str | None = None, form: ContributionForm | None = None) -> str`
  - `prepare_contribution(*, opportunity, runner, service, voice_profile=None, confirmed_jobs=None, practice_profile=None, reaction: str | None = None) -> PreparedContribution`
  - `PreparedContribution.reaction: Reaction | None` and `PreparedContribution.form_forced: bool` (new dataclass fields with defaults `None` / `False`).

- [ ] **Step 1: Write the failing tests**

In `tests/unit/test_opportunity_prepare.py`, extend the two top-of-file import blocks: add
`Reaction,` to the `from finch.opportunities.models import (...)` block and add
`artifact_id_for,` and `effective_form,` to the `from finch.opportunities.prepare import (...)`
block (keep alphabetical order for ruff `I`). Then append:

```python
# ---- 反应门禁（spec 2026-10-01-reaction-before-prepare §4.2 / §6）----


def test_effective_form_forces_clarifying_question_without_reactions():
    assert effective_form(_opportunity()) == ContributionForm.CLARIFYING_QUESTION


def test_effective_form_keeps_proposal_form_with_reactions():
    opp = _opportunity().model_copy(update={"reactions": [Reaction(seq=1, text="我试过")]})
    assert effective_form(opp) == ContributionForm.METHOD_CARD


def test_effective_form_without_proposal_defaults_then_gates():
    opp = _opportunity().model_copy(update={"proposal": None})
    assert effective_form(opp) == ContributionForm.CLARIFYING_QUESTION
    opp2 = opp.model_copy(update={"reactions": [Reaction(seq=1, text="x")]})
    assert effective_form(opp2) == ContributionForm.METHOD_CARD


def test_artifact_id_for_keeps_legacy_id_without_reactions():
    assert artifact_id_for(_opportunity(), ContributionForm.METHOD_CARD) == (
        "art_opp_1_method_card"
    )


def test_artifact_id_for_suffixes_latest_reaction_seq():
    opp = _opportunity().model_copy(
        update={"reactions": [Reaction(seq=1, text="a"), Reaction(seq=2, text="b")]}
    )
    assert artifact_id_for(opp, ContributionForm.METHOD_CARD) == "art_opp_1_method_card_r2"


def test_write_contribution_renders_reaction_slot_none_by_default():
    runner = FakeRunner("x")
    write_contribution(runner, _opportunity())
    p = runner.last_prompt or ""
    section = p.split("## User reaction to THIS opportunity")[1]
    assert "\n\n(none)\n\n## Task" in section


def test_write_contribution_renders_verbatim_reaction_and_form_override():
    runner = FakeRunner("x")
    write_contribution(
        runner,
        _opportunity(),
        user_reaction="我当时最难的是不知道任务到底跑没跑",
        form=ContributionForm.CLARIFYING_QUESTION,
    )
    p = runner.last_prompt or ""
    assert "我当时最难的是不知道任务到底跑没跑" in p
    assert "- form: clarifying_question" in p
    assert "- form: method_card" not in p


def test_write_contribution_prompt_unchanged_except_reaction_block():
    """无反应时，除新增块外 prompt 文本与改动前一致（回归保护）。"""
    runner = FakeRunner("x")
    write_contribution(runner, _opportunity())
    p = runner.last_prompt or ""
    assert "## User real practices (confirmed; the ONLY source for first-person" in p
    assert "## User reaction to THIS opportunity (verbatim" in p
    assert "[reaction]" in p  # 规则文本已加入


def test_prepare_without_reaction_forces_clarifying_question(tmp_path):
    service, repo, art_repo = _service(tmp_path)
    service.create(
        opportunity_id="opp_1",
        topic="t",
        proposal=Proposal(
            contribution="c",
            form=ContributionForm.METHOD_CARD,
            expected_output="o",
            scope="s",
        ),
    )
    service.select("opp_1")
    runner = FakeRunner("观察 + 一个问题")
    prepared = prepare_contribution(
        opportunity=service.get("opp_1"), runner=runner, service=service
    )
    assert prepared.form_forced is True
    assert prepared.reaction is None
    assert prepared.artifacts[0].kind == ArtifactKind.REPLY_DRAFT
    assert prepared.artifacts[0].id == "art_opp_1_clarifying_question"
    assert "- form: clarifying_question" in (runner.last_prompt or "")
    assert [e.event_type for e in repo.list_events("opp_1")] == [
        "proposed",
        "selected",
        "artifact_added",
        "ready",
    ]


def test_prepare_with_reaction_records_then_keeps_proposal_form(tmp_path):
    service, repo, art_repo = _service(tmp_path)
    service.create(
        opportunity_id="opp_1",
        topic="t",
        proposal=Proposal(
            contribution="c",
            form=ContributionForm.METHOD_CARD,
            expected_output="o",
            scope="s",
        ),
    )
    service.select("opp_1")
    runner = FakeRunner("方法卡正文 [reaction]")
    prepared = prepare_contribution(
        opportunity=service.get("opp_1"),
        runner=runner,
        service=service,
        reaction="我当时最难的是不知道任务到底跑没跑",
    )
    assert prepared.form_forced is False
    assert prepared.reaction is not None
    assert prepared.reaction.seq == 1
    assert prepared.reaction.text == "我当时最难的是不知道任务到底跑没跑"
    assert prepared.artifacts[0].kind == ArtifactKind.METHOD_CARD
    assert prepared.artifacts[0].id == "art_opp_1_method_card_r1"
    assert "我当时最难的是不知道任务到底跑没跑" in (runner.last_prompt or "")
    assert "- form: method_card" in (runner.last_prompt or "")
    assert [e.event_type for e in repo.list_events("opp_1")] == [
        "proposed",
        "selected",
        "reaction_recorded",
        "artifact_added",
        "ready",
    ]
    assert art_repo.read_content("opp_1", "art_opp_1_method_card_r1") == "方法卡正文 [reaction]"


def test_prepare_uses_latest_stored_reaction_when_none_passed(tmp_path):
    service, _repo, _art_repo = _service(tmp_path)
    service.create(
        opportunity_id="opp_1",
        topic="t",
        proposal=Proposal(
            contribution="c",
            form=ContributionForm.CASE,
            expected_output="o",
            scope="s",
        ),
    )
    service.select("opp_1")
    service.record_reaction("opp_1", text="早先说过的一句")
    runner = FakeRunner("x")
    prepared = prepare_contribution(
        opportunity=service.get("opp_1"), runner=runner, service=service
    )
    assert prepared.form_forced is False
    assert prepared.reaction is not None and prepared.reaction.text == "早先说过的一句"
    assert prepared.artifacts[0].id == "art_opp_1_case_r1"
    assert "早先说过的一句" in (runner.last_prompt or "")


def test_prepare_blank_reaction_rejected_before_llm(tmp_path):
    service, _repo, _art_repo = _service(tmp_path)
    service.create(opportunity_id="opp_1", topic="t", proposal=None)
    service.select("opp_1")
    runner = FakeRunner("x")
    with pytest.raises(ValueError, match="blank"):
        prepare_contribution(
            opportunity=service.get("opp_1"), runner=runner, service=service, reaction="  "
        )
    assert runner.calls == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_opportunity_prepare.py -v`
Expected: FAIL with `ImportError: cannot import name 'artifact_id_for'`.

- [ ] **Step 3: Update the prompt**

In `prompts/prepare-contribution.md`, after the `## User real practices ...` block (after the `{user_practices}` line and its blank line), insert:

```markdown
## User reaction to THIS opportunity (verbatim; the user's own words in this session)

{user_reaction}

```

Then, inside the `First-person experience rules:` list, append three bullets after the existing `- When that section is \`(none)\`...` bullet:

```markdown
- The "User reaction" block is what the user actually said about this opportunity. You may
  write it in the first person, but ONLY restate it — never extend it with numbers, outcomes,
  or scenarios the user did not say. Tag every such sentence with `[reaction]`. When a reaction
  and a confirmed practice are both relevant, cite both (`[reaction]` and `[practice-id]`).
- If the reaction is a question, make it the contribution's own continuable question — do not
  answer it on the user's behalf. If the reaction is a guess, write it as "我猜测 / 一个假设是"
  plus how to verify it — never as a conclusion.
- When the reaction block is `(none)`, the code has already set form to `clarifying_question`:
  write one concrete observation plus one honest question only; ignore any method-card-shaped
  `expected_output` / `scope`. First person is then allowed only from confirmed practices.
```

Update `tests/unit/test_prompt_placeholders.py` — in the `"prompts/prepare-contribution.md"` set add `"user_reaction",` after `"user_practices",`.

- [ ] **Step 4: Implement helpers, `write_contribution`, `prepare_contribution`**

In `src/finch/opportunities/prepare.py`:

Add `Reaction` to the models import block:

```python
from finch.opportunities.models import (
    Artifact,
    ArtifactKind,
    ContributionForm,
    EvidenceRef,
    ExecutionStatus,
    MaterialOrigin,
    Opportunity,
    OpportunityStatus,
    Reaction,
)
```

Replace the `PreparedContribution` dataclass with:

```python
@dataclass
class PreparedContribution:
    """``prepare_contribution`` 的结果：机会（ready）+ 可直接审阅的成果正文。

    ``reaction`` 是正文所依据的最新一条用户反应（无则 None）；``form_forced`` 为 True 表示
    因无反应被代码强制为 clarifying_question。
    """

    opportunity: Opportunity
    artifacts: list[PreparedArtifact]
    reaction: Reaction | None = None
    form_forced: bool = False
```

Add two helpers after `artifact_kind_for`:

```python
def effective_form(opportunity: Opportunity) -> ContributionForm:
    """本设计唯一的 Python 门禁：没有任何用户反应 → 只能准备澄清问题。

    有反应 → 沿用 assess 给出的 ``proposal.form``，如何用进原话由写作 prompt 决定。
    """
    p = opportunity.proposal
    base = p.form if p else ContributionForm.METHOD_CARD
    return base if opportunity.reactions else ContributionForm.CLARIFYING_QUESTION


def artifact_id_for(opportunity: Opportunity, form: ContributionForm) -> str:
    """成果 id：无反应沿用 ``art_{opp}_{form}``（不动现有数据）；有反应加 ``_r{seq}``。"""
    base = f"art_{opportunity.id}_{form.value}"
    if opportunity.reactions:
        return f"{base}_r{opportunity.reactions[-1].seq}"
    return base
```

Change `write_contribution` signature and body:

```python
def write_contribution(
    runner: StructuredInferenceRunner,
    opportunity: Opportunity,
    *,
    voice_profile: VoiceProfile | None = None,
    confirmed_jobs: list[ContentJob] | None = None,
    practice_profile: PracticeProfile | None = None,
    user_reaction: str | None = None,
    form: ContributionForm | None = None,
) -> str:
    """按机会的 proposal 生成贡献正文（纯正文，不落库、不改状态）。

    ``practice_profile`` 只渲染 confirmed 条目；None / 空 → ``(none)``，正文维持假设场景写法。
    ``user_reaction`` 是用户对这条机会的原话（None → ``(none)``）；``form`` 覆盖 proposal 的
    形式（由 ``effective_form`` 决定），None 时沿用 proposal。
    """
    p = opportunity.proposal
    resolved_form = form if form is not None else (p.form if p else ContributionForm.METHOD_CARD)
    prompt = _PROMPT.read_text().format(
        topic=opportunity.topic or "(none)",
        entry_kind=opportunity.entry_kind.value if opportunity.entry_kind else "none",
        why_me=opportunity.why_me or "(none)",
        why_continue=opportunity.why_continue or "(none)",
        contribution=p.contribution if p else "(none)",
        form=resolved_form.value,
        expected_output=p.expected_output if p else "(none)",
        scope=p.scope if p else "(none)",
        cost_note=(p.cost_note if p and p.cost_note else "(none)"),
        evidence=render_evidence_refs(opportunity.evidence_refs),
        voice_summary=render_voice_summary(voice_profile),
        user_positions=render_user_positions(confirmed_jobs or []),
        user_practices=render_user_practices(practice_profile),
        user_reaction=(user_reaction.strip() if user_reaction and user_reaction.strip() else "(none)"),
    )
    out = cast(ContributionBodyOutput, runner.run(prompt, ContributionBodyOutput))
    return out.body
```

Replace `prepare_contribution` with:

```python
def prepare_contribution(
    *,
    opportunity: Opportunity,
    runner: StructuredInferenceRunner,
    service: OpportunityService,
    voice_profile: VoiceProfile | None = None,
    confirmed_jobs: list[ContentJob] | None = None,
    practice_profile: PracticeProfile | None = None,
    reaction: str | None = None,
) -> PreparedContribution:
    """选定机会后按需制作：记录反应 → 决定形式 → 生成正文 → 写文件 → 登记 Artifact → mark_ready。

    ``reaction`` 非 None 时先经 ``record_reaction`` 落库（空白拒绝、同文本幂等）。没有任何
    反应 → 形式强制 clarifying_question（唯一的 Python 门禁）。正文是可审阅表达方案，默认
    material_origin=SYNTHETIC、execution_status=NOT_RUN；代码不得把未运行标为已运行。
    """
    if service.artifacts is None:
        raise ValueError("prepare_contribution requires an ArtifactRepository")
    with service.locked(opportunity.id):
        opp = service.get(opportunity.id)
        if opp is None:
            raise KeyError(opportunity.id)
        # 未选中直接拒绝，避免先调模型/写文件后才因状态转换非法报错。
        if opp.status != OpportunityStatus.SELECTED:
            raise ValueError(
                f"illegal state to prepare: {opp.status.value} "
                f"(expected selected) for opportunity {opportunity.id}"
            )
        if reaction is not None:
            opp = service.record_reaction(opp.id, text=reaction)
        form = effective_form(opp)
        latest = opp.reactions[-1] if opp.reactions else None
        body = write_contribution(
            runner,
            opp,
            voice_profile=voice_profile,
            confirmed_jobs=confirmed_jobs,
            practice_profile=practice_profile,
            user_reaction=latest.text if latest else None,
            form=form,
        )
        artifact_id = artifact_id_for(opp, form)
        source_refs = [
            ref.source_ref for ref in opp.evidence_refs if ref.source_ref
        ]
        service.artifacts.write_content(opp.id, artifact_id, body)
        artifact = Artifact(
            id=artifact_id,
            kind=artifact_kind_for(form),
            path=f"artifacts/{artifact_id}.md",
            source_refs=source_refs,
            material_origin=MaterialOrigin.SYNTHETIC,
            execution_status=ExecutionStatus.NOT_RUN,
        )
        service.add_artifact(opp.id, artifact)
        ready = service.mark_ready(opp.id)
        prepared_artifact = PreparedArtifact(
            id=artifact.id,
            kind=artifact.kind,
            body=body,
            source_refs=source_refs,
            execution_status=artifact.execution_status,
        )
        return PreparedContribution(
            opportunity=ready,
            artifacts=[prepared_artifact],
            reaction=latest,
            form_forced=latest is None,
        )
```

- [ ] **Step 5: Fix the existing test that assumed the old artifact id**

`test_prepare_contribution_marks_ready_and_records_artifact` in `tests/unit/test_opportunity_prepare.py` prepares without a reaction, so the form is now `clarifying_question` and the artifact id is `art_opp_1_clarifying_question`. Update that test's three assertions:

```python
    assert "art_opp_1_clarifying_question" in opp.artifact_refs
    assert art_repo.read_content("opp_1", "art_opp_1_clarifying_question") == body
    ...
    saved = art_repo.get("opp_1", "art_opp_1_clarifying_question")
```

(Everything else in that test stays — event order is unchanged because no reaction was passed.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_opportunity_prepare.py tests/unit/test_prompt_placeholders.py -v`
Expected: all PASS.

- [ ] **Step 7: Lint, type-check, full suite, commit**

Run: `uv run ruff check . && uv run mypy src && uv run pytest -q`
Expected: clean; full suite green (a CLI test may fail if it asserted the old `art_opp_1_method_card` id through the real `prepare_contribution` — none does today; all CLI tests monkeypatch `prepare_contribution`).

```bash
git add src/finch/opportunities/prepare.py prompts/prepare-contribution.md \
  tests/unit/test_opportunity_prepare.py tests/unit/test_prompt_placeholders.py
git commit -m "feat(opportunities): reaction gate in prepare — no reaction → clarifying question only"
```

---

### Task 4: CLI `connect prepare --reaction`

**Files:**
- Modify: `src/finch/cli.py:2187-2215` (`_prepare_new_opportunity`), `:2240-2265` (`_render_prepared_contribution`, `_prepared_payload`), `:2727-2782` (`connect_prepare`)
- Test: `tests/unit/test_cli_connect.py`

**Interfaces:**
- Consumes: `prepare_contribution(..., reaction=)`, `PreparedContribution.reaction` / `.form_forced` (Task 3); `OpportunityService.select` (existing, `ready → selected` is legal).
- Produces: `_prepare_new_opportunity(settings, ws, opportunity_id, *, reaction: str | None = None)`; CLI option `--reaction`; JSON keys `reaction` and `form_forced` on each `opportunities[]` item.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_cli_connect.py` (after `test_connect_prepare_requires_selection`):

```python
def test_connect_prepare_passes_reaction_through(monkeypatch, tmp_path):
    from finch.opportunities.models import OpportunityStatus, Reaction
    from finch.opportunities.prepare import PreparedContribution

    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    _seed_new_opportunity(ws, "opp_person_1")
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    seen: dict = {}

    def _fake_prepare(*, opportunity, runner, service, **kw):
        seen["reaction"] = kw.get("reaction")
        return PreparedContribution(
            opportunity=opportunity.model_copy(update={"status": OpportunityStatus.READY}),
            artifacts=[],
            reaction=Reaction(seq=1, text=kw.get("reaction") or ""),
            form_forced=False,
        )

    monkeypatch.setattr(cli, "prepare_contribution", _fake_prepare)
    r = CliRunner().invoke(
        app,
        [
            "connect", "prepare", "--opportunity", "opp_person_1",
            "--reaction", "我当时最难的是不知道任务到底跑没跑",
        ],
    )
    assert r.exit_code == 0, r.output
    assert seen["reaction"] == "我当时最难的是不知道任务到底跑没跑"
    assert "你的反应（第 1 条）：我当时最难的是不知道任务到底跑没跑" in r.output


def test_connect_prepare_without_reaction_says_clarifying_only(monkeypatch, tmp_path):
    from finch.opportunities.models import OpportunityStatus
    from finch.opportunities.prepare import PreparedContribution

    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    _seed_new_opportunity(ws, "opp_person_1")
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    def _fake_prepare(*, opportunity, runner, service, **kw):
        assert kw.get("reaction") is None
        return PreparedContribution(
            opportunity=opportunity.model_copy(update={"status": OpportunityStatus.READY}),
            artifacts=[],
            reaction=None,
            form_forced=True,
        )

    monkeypatch.setattr(cli, "prepare_contribution", _fake_prepare)
    r = CliRunner().invoke(app, ["connect", "prepare", "--opportunity", "opp_person_1"])
    assert r.exit_code == 0, r.output
    assert "无反应：本次只准备澄清问题" in r.output


def test_connect_prepare_json_includes_reaction_and_form_forced(monkeypatch, tmp_path):
    from finch.opportunities.models import OpportunityStatus, Reaction
    from finch.opportunities.prepare import PreparedContribution

    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    _seed_new_opportunity(ws, "opp_person_1")
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    def _fake_prepare(*, opportunity, runner, service, **kw):
        return PreparedContribution(
            opportunity=opportunity.model_copy(update={"status": OpportunityStatus.READY}),
            artifacts=[],
            reaction=Reaction(seq=2, text="第二句"),
            form_forced=False,
        )

    monkeypatch.setattr(cli, "prepare_contribution", _fake_prepare)
    r = CliRunner().invoke(
        app, ["connect", "prepare", "--opportunity", "opp_person_1", "--reaction", "第二句", "--json"]
    )
    assert r.exit_code == 0, r.output
    opp = json.loads(r.output)["opportunities"][0]
    assert opp["reaction"] == {"seq": 2, "text": "第二句"}
    assert opp["form_forced"] is False


def test_connect_prepare_rejects_blank_reaction(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    _seed_new_opportunity(ws, "opp_person_1")
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    called = {"n": 0}

    def _fake_prepare(**_kw):
        called["n"] += 1

    monkeypatch.setattr(cli, "prepare_contribution", _fake_prepare)
    r = CliRunner().invoke(
        app, ["connect", "prepare", "--opportunity", "opp_person_1", "--reaction", "   "]
    )
    assert r.exit_code == 1
    assert "reaction must not be blank" in r.output
    assert called["n"] == 0


def test_connect_prepare_rejects_reaction_with_multiple_opportunities(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    _seed_new_opportunity(ws, "opp_a")
    _seed_new_opportunity(ws, "opp_b")
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    r = CliRunner().invoke(
        app,
        ["connect", "prepare", "--opportunity", "opp_a", "--opportunity", "opp_b",
         "--reaction", "x"],
    )
    assert r.exit_code == 1
    assert "--reaction applies to exactly one --opportunity" in r.output


def _seed_ready_opportunity(ws, opportunity_id: str = "opp_ready"):
    from finch.opportunities.models import Artifact, ArtifactKind, OpportunityStatus
    from finch.opportunities.repository import ArtifactRepository as NewArtRepo
    from finch.opportunities.repository import OpportunityRepository as NewOppRepo

    _seed_new_opportunity(ws, opportunity_id)
    repo = NewOppRepo(ws)
    opp = repo.get(opportunity_id)
    assert opp is not None
    art_id = f"art_{opportunity_id}_clarifying_question"
    NewArtRepo(ws).write_content(opportunity_id, art_id, "旧的澄清问题")
    NewArtRepo(ws).save(
        Artifact(id=art_id, opportunity_id=opportunity_id, kind=ArtifactKind.REPLY_DRAFT)
    )
    repo.save(
        opp.model_copy(
            update={"status": OpportunityStatus.READY, "artifact_refs": [art_id], "revision": 3}
        )
    )


def test_connect_prepare_ready_without_reaction_returns_existing(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    _seed_ready_opportunity(ws, "opp_ready")
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    called = {"n": 0}

    def _fake_prepare(**_kw):
        called["n"] += 1

    monkeypatch.setattr(cli, "prepare_contribution", _fake_prepare)
    r = CliRunner().invoke(app, ["connect", "prepare", "--opportunity", "opp_ready"])
    assert r.exit_code == 0, r.output
    assert called["n"] == 0
    assert "旧的澄清问题" in r.output
    assert "无反应：本次只准备澄清问题" in r.output


def test_connect_prepare_ready_with_reaction_reselects_and_regenerates(monkeypatch, tmp_path):
    from finch.opportunities.models import OpportunityStatus, Reaction
    from finch.opportunities.prepare import PreparedContribution

    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    _seed_ready_opportunity(ws, "opp_ready")
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    seen: dict = {}

    def _fake_prepare(*, opportunity, runner, service, **kw):
        seen["status"] = opportunity.status
        seen["reaction"] = kw.get("reaction")
        return PreparedContribution(
            opportunity=opportunity.model_copy(update={"status": OpportunityStatus.READY}),
            artifacts=[],
            reaction=Reaction(seq=1, text=kw["reaction"]),
            form_forced=False,
        )

    monkeypatch.setattr(cli, "prepare_contribution", _fake_prepare)
    r = CliRunner().invoke(
        app, ["connect", "prepare", "--opportunity", "opp_ready", "--reaction", "新反应"]
    )
    assert r.exit_code == 0, r.output
    assert seen["status"] == OpportunityStatus.SELECTED  # ready → selected（调整贡献）
    assert seen["reaction"] == "新反应"


def test_connect_daily_json_preferred_opportunity_carries_reactions(monkeypatch, tmp_path):
    """首选机会 JSON 透出 reactions（兼容新增字段）。"""
    from finch.engagement.models import DiscoverySnapshot
    from finch.opportunities.models import Reaction
    from finch.opportunities.repository import OpportunityRepository as NewOppRepo
    from finch.storage.repositories import DiscoverySnapshotRepository

    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    _seed_new_opportunity(ws, "opp_person_1")
    repo = NewOppRepo(ws)
    opp = repo.get("opp_person_1")
    assert opp is not None
    repo.save(opp.model_copy(update={"reactions": [Reaction(seq=1, text="一句反应")]}))
    DiscoverySnapshotRepository(ws).upsert(
        DiscoverySnapshot(
            id="daily_test",
            created_at=datetime.now(UTC),
            context_fingerprint="ctx",
            ranked_opportunity_ids=[],
            preferred_opportunity_id="opp_person_1",
        )
    )
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    r = CliRunner().invoke(app, ["connect", "daily", "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert payload["preferred_opportunity"]["reactions"][0]["text"] == "一句反应"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_cli_connect.py -k "prepare or carries_reactions" -v`
Expected: the new tests FAIL (`No such option: --reaction`; `reaction` key missing; ready-with-reaction test calls fake with `READY`). Existing prepare tests still PASS.

- [ ] **Step 3: Implement in `src/finch/cli.py`**

Replace `_prepare_new_opportunity`:

```python
def _prepare_new_opportunity(
    settings: Settings,
    ws: Workspace,
    opportunity_id: str,
    *,
    reaction: str | None = None,
) -> PreparedContribution | None:
    """对首选机会（新聚合）执行 select → prepare_contribution → ready（幂等）。

    proposed → select → 生成正文 → 登记 Artifact → mark_ready；parked / closed 不可准备，
    返回 None。已 ready：无 ``reaction`` 直接返回现有成果；有 ``reaction`` 则 ready → selected
    （合法的「调整贡献」转换）后重新生成。
    """
    service = OpportunityService(
        PreferredOpportunityRepository(ws), artifacts=PreferredArtifactRepository(ws)
    )
    opp = service.get(opportunity_id)
    if opp is None:
        return None
    if opp.status in (OpportunityStatus.PARKED, OpportunityStatus.CLOSED):
        return None
    if opp.status == OpportunityStatus.READY:
        if reaction is None:
            return _prepared_contribution_for(ws, opp)
        opp = service.select(opportunity_id)
    elif opp.status == OpportunityStatus.PROPOSED:
        opp = service.select(opportunity_id)
    runner = cast(CodexRunner, create_runner(settings.llm, "critique") or CodexRunner())
    return prepare_contribution(
        opportunity=opp,
        runner=runner,
        service=service,
        voice_profile=load_voice_profile(settings.paths.voice_profile_path),
        confirmed_jobs=ContentJobRepository(ws).list_jobs(),
        practice_profile=load_practice_profile(settings.paths.practice_profile_path),
        reaction=reaction,
    )
```

In `_prepared_contribution_for`, change the final `return` so the existing-artifact path also reports reaction state:

```python
    latest = opp.reactions[-1] if opp.reactions else None
    return PreparedContribution(
        opportunity=opp,
        artifacts=artifacts,
        reaction=latest,
        form_forced=latest is None,
    )
```

Replace `_render_prepared_contribution`:

```python
def _render_prepared_contribution(pc: PreparedContribution) -> list[str]:
    """渲染 prepare 结果：机会摘要 + 反应状态 + 可审阅正文。"""
    lines = _render_preferred_opportunity(pc.opportunity)
    if pc.reaction is not None:
        lines.append(f"你的反应（第 {pc.reaction.seq} 条）：{pc.reaction.text}")
    else:
        lines.append("无反应：本次只准备澄清问题")
    lines.append("")
    for a in pc.artifacts:
        lines.append(f"成果（{a.kind.value}）：")
        lines.append(a.body)
        lines.append("")
    return lines
```

In `_prepared_payload`, add two keys after `"status"`:

```python
        "reaction": (
            {"seq": pc.reaction.seq, "text": pc.reaction.text} if pc.reaction else None
        ),
        "form_forced": pc.form_forced,
```

In `connect_prepare`, add the option after `opportunity_ids` and validate before the loop:

```python
    reaction: str | None = typer.Option(
        None,
        "--reaction",
        help="用户对这条机会的原话（可选；不回答请不传。无反应时只准备澄清问题）",
    ),
```

Right after the existing `if not ids:` block (which exits with "selection required"), insert:

```python
    if reaction is not None and not reaction.strip():
        typer.echo("reaction must not be blank; omit --reaction if the user did not answer")
        raise typer.Exit(code=1)
    if reaction is not None and len(ids) > 1:
        typer.echo("--reaction applies to exactly one --opportunity")
        raise typer.Exit(code=1)
```

Change the loop call to `pc = _prepare_new_opportunity(settings, ws, oid, reaction=reaction)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_cli_connect.py tests/unit/test_cli_preferred_opportunity.py -v`
Expected: all PASS.

- [ ] **Step 5: Lint, type-check, full suite, commit**

Run: `uv run ruff check . && uv run mypy src && uv run pytest -q`
Expected: clean and green.

```bash
git add src/finch/cli.py tests/unit/test_cli_connect.py
git commit -m "feat(cli): connect prepare --reaction; ready + new reaction regenerates"
```

---

### Task 5: Skill presentation, eval cases, docs

**Files:**
- Modify: `skills/peer-discovery/SKILL.md` (CLI list + 「向用户呈现」首选机会 bullet)
- Modify: `skills/peer-discovery/references/presentation.md` (首选机会形状 + 用户下一轮映射)
- Modify: `skills/peer-discovery/evals/cases.yaml` (append cases 8–11)
- Modify: `skills/interaction-preparation/SKILL.md` (CLI + 产出契约 + 边界)
- Modify: `skills/interaction-preparation/references/presentation.md` (形状 + 映射)
- Modify: `AGENTS.md:12`, `CLAUDE.md:32,91`, `README.md:34,82`

**Interfaces:**
- Consumes: `connect prepare --reaction` (Task 4); the gate semantics (Task 3).
- Produces: documentation only; no code.

- [ ] **Step 1: `skills/peer-discovery/SKILL.md`**

In `## CLI`, replace the line
`- 准备互动：\`finch connect prepare --opportunity <id>\`（可重复；每次最多 5；**必须选中**）`
with:

```markdown
- 准备互动：`finch connect prepare --opportunity <id> [--reaction "<用户原话>"]`（可重复；每次最多 5；
  **必须选中**；`--reaction` 只配一条机会。无任何反应时只会得到一个澄清问题）
```

In `## 向用户呈现`, replace the sentence
`以「先做这个切口吗？」收尾，接受自然语言接受/修改/拒绝；不暴露内部命令与状态枚举。`
with:

```markdown
  以**一个指向用户自己经历或分歧的具体问题**收尾（从 `why_me` / `open_questions` / 对方帖子的具体
  细节组织；「A 还是 B」或「你那次是怎么处理的」式，10 秒内能答；禁止「你怎么看」「要不要做」）。
  用户的任何实质回答原话传入 `connect prepare --reaction`；不回答则不传 `--reaction`，此时只会得到
  澄清问题，呈现时说明原因。不暴露内部命令与状态枚举。
```

- [ ] **Step 2: `skills/peer-discovery/references/presentation.md`**

Replace the 首选机会 example block and its 要点 with:

````markdown
## 形状（首选机会）

```text
今天我会优先跟进 @alice 关于 Agent 重试失败的讨论：她保存了 trace，但同一任务重跑结果仍不同，
现有回复主要建议多跑几次。

这和你处理过的调用超时问题有关，但她讨论的是重复执行带来的后果。

你当时最难处理的是等待太久，还是不确定任务到底执行了没有？
```

要点：

- 两三段自然语言，围绕「这次交流还缺什么，我们愿意补上什么」；不暴露内部 id / 状态 / 评分。
- 收尾是**一个用户 10 秒内能答的具体问题**，指向用户自己的经历或分歧（「A 还是 B」/「你那次怎么处理的」），
  由 `why_me` / `open_questions` / 对方帖子细节组织。禁止「你怎么看」「要不要做这个切口」。
- 首选可为空：没有值得优先投入的讨论就明说原因（已解决 / 与现有回复重复 / 证据不足），
  并保留可浏览的线索，不硬凑、不编造对方困难。空首选不提反应问题。
- 用户回答原话进入 `--reaction`，Skill 不润色、不补全、不拆分；一段话里既有经历又有疑问就整段传入。
````

In `## 用户下一轮 → CLI`, replace the `准备互动 N` line with these three:

```markdown
- 用户对收尾问题给出任何实质回答（经历 / 疑问 / 猜测）→
  `uv run finch connect prepare --opportunity {opportunity_id} --reaction "<用户原话>"`
- 「先准备吧」「直接给正文」（不回答）→ `uv run finch connect prepare --opportunity {opportunity_id}`；
  呈现时说明「没有你的反应，这次只准备了一个澄清问题」
- 「今天不弄」/ 换话题 → 不 prepare，自然结束
- `准备互动 N`（浏览列表里选人）→ 先问同样的一个具体问题，再按上面两条走
```

- [ ] **Step 3: `skills/peer-discovery/evals/cases.yaml`**

Append:

```yaml
  - id: 8
    name: 首选机会以具体反应问题收尾
    input:
      preferred_opportunity:
        topic: Agent 重试失败后结果不一致
        why_me: "[agent-100-days] 处理过调用超时，但没处理过重复执行后果"
        open_questions: ["未核对线程已有回复"]
    expected_output:
      closing_line_is_question: true
    assertions:
      - name: 具体问题
        description: 收尾是一个指向用户经历或分歧的具体问题（A 还是 B / 你那次怎么处理）
      - name: 禁止泛问
        description: 不以「你怎么看」「先做这个切口吗」「要不要做」收尾

  - id: 9
    name: 实质回答传入 --reaction
    input:
      user_reply: "我当时最难的是不知道任务到底跑没跑，后来加了幂等键才敢重试"
    expected_output:
      cli: "finch connect prepare --opportunity <id> --reaction \"我当时最难的是不知道任务到底跑没跑，后来加了幂等键才敢重试\""
    assertions:
      - name: 原话
        description: --reaction 传用户原话，不润色、不补全、不拆成多条

  - id: 10
    name: 不回答只得澄清问题
    input:
      user_reply: "先准备吧"
    expected_output:
      cli: "finch connect prepare --opportunity <id>"
      explains_clarifying_only: true
    assertions:
      - name: 不传反应
        description: 不传 --reaction；呈现时说明「没有你的反应，这次只准备了一个澄清问题」

  - id: 11
    name: 空首选不提反应问题
    input:
      preferred_opportunity: null
    expected_output:
      closing_line_is_question: false
    assertions:
      - name: 自然结束
        description: 说明为何无首选并保留可浏览线索，不硬凑反应问题
```

- [ ] **Step 4: `skills/interaction-preparation/SKILL.md`**

In `## CLI`, replace the first bullet with:

```markdown
- 选中机会：`finch connect prepare --opportunity <id> [--reaction "<用户原话>"]`（可重复；本批最多 5 =
  deep_prepare_limit；`--reaction` 只配一条机会）。**无任何反应时代码只允许准备澄清问题**；有反应时沿用
  机会的 `proposal.form`，正文中来自用户的句子标 `[reaction]`。对已 ready 的机会再传新 `--reaction`
  会重新生成。
```

In `## 产出契约（Artifact）`, add after the `- 默认展示：…` bullet:

```markdown
- 正文中标 `[reaction]` 的句子只能复述用户对这条机会亲口说的话，不得外推；`[practice-id]` 句子只能来自
  confirmed practices。两者之外不得出现第一人称经历。
```

In `## 边界`, add:

```markdown
- 反应是用户在本次会话里亲口说的，可以第一人称写；但它不是 confirmed practice，不进
  `practice-profile.yaml`，Skill 不自动建议 `profile add`。
```

- [ ] **Step 5: `skills/interaction-preparation/references/presentation.md`**

Replace the 形状 block with:

````markdown
## 形状

```text
为你选了这条机会，先做这个切口吗？

**来自你的那句**：你说「最难的是不知道任务到底跑没跑，后来加了幂等键才敢重试」——方法卡的第三步就从这里来。
**对象与话题**：@alice 在问「同一任务重跑为何结果不同」，和你处理过的调用超时直接对得上。
**你能补充**：把「先判断是否执行过 → 再决定重试」作为最小抓手。
**来源**：https://x.com/alice/status/1

方法卡正文：
> 适用处境：… 输入：… 步骤：… [reaction] 我当时先加了幂等键才敢重试 … 输出与判断：… 限制：…

状态：待审，未运行。回复「采用」「改：…」或「跳过」。
```

无反应时第一行改为：「没有你的反应，这次只准备了一个澄清问题」，正文只有一个具体观察 + 一个问题。
````

In `## 用户下一轮 → CLI`, replace the `改：…` line with:

```markdown
- `改：…` 改的是你自己的经历或判断 → `uv run finch connect prepare --opportunity <id>
  --reaction "<新原话>"`（追加一条反应并重新生成）；只是改措辞 → 直接按指令改正文后重新生成，
  或走 `finch drafts revise`（若正文走草稿流）。
```

- [ ] **Step 6: Root docs**

`AGENTS.md` line 12 — append one sentence to the bullet: `connect prepare` 接受 `--reaction "<用户原话>"`；无任何反应时只准备澄清问题，正文第一人称只能来自 `[reaction]` 或 confirmed `[practice-id]`。

`CLAUDE.md` line 32 — change to: `interaction-preparation/ 选中后深度准备（finch connect prepare --opportunity [--reaction]；无反应只出澄清问题；单次不超过 deep_prepare_limit，默认 5）`.

`CLAUDE.md` line 91 — after `connect prepare --opportunity\` 对已选机会制作可审阅贡献（Artifact；单次不超过 \`deep_prepare_limit\`，默认 5）` insert: `；\`--reaction "<原话>"\` 记录用户对这条机会的反应（\`Opportunity.reactions\`，append-only），无反应时形式强制 \`clarifying_question\``.

`README.md` line 34 — change to: `uv run finch connect prepare --opportunity ID --reaction "你的一句反应"   # 深度准备（默认每次最多 5；不传 --reaction 只得澄清问题）`.

`README.md` line 82 — change to: `uv run finch connect prepare --opportunity <opportunity_id> [--reaction "<你的原话>"]`.

- [ ] **Step 7: Verify docs are consistent and commit**

Run: `rg -n "先做这个切口吗" skills/peer-discovery` — Expected: no matches in `SKILL.md` / `references/presentation.md` (the phrase may legitimately remain in `interaction-preparation/references/presentation.md` first line).

Run: `uv run pytest -q` — Expected: green (docs-only change).

```bash
git add skills/peer-discovery skills/interaction-preparation AGENTS.md CLAUDE.md README.md
git commit -m "docs(skills): close preferred opportunity with a reaction question; map answer to --reaction"
```

---

## Self-Review

**Spec coverage**

| Spec section | Task |
|---|---|
| §3 `Reaction` model, `Opportunity.reactions`, legacy YAML loads | Task 1 |
| §4.1 `record_reaction` (blank/closed reject, content idempotent, request_id, replay, no status change) | Task 2 |
| §4.2 gate `effective_form`, prompt slot, artifact id suffix, record inside prepare | Task 3 |
| §4.3 `_prepare_new_opportunity` state table | Task 4 |
| §5 CLI `--reaction`, validations, text line, JSON `reaction` / `form_forced`, daily JSON carries `reactions` | Task 4 |
| §6 prompt block + three rules | Task 3 Step 3 |
| §7.1 peer-discovery closing question + mapping | Task 5 Steps 1–2 |
| §7.2 interaction-preparation 「来自你的那句」 + `改：…` mapping + boundary | Task 5 Steps 4–5 |
| §7.3 dialogue-policy unchanged | (no task; verified by omission) |
| §8 error table | Tasks 2–4 (blank, closed, multi-opportunity; LLM-failure case is covered by content idempotency in Task 2 + `selected` state left by `record_reaction` before `write_contribution` in Task 3) |
| §9 tests | Tasks 1–4; cases.yaml in Task 5 Step 3 |
| §10 acceptance 1–4 | Tasks 3–5 (manual run after Task 5); 5 → each task's lint/type/test step |
| §11 two-week observation | Manual; `docs/trial-run.md` cadence, no code |

**Placeholder scan:** none ("TBD"/"similar to" absent; every code step shows code).

**Type consistency:** `record_reaction(opportunity_id, *, text, expected_revision, request_id)` used identically in Tasks 2–3; `prepare_contribution(..., reaction=)` keyword matches Task 4's call and the CLI fakes (`**kw` → `kw.get("reaction")`); `PreparedContribution(reaction=, form_forced=)` fields match Task 4 fakes and `_prepared_contribution_for`; `effective_form` / `artifact_id_for` signatures match tests.

**Known behavioural change surfaced in Task 3 Step 5:** preparing with no reaction now yields a `clarifying_question` artifact (`art_<id>_clarifying_question`, kind `reply_draft`) even when `proposal.form` was `method_card`. This is the intended gate; the one existing test asserting the old id is updated in that step.
