# ASD-STE100 Clarity Editing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every `finch drafts revise` apply Finch ASD-STE100-inspired clarity rules once, persist a `ClarityReview` on the Draft, and extend practice diagnosis plus article analysis with the same shared rules — without a new CLI command.

**Architecture:** Add Pydantic `ClarityReview` on `Draft`; parse preset in Python; change `rewrite_with_instruction` to one structured LLM call (`ClarityEditOutput`) that returns body + review; overwrite `preset` / `rules_version` in code. Shared rules live in `skills/_shared/asd-ste100-inspired.md`. Critic-driven `_rewrite` / `rewrite` (failed-check path) is unchanged. P2 updates practice diagnose prompt + skill docs; P3 adds `ArticleReport.clarity_cost_reductions`.

**Tech Stack:** Python 3.12+, Pydantic 2, typer, existing `CodexRunner` / `StructuredInferenceRunner`, pytest, ruff, mypy. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-03-asd-ste100-clarity-design.md`

## Global Constraints

- No new CLI subcommand; entry remains `finch drafts revise`.
- Every `drafts revise` defaults to preset `asd-ste100-inspired`; switch to `asd-ste100-technical` only when instruction matches the parser below.
- One LLM call per revise; meaning check is part of that output, not a second request.
- Do not claim strict ASD-STE100 compliance in prompts, docs, or outputs.
- Do not invent facts, numbers, actors, or measurements; preserve negation, conditions, uncertainty, and attribution (CL08).
- Do not modify Critic checker suite, auto-publish, or author-position confirmation.
- Do not change `rewrite()` / `_rewrite()` used by the critic loop — only `rewrite_with_instruction`.
- Python 3.12+; ruff `line-length 100`, selects `E,F,I,B,UP`; `uv run mypy src` must stay clean.
- Before every commit: `uv run pytest` (touched tests), `uv run ruff check .`, `uv run mypy src`.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `src/finch/content/clarity.py` | `ClarityChange` / `ClarityReview` / `ClarityEditOutput`, `RULES_VERSION`, `parse_clarity_preset` | Create |
| `src/finch/content/models.py` | Optional `Draft.clarity_review` | Modify |
| `src/finch/content/writer.py` | `rewrite_with_instruction` → clarity path | Modify |
| `prompts/clarity-revise.md` | Structured clarity revise prompt | Create |
| `skills/_shared/asd-ste100-inspired.md` | Shared CL rules + presets (canonical) | Create |
| `skills/_shared/expression-contract.md` | Pointer to clarity rules | Modify |
| `src/finch/cli.py` | `drafts revise` JSON/text include `clarity_review`; article render section | Modify |
| `tests/unit/test_clarity.py` | Preset parse + model + frontmatter | Create |
| `tests/unit/test_cli_drafts.py` | Revise returns / persists `clarity_review` | Modify |
| `tests/unit/test_prompt_placeholders.py` | Register `clarity-revise.md` | Modify |
| `README.md` | Discoverability + trigger examples | Modify |
| `skills/idea-to-draft/SKILL.md` | ASD-STE100 keywords + revise note | Modify |
| `skills/expression-practice/SKILL.md` | Clarity practice trigger + link | Modify |
| `skills/expression-practice/references/diagnosis-rules.md` | Clarity dimension | Modify |
| `skills/article_analysis/SKILL.md` | ASD-STE100 + clarity lens | Modify |
| `skills/feynman-practice/SKILL.md` | Link shared rules | Modify |
| `skills/sticky-message/SKILL.md` | Link shared rules (vs polish) | Modify |
| `src/finch/practice/service.py` | Diagnose prompt mentions clarity / CL rules | Modify |
| `src/finch/article/models.py` | `ClarityCostReduction` + field | Modify |
| `prompts/analyze-article.md` | Ask for clarity_cost_reductions | Modify |
| `tests/unit/test_article_models.py` | New field validation | Modify |
| `tests/unit/test_cli_article.py` | Fake report + render assertion | Modify |
| `docs/superpowers/specs/2026-10-03-asd-ste100-clarity-design.md` | Status → implemented plan | Modify (last) |

---

### Task 1: Clarity models + preset parser + Draft field

**Files:**
- Create: `src/finch/content/clarity.py`
- Modify: `src/finch/content/models.py`
- Test: `tests/unit/test_clarity.py`

**Interfaces:**
- Produces:
  - `RULES_VERSION: str = "finch-clarity-v1"`
  - `ClarityPreset = Literal["asd-ste100-inspired", "asd-ste100-technical"]`
  - `parse_clarity_preset(instruction: str) -> ClarityPreset`
  - `ClarityChange`, `ClarityReview`, `ClarityEditOutput`
  - `Draft.clarity_review: ClarityReview | None = None`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_clarity.py`:

```python
"""ASD-STE100-inspired clarity models and preset parsing."""

from finch.content.clarity import (
    RULES_VERSION,
    ClarityChange,
    ClarityEditOutput,
    ClarityReview,
    parse_clarity_preset,
)
from finch.content.models import Draft, DraftKind
from finch.storage.repositories import DraftRepository
from finch.storage.workspace import Workspace


def test_parse_clarity_preset_default_inspired():
    assert parse_clarity_preset("make it shorter") == "asd-ste100-inspired"
    assert parse_clarity_preset("用 ASD-STE100 的原则优化") == "asd-ste100-inspired"
    assert parse_clarity_preset("asd-ste100-inspired check") == "asd-ste100-inspired"


def test_parse_clarity_preset_technical():
    assert parse_clarity_preset("asd-ste100-technical") == "asd-ste100-technical"
    assert parse_clarity_preset("ASD-STE100-TECHNICAL please") == "asd-ste100-technical"
    assert parse_clarity_preset("改成清晰的技术操作说明") == "asd-ste100-technical"
    assert parse_clarity_preset("写成技术操作说明") == "asd-ste100-technical"


def test_clarity_review_max_three_changes():
    changes = [
        ClarityChange(rule_id=f"CL0{i}", before="a", after="b", reason="r")
        for i in range(1, 4)
    ]
    review = ClarityReview(
        preset="asd-ste100-inspired",
        rules_version=RULES_VERSION,
        changes=changes,
        meaning_check="passed",
    )
    assert len(review.changes) == 3


def test_clarity_edit_output_shape():
    out = ClarityEditOutput(
        body="改后正文",
        clarity_review=ClarityReview(
            preset="asd-ste100-inspired",
            rules_version=RULES_VERSION,
            changes=[],
            meaning_check="passed",
            missing_information=["缺少耗时指标"],
        ),
    )
    assert out.body.startswith("改")
    assert out.clarity_review.missing_information == ["缺少耗时指标"]


def test_draft_clarity_review_frontmatter_roundtrip(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    review = ClarityReview(
        preset="asd-ste100-technical",
        rules_version=RULES_VERSION,
        changes=[
            ClarityChange(
                rule_id="CL02",
                before="处理后更好",
                after="脚本读取 input.csv 并写出 output.csv",
                reason="主体与对象明确",
            )
        ],
        meaning_check="needs_review",
        missing_information=[],
    )
    draft = Draft(
        id="draft_clarity1",
        kind=DraftKind.ORIGINAL,
        language="zh",
        body="正文",
        claims=[],
        clarity_review=review,
    )
    DraftRepository(ws).upsert_draft(draft)
    loaded = DraftRepository(ws).get_draft("draft_clarity1")
    assert loaded is not None
    assert loaded.clarity_review is not None
    assert loaded.clarity_review.preset == "asd-ste100-technical"
    assert loaded.clarity_review.changes[0].rule_id == "CL02"


def test_draft_without_clarity_review_still_loads(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    draft = Draft(
        id="draft_old1",
        kind=DraftKind.ORIGINAL,
        language="zh",
        body="旧稿",
        claims=[],
    )
    DraftRepository(ws).upsert_draft(draft)
    loaded = DraftRepository(ws).get_draft("draft_old1")
    assert loaded is not None
    assert loaded.clarity_review is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_clarity.py -v`  
Expected: FAIL with `ModuleNotFoundError: No module named 'finch.content.clarity'` (or import error for `clarity_review`).

- [ ] **Step 3: Minimal implementation**

Create `src/finch/content/clarity.py`:

```python
"""ASD-STE100-inspired clarity review models and preset parsing."""

from typing import Literal

from pydantic import BaseModel, Field

RULES_VERSION = "finch-clarity-v1"

ClarityPreset = Literal["asd-ste100-inspired", "asd-ste100-technical"]

_TECHNICAL_NEEDLES = (
    "asd-ste100-technical",
    "清晰的技术操作说明",
    "技术操作说明",
)


def parse_clarity_preset(instruction: str) -> ClarityPreset:
    """Deterministic preset from revise instruction. Default inspired."""
    lowered = instruction.casefold()
    if "asd-ste100-technical" in lowered:
        return "asd-ste100-technical"
    for needle in _TECHNICAL_NEEDLES[1:]:
        if needle in instruction:
            return "asd-ste100-technical"
    return "asd-ste100-inspired"


class ClarityChange(BaseModel):
    rule_id: str
    before: str
    after: str
    reason: str


class ClarityReview(BaseModel):
    preset: ClarityPreset
    rules_version: str = RULES_VERSION
    changes: list[ClarityChange] = Field(default_factory=list, max_length=3)
    meaning_check: Literal["passed", "needs_review"]
    missing_information: list[str] = Field(default_factory=list)


class ClarityEditOutput(BaseModel):
    """LLM structured output for drafts revise (body + review)."""

    body: str
    clarity_review: ClarityReview
```

In `src/finch/content/models.py`, add import and field on `Draft`:

```python
from finch.content.clarity import ClarityReview  # avoid cycle: clarity must not import Draft
```

If a cycle appears, define `ClarityReview` in `models.py` instead and re-export from `clarity.py` — prefer `clarity.py` with models importing it (Draft → clarity is fine; clarity must not import Draft).

```python
class Draft(BaseModel):
    # ... existing fields ...
    clarity_review: ClarityReview | None = None
```

Use a late import or TYPE_CHECKING only if mypy/ruff complains; default is direct import at top of `models.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_clarity.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/finch/content/clarity.py src/finch/content/models.py tests/unit/test_clarity.py
git commit -m "$(cat <<'EOF'
feat(content): add ClarityReview models and preset parser

EOF
)"
```

---

### Task 2: Shared rules document

**Files:**
- Create: `skills/_shared/asd-ste100-inspired.md`
- Modify: `skills/_shared/expression-contract.md`

**Interfaces:**
- Produces: canonical markdown at `skills/_shared/asd-ste100-inspired.md` (loaded by writer in Task 3).

- [ ] **Step 1: Write the shared rules file**

Create `skills/_shared/asd-ste100-inspired.md` with exactly this structure (full text required — do not leave sections empty):

```markdown
# Finch ASD-STE100-inspired clarity rules

Project adaptations inspired by ASD-STE100 (Simplified Technical English).
Official FAQ: https://www.asd-ste100.org/STE_faq.html
Do **not** claim strict ASD-STE100 compliance.

Rules version: `finch-clarity-v1`

## Presets

| Preset | When | Behaviour |
|---|---|---|
| `asd-ste100-inspired` (default) | Chinese drafts, social posts, explanations | Local edits; keep story, tone, useful metaphors; fix ambiguity, abstraction, term drift |
| `asd-ste100-technical` | Procedures, how-tos, technical steps | Explicit actor, action, prerequisites; prefer one instruction per sentence; these requirements are not relaxed by user style instructions |

Conflict priority: **facts and original meaning > clarity > personal voice > neat copy**.

Default preset: user instruction may change *method* (full rewrite, keep a metaphor).
Technical preset: actor / action / prerequisites are not relaxed by instruction.
Never invent actors, examples, measurements, or numbers.

## Chinese adaptations

- Do not apply English word-count caps or unverified Chinese character hard limits.
- Prefer concrete verbs and consistent terms; keep necessary narrative.

## Rules (CL01–CL08)

| ID | Rule | Edit behaviour |
|---|---|---|
| CL01 | One main idea per sentence when practical | Split independent judgments; keep needed causality |
| CL02 | Actor, action, object clear | State who did what to what; never invent unknown actors |
| CL03 | Same concept, same name | Unify synonyms; keep real conceptual differences |
| CL04 | Abstract claims need concrete support | Name the missing fact; do not invent metrics |
| CL05 | Prerequisites near the advice | Condition before action or conclusion |
| CL06 | Steps must be executable | In technical preset, split commands; name object and checks |
| CL07 | One topic per paragraph | Drop repeated setup; fix sudden topic jumps |
| CL08 | Simplify without changing meaning | Keep negation, numbers, scope, probability, conditions, attribution, uncertainty |

## Output contract for editors

- Prefer local edits; at most three high-value explained changes with rule IDs.
- If clarity needs missing facts, list gaps instead of filling them.
- Check final wording for meaning drift (`passed` / `needs_review`).
- Keywords ASD-STE100 may trigger editing; do not force them into published prose.

## Safe example

Bad abstract: 「通过优化证据处理机制，显著提升系统性能。」
Feedback: missing concrete change and metric — ask which of latency, cost, or accuracy changed.
After author supplies parallelization and 60s→25s on one run:
「我把证据请求从串行改成并行。这次运行的耗时从 60 秒降到 25 秒。」
Do not expand to “always” or all runs.
```

- [ ] **Step 2: Point expression-contract at the rules**

Append to `skills/_shared/expression-contract.md`:

```markdown

## Clarity editing (ASD-STE100-inspired)

`finch drafts revise` loads `skills/_shared/asd-ste100-inspired.md` by default
(`asd-ste100-inspired`; switch to `asd-ste100-technical` when the instruction asks
for technical procedure wording). See that file for CL01–CL08. Clarity editing
does not replace expression-practice (user writes first) and does not auto-publish.
```

- [ ] **Step 3: Verify file is readable from repo root**

Run: `test -f skills/_shared/asd-ste100-inspired.md && rg -n "CL01|ASD-STE100|finch-clarity-v1" skills/_shared/asd-ste100-inspired.md skills/_shared/expression-contract.md`  
Expected: matches for those strings.

- [ ] **Step 4: Commit**

```bash
git add skills/_shared/asd-ste100-inspired.md skills/_shared/expression-contract.md
git commit -m "$(cat <<'EOF'
docs(skills): add shared ASD-STE100-inspired clarity rules

EOF
)"
```

---

### Task 3: `rewrite_with_instruction` clarity path

**Files:**
- Create: `prompts/clarity-revise.md`
- Modify: `src/finch/content/writer.py`
- Modify: `tests/unit/test_prompt_placeholders.py`
- Test: extend `tests/unit/test_clarity.py` with a fake-runner unit test (or new `tests/unit/test_clarity_writer.py`)

**Interfaces:**
- Consumes: `parse_clarity_preset`, `ClarityEditOutput`, `RULES_VERSION`, rules file path
- Produces: `rewrite_with_instruction(...) -> Draft` with `body` and `clarity_review` set; `preset` / `rules_version` overwritten in Python; `changes` truncated to 3
- Unchanged: `rewrite(...)` / `_rewrite(...)` critic path

- [ ] **Step 1: Write failing writer test**

Add to `tests/unit/test_clarity.py` (or create `tests/unit/test_clarity_writer.py`):

```python
from finch.content.clarity import RULES_VERSION, ClarityEditOutput, ClarityReview
from finch.content.models import Draft, DraftKind
from finch.content.writer import rewrite_with_instruction


class _FakeRunner:
    def __init__(self, output: ClarityEditOutput):
        self.output = output
        self.prompts: list[str] = []

    def run(self, prompt: str, model):
        self.prompts.append(prompt)
        assert model is ClarityEditOutput
        return self.output


def test_rewrite_with_instruction_applies_clarity_and_overwrites_preset():
    draft = Draft(
        id="draft_x",
        kind=DraftKind.ORIGINAL,
        language="zh",
        body="通过优化显著提升性能。",
        claims=[],
        content_job_id=None,
        run_id="idea",
    )
    llm_out = ClarityEditOutput(
        body="请补充具体改动与指标后再写性能结论。",
        clarity_review=ClarityReview(
            preset="asd-ste100-technical",  # model lies — code must overwrite
            rules_version="wrong",
            changes=[],
            meaning_check="passed",
            missing_information=["缺少改动方式与耗时/准确率指标"],
        ),
    )
    runner = _FakeRunner(llm_out)
    revised = rewrite_with_instruction(
        runner, draft, "用 ASD-STE100 的原则优化，保留语气", {}, None
    )
    assert revised.id == "draft_x"
    assert revised.body == llm_out.body
    assert revised.clarity_review is not None
    assert revised.clarity_review.preset == "asd-ste100-inspired"
    assert revised.clarity_review.rules_version == RULES_VERSION
    assert "ASD-STE100" in runner.prompts[0] or "clarity" in runner.prompts[0].casefold()
    assert "CL01" in runner.prompts[0] or "asd-ste100-inspired" in runner.prompts[0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_clarity.py::test_rewrite_with_instruction_applies_clarity_and_overwrites_preset -v`  
Expected: FAIL (still returns Draft-only / wrong schema).

- [ ] **Step 3: Add prompt file**

Create `prompts/clarity-revise.md`:

```markdown
Apply the Finch ASD-STE100-inspired clarity rules below.
For Chinese content, use the project's Chinese adaptations.
Do not claim strict ASD-STE100 compliance.

Keep the author's claims, evidence, numbers, conditions,
negation, uncertainty, and source attribution unchanged.
Prefer local edits. Do not invent actors, examples, or measurements.
Preserve useful narrative and metaphors in the default preset.
Use explicit actions and prerequisites in the technical preset.
Explain at most three high-value edits with their rule IDs.
If clarity requires missing facts, identify the gap instead of filling it.
Check the final wording for changes in meaning.

Active preset (chosen by code; echo it in clarity_review.preset): {preset}

## Shared rules
{rules}

## User instruction
{instruction}

{job_context}## Original draft
{body}

## Evidence cards
{cards}

Return JSON matching ClarityEditOutput: body (revised full text) and clarity_review
(preset, rules_version, changes[≤3] with rule_id/before/after/reason,
meaning_check passed|needs_review, missing_information).
```

- [ ] **Step 4: Implement writer path**

In `src/finch/content/writer.py`:

1. Import `ClarityEditOutput`, `RULES_VERSION`, `parse_clarity_preset` from `finch.content.clarity`.
2. Add paths:

```python
_CLARITY_PROMPT_PATH = Path("prompts/clarity-revise.md")
_CLARITY_RULES_PATH = Path("skills/_shared/asd-ste100-inspired.md")
```

3. Replace `rewrite_with_instruction` body so it does **not** call `_rewrite`. Keep `rewrite` → `_rewrite` as today.

```python
def rewrite_with_instruction(
    runner: CodexRunner,
    draft: Draft,
    instruction: str,
    cards_by_id: dict[str, EvidenceCard],
    job: ContentJob | None = None,
) -> Draft:
    """按自然语言指令重写，并附带 ASD-STE100-inspired ClarityReview（一次调用）。"""
    preset = parse_clarity_preset(instruction)
    card_ids = {ref.evidence_card_id for ref in draft.claims}
    cards = [cards_by_id[cid] for cid in card_ids if cid in cards_by_id]
    prompt = _CLARITY_PROMPT_PATH.read_text().format(
        preset=preset,
        rules=_CLARITY_RULES_PATH.read_text(),
        instruction=instruction,
        job_context=_render_job_context(job),
        body=draft.body,
        cards=_render_cards(cards),
    )
    out = cast(ClarityEditOutput, runner.run(prompt, ClarityEditOutput))
    review = out.clarity_review.model_copy(
        update={
            "preset": preset,
            "rules_version": RULES_VERSION,
            "changes": list(out.clarity_review.changes[:3]),
        }
    )
    sanitized = _sanitize_draft_claims(
        Draft(
            id=draft.id,
            kind=draft.kind,
            candidate_id=draft.candidate_id,
            language=draft.language,
            body=out.body,
            claims=draft.claims,
            content_job_id=draft.content_job_id,
            position_statement=draft.position_statement,
            critic_report_id=draft.critic_report_id,
            run_id=draft.run_id,
            clarity_review=review,
        )
    )
    return sanitized.model_copy(
        update={
            "body": out.body,
            "clarity_review": review,
            "claims": sanitized.claims,
        }
    )
```

Simplify if preferred: build `Draft` via `draft.model_copy(update={...})` then sanitize claims — keep id/kind/run_id/etc.

- [ ] **Step 5: Register prompt placeholders**

In `tests/unit/test_prompt_placeholders.py` add:

```python
    "prompts/clarity-revise.md": {
        "preset",
        "rules",
        "instruction",
        "job_context",
        "body",
        "cards",
    },
```

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/unit/test_clarity.py tests/unit/test_prompt_placeholders.py -v`  
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add prompts/clarity-revise.md src/finch/content/writer.py tests/unit/test_clarity.py tests/unit/test_prompt_placeholders.py
git commit -m "$(cat <<'EOF'
feat(drafts): apply ASD-STE100-inspired clarity on revise

EOF
)"
```

---

### Task 4: CLI `drafts revise` surfaces `clarity_review`

**Files:**
- Modify: `src/finch/cli.py` (`drafts_revise` ~1420–1451)
- Modify: `tests/unit/test_cli_drafts.py`

**Interfaces:**
- Consumes: `Draft.clarity_review` from `rewrite_with_instruction`
- Produces: `--json` payload includes `clarity_review`; human output lists up to 3 changes

- [ ] **Step 1: Update failing CLI expectations**

Change `_FakeRewrite.rewrite` in `tests/unit/test_cli_drafts.py` to return a draft with `clarity_review`:

```python
from finch.content.clarity import RULES_VERSION, ClarityChange, ClarityReview

class _FakeRewrite:
    called: list[dict] = []

    @staticmethod
    def rewrite(runner, draft, instruction, cards_by_id, job):
        _FakeRewrite.called.append(
            {"instruction": instruction, "job_id": job.id if job else None}
        )
        review = ClarityReview(
            preset="asd-ste100-inspired",
            rules_version=RULES_VERSION,
            changes=[
                ClarityChange(
                    rule_id="CL04",
                    before="显著提升",
                    after="（待补充指标）",
                    reason="抽象判断缺支撑",
                )
            ],
            meaning_check="passed",
            missing_information=["缺少性能指标"],
        )
        return draft.model_copy(
            update={"body": "REVISED: " + draft.body, "clarity_review": review}
        )
```

Extend `test_drafts_revise_updates_body`:

```python
    assert payload["clarity_review"]["preset"] == "asd-ste100-inspired"
    assert payload["clarity_review"]["changes"][0]["rule_id"] == "CL04"
    stored = DraftRepository(ws).get_draft("draft_fake1234")
    assert stored.clarity_review is not None
    assert stored.clarity_review.missing_information == ["缺少性能指标"]
```

Add test for human-readable output containing `CL04` or `关键修改`.

- [ ] **Step 2: Run CLI test — expect fail on missing key**

Run: `uv run pytest tests/unit/test_cli_drafts.py::test_drafts_revise_updates_body -v`  
Expected: FAIL (`clarity_review` not in payload).

- [ ] **Step 3: Update `drafts_revise`**

In `src/finch/cli.py` `drafts_revise`, after `upsert_draft(revised)`:

```python
    review_payload = (
        revised.clarity_review.model_dump(mode="json")
        if revised.clarity_review is not None
        else None
    )
    if as_json:
        payload = {
            "draft_id": revised.id,
            "body": revised.body,
            "clarity_review": review_payload,
        }
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        typer.echo(f"已更新草稿 {revised.id}:")
        typer.echo(revised.body)
        if revised.clarity_review is not None:
            cr = revised.clarity_review
            typer.echo("")
            typer.echo(
                f"清晰度复核：preset={cr.preset} rules={cr.rules_version} "
                f"meaning_check={cr.meaning_check}"
            )
            if cr.changes:
                typer.echo("关键修改：")
                for ch in cr.changes:
                    typer.echo(f"- [{ch.rule_id}] {ch.reason}")
                    typer.echo(f"  前：{ch.before}")
                    typer.echo(f"  后：{ch.after}")
            if cr.missing_information:
                typer.echo("待补信息：")
                for gap in cr.missing_information:
                    typer.echo(f"- {gap}")
```

- [ ] **Step 4: Run drafts CLI tests**

Run: `uv run pytest tests/unit/test_cli_drafts.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/finch/cli.py tests/unit/test_cli_drafts.py
git commit -m "$(cat <<'EOF'
feat(cli): print and JSON-export clarity_review on drafts revise

EOF
)"
```

---

### Task 5: Keyword landing (README + skills)

**Files:**
- Modify: `README.md`
- Modify: `skills/idea-to-draft/SKILL.md`
- Modify: `skills/expression-practice/SKILL.md`
- Modify: `skills/article_analysis/SKILL.md`
- Modify: `skills/feynman-practice/SKILL.md` (short pointer)
- Modify: `skills/sticky-message/SKILL.md` (short pointer: clarity polish ≠ sticky rediscovery)

**Interfaces:**
- Produces: `rg ASD-STE100` hits in README and listed skills.

- [ ] **Step 1: README section**

Under 「表达复利循环」or 「自然语言用法」, add:

```markdown
### 清晰表达（ASD-STE100-inspired）

草稿修订默认应用 Finch 的 ASD-STE100-inspired 清晰规则（中文适配，不声称官方合规）：

```bash
uv run finch drafts revise <draft_id> --instruction "用 ASD-STE100 的原则优化，保留我的语气"
uv run finch drafts revise <draft_id> --instruction "改成清晰的技术操作说明"
```

规则见 `skills/_shared/asd-ste100-inspired.md`。练习清晰表达走 `expression-practice`；从文章学习降低理解成本的写法见 `finch article analyze`。
```

- [ ] **Step 2: Skill description keywords**

In each skill frontmatter `description` and/or body, ensure searchable strings: `ASD-STE100`, and where natural `Simplified Technical English` / `简化技术英语` / `清晰表达`.

- `idea-to-draft`: note that post-draft clarity editing is `drafts revise` with shared rules.
- `expression-practice`: trigger examples「帮我练习清晰表达」「按 ASD-STE100-inspired 练一句」; link shared rules; user writes first.
- `article_analysis`: mention optional clarity cost-reduction lens (`clarity_cost_reductions`) inspired by ASD-STE100-inspired rules.
- `feynman-practice`: one line — for prose clarity editing of a draft, see shared rules / `drafts revise`; Feynman remains understanding-debug.
- `sticky-message`: one line — sticky rediscovers the core message; sentence-level ASD-STE100-inspired clarity is `drafts revise`, not this skill.

- [ ] **Step 3: Verify ripgrep**

Run: `rg -n "ASD-STE100" README.md skills/idea-to-draft/SKILL.md skills/expression-practice/SKILL.md skills/article_analysis/SKILL.md skills/feynman-practice/SKILL.md skills/sticky-message/SKILL.md skills/_shared/asd-ste100-inspired.md prompts/clarity-revise.md`  
Expected: each file has at least one hit.

- [ ] **Step 4: Commit**

```bash
git add README.md skills/idea-to-draft/SKILL.md skills/expression-practice/SKILL.md skills/article_analysis/SKILL.md skills/feynman-practice/SKILL.md skills/sticky-message/SKILL.md
git commit -m "$(cat <<'EOF'
docs: land ASD-STE100 keywords in README and expression skills

EOF
)"
```

---

### Task 6: Practice short training (P2)

**Files:**
- Modify: `skills/expression-practice/references/diagnosis-rules.md`
- Modify: `src/finch/practice/service.py` (`_DIAGNOSE_PROMPT`)
- Modify: `skills/expression-practice/SKILL.md` (if not fully done in Task 5)
- Test: `tests/unit/test_practice_service.py` (prompt content via fake runner capturing prompt — add if none exists)

**Interfaces:**
- Consumes: shared rules concepts CL01–CL08 (by reference in prompt text, not file read required)
- Produces: diagnose prompt includes clarity dimension; skill flow still user-first

- [ ] **Step 1: Update diagnosis-rules.md**

Append:

```markdown

## 清晰度（ASD-STE100-inspired）

当用户明确要求练习清晰表达时，优先从 CL01–CL08 中选**一个**最大障碍
（见 `skills/_shared/asd-ste100-inspired.md`）：一句多意、主体不明、术语漂移、
抽象无支撑、前置条件过远、步骤不可执行、段内跳题、简化伤原意。
仍只追问一个问题；先让作者改一句，再给参考版本与适用条件。
```

- [ ] **Step 2: Update `_DIAGNOSE_PROMPT`**

In `src/finch/practice/service.py`, extend the Check dimensions line:

```text
Check dimensions: real understanding / own judgment / causality / relevance to the peer /
specificity / sounds like the author / leaves room to respond /
clarity (ASD-STE100-inspired CL01–CL08: one idea per sentence, clear actor/action,
consistent terms, concrete support, prerequisites near advice, executable steps,
one topic per paragraph, meaning preserved).
When the user asked to practice clarity, prefer the single biggest clarity obstacle.
```

Do not add a second LLM call or new PracticeSession fields.

- [ ] **Step 3: Optional unit test — prompt contains clarity**

If easy: monkeypatch runner to capture prompt in `test_practice_service.py` and assert `"CL01"` or `"clarity"` in prompt. Skip only if existing fakes make this costly — then rely on string presence in source via a tiny test:

```python
from finch.practice import service as practice_service

def test_diagnose_prompt_mentions_clarity_rules():
    assert "CL01" in practice_service._DIAGNOSE_PROMPT or "clarity" in practice_service._DIAGNOSE_PROMPT.casefold()
    assert "ASD-STE100" in practice_service._DIAGNOSE_PROMPT
```

- [ ] **Step 4: Run practice tests**

Run: `uv run pytest tests/unit/test_practice_service.py tests/unit/test_cli_practice.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add skills/expression-practice/references/diagnosis-rules.md src/finch/practice/service.py tests/unit/test_practice_service.py skills/expression-practice/SKILL.md
git commit -m "$(cat <<'EOF'
feat(practice): include ASD-STE100-inspired clarity in diagnosis

EOF
)"
```

---

### Task 7: Article `clarity_cost_reductions` (P3)

**Files:**
- Modify: `src/finch/article/models.py`
- Modify: `prompts/analyze-article.md`
- Modify: `src/finch/cli.py` (`_render_article_report`)
- Modify: `tests/unit/test_article_models.py`
- Modify: `tests/unit/test_cli_article.py`
- Modify: `skills/article_analysis/references/output-contract.md` (if present)

**Interfaces:**
- Produces: `ClarityCostReduction` model; `ArticleReport.clarity_cost_reductions: list[...]` default `[]`, `max_length=3`
- Consumes: existing analyze pipeline (no persistence)

- [ ] **Step 1: Failing model test**

Add to `tests/unit/test_article_models.py`:

```python
from finch.article.models import ClarityCostReduction

def test_clarity_cost_reductions_max_three():
    items = [
        ClarityCostReduction(
            excerpt=f"e{i}",
            method="m",
            reader_effect="r",
            mini_exercise="ex",
            rule_id="CL01",
        )
        for i in range(3)
    ]
    report = ArticleReport(
        expression_task=ExpressionTask(topic="t", primary_task="p"),
        audience_change=AudienceChange(
            who="w", before="b", after="a", fit_check="f"
        ),
        effectiveness=Effectiveness(
            clarity="c", concreteness="c", credibility="c", actionability="n/a"
        ),
        transferable_methods=_methods(2),
        clarity_cost_reductions=items,
    )
    assert len(report.clarity_cost_reductions) == 3

def test_clarity_cost_reductions_default_empty():
    report = ArticleReport(
        expression_task=ExpressionTask(topic="t", primary_task="p"),
        audience_change=AudienceChange(
            who="w", before="b", after="a", fit_check="f"
        ),
        effectiveness=Effectiveness(
            clarity="c", concreteness="c", credibility="c", actionability="n/a"
        ),
        transferable_methods=_methods(2),
    )
    assert report.clarity_cost_reductions == []
```

- [ ] **Step 2: Run — expect fail**

Run: `uv run pytest tests/unit/test_article_models.py::test_clarity_cost_reductions_default_empty -v`  
Expected: FAIL (`ClarityCostReduction` missing).

- [ ] **Step 3: Models**

In `src/finch/article/models.py`:

```python
class ClarityCostReduction(BaseModel):
    """写法如何降低理解成本（ASD-STE100-inspired lens）。"""

    excerpt: str
    method: str
    reader_effect: str
    mini_exercise: str
    rule_id: str | None = None  # optional CL*
```

On `ArticleReport`:

```python
    clarity_cost_reductions: list[ClarityCostReduction] = Field(
        default_factory=list, max_length=3
    )
```

- [ ] **Step 4: Prompt**

In `prompts/analyze-article.md`, after transferable_methods step, add:

```markdown
6. clarity_cost_reductions (0–3 items, ASD-STE100-inspired clarity lens):
   For each: excerpt (verbatim) → method → reader_effect → mini_exercise;
   optional rule_id from CL01–CL08 when it clearly fits.
   Focus on moves that lower ambiguity, make actors/actions concrete, keep
   consistent terms, or place prerequisites next to advice.
   If none are clear, return an empty list — do not invent.
```

- [ ] **Step 5: CLI render**

In `_render_article_report`, after「可借鉴方法」block (before limitations):

```python
    if report.clarity_cost_reductions:
        lines += ["", "## 降低理解成本的写法（ASD-STE100-inspired）"]
        for item in report.clarity_cost_reductions:
            rid = f" [{item.rule_id}]" if item.rule_id else ""
            lines += [
                f"- 「{item.excerpt}」→ {item.method}{rid}",
                f"  作用：{item.reader_effect}",
                f"  练习：{item.mini_exercise}",
            ]
```

- [ ] **Step 6: Update `_FakeService` in `test_cli_article.py`**

Add `clarity_cost_reductions=[]` or one sample item; add assertion that text mode with a sample includes `降低理解成本` when non-empty.

- [ ] **Step 7: Run article tests**

Run: `uv run pytest tests/unit/test_article_models.py tests/unit/test_article_service.py tests/unit/test_cli_article.py -v`  
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add src/finch/article/models.py prompts/analyze-article.md src/finch/cli.py tests/unit/test_article_models.py tests/unit/test_cli_article.py skills/article_analysis/references/output-contract.md
git commit -m "$(cat <<'EOF'
feat(article): add clarity_cost_reductions lens to article analyze

EOF
)"
```

---

### Task 8: Spec status + full verification

**Files:**
- Modify: `docs/superpowers/specs/2026-10-03-asd-ste100-clarity-design.md` (status line)

- [ ] **Step 1: Update spec header**

```markdown
状态：已确认（实现计划见 docs/superpowers/plans/2026-10-03-asd-ste100-clarity.md）
```

- [ ] **Step 2: Acceptance ripgrep**

Run:

```bash
rg -n "ASD-STE100" README.md skills/_shared/asd-ste100-inspired.md prompts/clarity-revise.md skills/idea-to-draft/SKILL.md skills/expression-practice/SKILL.md skills/article_analysis/SKILL.md
uv run pytest tests/unit/test_clarity.py tests/unit/test_cli_drafts.py tests/unit/test_practice_service.py tests/unit/test_article_models.py tests/unit/test_cli_article.py tests/unit/test_prompt_placeholders.py -v
uv run ruff check .
uv run mypy src
```

Expected: all green; keyword hits present.

- [ ] **Step 3: Manual semantic checklist (human, not CI)**

Using five sample drafts or the spec’s必测样例, confirm via real or staged revise:

- 「可能减少」not →「减少」
- 「这次运行」not →「总是」
- prerequisite clauses kept
- unknown actor not invented
- useful metaphor kept under default preset

Record notes in the PR / commit message body if anything fails — do not “fix” by weakening CL08.

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-10-03-asd-ste100-clarity-design.md
git commit -m "$(cat <<'EOF'
docs: mark ASD-STE100 clarity design as planned

EOF
)"
```

---

## Spec coverage (self-review)

| Spec requirement | Task |
|---|---|
| Shared rules CL01–CL08 + presets | Task 2 |
| Keywords in README / skills / prompts | Tasks 2, 3, 5 |
| Default inspired on every `drafts revise` | Tasks 1, 3 |
| Technical preset switch | Task 1 parser + Task 3 |
| Conflict priority by preset | Task 2 rules + Task 3 prompt |
| ClarityReview on Draft / CLI | Tasks 1, 3, 4 |
| One LLM call + meaning_check | Task 3 |
| No new CLI / no Critic path change | Global + Task 3 |
| Practice clarity training | Task 6 |
| Article clarity_cost_reductions | Task 7 |
| Acceptance / semantic samples | Task 8 |

**Placeholder scan:** none intentional.  
**Type consistency:** `ClarityReview` / `ClarityEditOutput` / `RULES_VERSION` / `parse_clarity_preset` names stable across tasks; `rewrite_with_instruction` still returns `Draft`.
