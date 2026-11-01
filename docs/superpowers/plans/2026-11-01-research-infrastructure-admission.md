# Research Infrastructure Phase 1 — Admission Write Policy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Separate evidence validation from problem admission so cards without valid evidence land as `quarantined` (kept, not silent-dropped) and default inbox queries only show `verified|accepted`.

**Architecture:** Keep existing `ProblemStatus`. Add `Admission` + `admission_reason` on `Problem`. `build_problem` stays the evidence gate (tighten N05 author independence). New pure `admit_problem` sets admission from evidence count. Wire into `validate_upsert` and `discover_problems`. Filter `list`/`summary` by default; optional `admit` CLI for human `accepted`.

**Tech Stack:** Python 3.12, Pydantic 2, existing file Workspace YAML repos, pytest, typer CLI.

**Spec:** `docs/superpowers/specs/2026-11-01-research-infrastructure-admission-design.md`

## Global Constraints

- Do not delete `discover_problems` / `assess_problems`.
- No DSL, no new Agent Runtime, no Product–Problem relation layer.
- No physical quarantine directory; same `problems/` path + query filter.
- Do not invent semantic “claim supported by evidence” checks.
- Prefer minimal diffs; bilingual comments only where the file already uses them.
- Ruff line-length 100; run targeted pytest per task.

## File map

| File | Role |
|------|------|
| `src/finch/problem_discovery/models.py` | `Admission`, Problem/Evidence fields, legacy load compat |
| `src/finch/problem_discovery/discover.py` | N05 cap, `admit_problem`, draft fields, discover hook |
| `src/finch/problem_discovery/shadow.py` | `validate_upsert` → admit; accepted protection |
| `src/finch/problem_discovery/repository.py` | optional `list_admitted` helper |
| `src/finch/problem_discovery/summary.py` | summary counts respect admission filter |
| `src/finch/cli.py` | `--admission` on list/summary; `admit` command |
| `tests/unit/test_problem_discovery_discover.py` | N05 + admit unit tests |
| `tests/unit/test_problem_discovery_admission.py` | new focused admission/filter/upsert tests |

---

### Task 1: Model — Admission enum and fields

**Files:**
- Modify: `src/finch/problem_discovery/models.py`
- Modify: `src/finch/problem_discovery/discover.py` (`ProblemEvidenceDraft` only in this task if needed for forward refs — prefer Task 2 for draft fields)
- Test: `tests/unit/test_problem_discovery_admission.py` (create)

**Interfaces:**
- Produces: `Admission` StrEnum; `Problem.admission`, `Problem.admission_reason`; `ProblemEvidence.author_id`, `ProblemEvidence.author_identity_confidence`

- [ ] **Step 1: Write failing tests for model defaults and legacy load**

Create `tests/unit/test_problem_discovery_admission.py`:

```python
from finch.problem_discovery.models import Admission, Problem, ProblemEvidence


def test_problem_default_admission_is_quarantined_when_no_evidence_on_construct():
    # Fresh construct without going through admit_problem: leave default
    # as quarantined so accidental writes are safe.
    p = Problem(problem_id="prob_x", title="t")
    assert p.admission == Admission.QUARANTINED
    assert p.admission_reason == ""


def test_legacy_dict_without_admission_infers_from_evidence():
    raw = {
        "problem_id": "prob_legacy",
        "title": "legacy",
        "evidence": [
            {
                "source_ref": "s1",
                "quote": "q",
                "claim": "c",
            }
        ],
    }
    p = Problem.model_validate(raw)
    assert p.admission == Admission.VERIFIED


def test_legacy_dict_without_admission_empty_evidence_quarantines():
    p = Problem.model_validate({"problem_id": "prob_y", "title": "y", "evidence": []})
    assert p.admission == Admission.QUARANTINED
```

- [ ] **Step 2: Run tests — expect FAIL (Admission missing)**

Run: `uv run pytest tests/unit/test_problem_discovery_admission.py -v`  
Expected: FAIL import/attribute errors for `Admission`

- [ ] **Step 3: Implement model fields**

In `models.py`:

```python
class Admission(StrEnum):
    DRAFT = "draft"
    QUARANTINED = "quarantined"
    VERIFIED = "verified"
    ACCEPTED = "accepted"


class ProblemEvidence(BaseModel):
    # ... existing fields ...
    author_id: str = ""
    author_identity_confidence: Literal["unknown", "stated", "verified"] = "unknown"


class Problem(BaseModel):
    # ... existing fields ...
    admission: Admission = Admission.QUARANTINED
    admission_reason: str = ""

    @model_validator(mode="before")
    @classmethod
    def _legacy_admission(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        if "admission" in data and data["admission"] not in (None, ""):
            return data
        evidence = data.get("evidence") or []
        data = {**data, "admission": "verified" if evidence else "quarantined"}
        if not data.get("admission_reason"):
            data["admission_reason"] = (
                "has_valid_evidence" if evidence else "no_valid_evidence"
            )
        return data
```

Export `Admission` from `problem_discovery/__init__.py` if that module re-exports models.

- [ ] **Step 4: Run tests — expect PASS**

Run: `uv run pytest tests/unit/test_problem_discovery_admission.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/finch/problem_discovery/models.py src/finch/problem_discovery/__init__.py \
  tests/unit/test_problem_discovery_admission.py
git commit -m "feat(problem-discovery): add Admission field alongside status"
```

---

### Task 2: `admit_problem` + wire build drafts

**Files:**
- Modify: `src/finch/problem_discovery/discover.py`
- Test: `tests/unit/test_problem_discovery_admission.py`

**Interfaces:**
- Consumes: `Problem` with `evidence`
- Produces: `admit_problem(problem: Problem, *, force_draft: bool = False) -> Problem`

- [ ] **Step 1: Write failing tests**

```python
from finch.problem_discovery.discover import admit_problem
from finch.problem_discovery.models import Admission, Problem, ProblemEvidence


def test_admit_problem_quarantines_without_evidence():
    p = Problem(problem_id="prob_a", title="a", evidence=[])
    out = admit_problem(p)
    assert out.admission == Admission.QUARANTINED
    assert out.admission_reason == "no_valid_evidence"


def test_admit_problem_verifies_with_evidence():
    p = Problem(
        problem_id="prob_b",
        title="b",
        evidence=[ProblemEvidence(source_ref="s", quote="q")],
    )
    out = admit_problem(p)
    assert out.admission == Admission.VERIFIED
    assert out.admission_reason == "has_valid_evidence"


def test_admit_problem_force_draft():
    p = Problem(
        problem_id="prob_c",
        title="c",
        evidence=[ProblemEvidence(source_ref="s", quote="q")],
    )
    out = admit_problem(p, force_draft=True)
    assert out.admission == Admission.DRAFT
    assert out.admission_reason == "host_draft"
```

- [ ] **Step 2: Run — expect FAIL (`admit_problem` missing)**

Run: `uv run pytest tests/unit/test_problem_discovery_admission.py::test_admit_problem_quarantines_without_evidence -v`

- [ ] **Step 3: Implement `admit_problem`**

In `discover.py`:

```python
def admit_problem(problem: Problem, *, force_draft: bool = False) -> Problem:
    """Write-policy gate: structure only (evidence count). Does not score claim semantics."""
    if force_draft:
        problem.admission = Admission.DRAFT
        problem.admission_reason = "host_draft"
        return problem
    if problem.evidence:
        problem.admission = Admission.VERIFIED
        problem.admission_reason = "has_valid_evidence"
    else:
        problem.admission = Admission.QUARANTINED
        problem.admission_reason = "no_valid_evidence"
    return problem
```

Import `Admission` from models. Extend `ProblemEvidenceDraft` with `author_id: str = ""` and `author_identity_confidence: str = "unknown"` so later N05 can pass through `build_problem`.

In `build_problem`, copy those draft fields onto `ProblemEvidence`.

- [ ] **Step 4: Run admission tests — PASS**

Run: `uv run pytest tests/unit/test_problem_discovery_admission.py -v`

- [ ] **Step 5: Commit**

```bash
git add src/finch/problem_discovery/discover.py tests/unit/test_problem_discovery_admission.py
git commit -m "feat(problem-discovery): add admit_problem write policy gate"
```

---

### Task 3: Tighten N05 evidence level cap

**Files:**
- Modify: `src/finch/problem_discovery/discover.py` (`_public_level_cap` and call site)
- Modify: `tests/unit/test_problem_discovery_discover.py` (`test_build_problem_two_refs_allow_e2`)
- Test: `tests/unit/test_problem_discovery_admission.py` (add N05 cases)

**Interfaces:**
- Consumes: pending evidence drafts with `author_id` / confidence
- Produces: cap E2 only for ≥2 independent stated/verified author_ids

- [ ] **Step 1: Write failing N05 tests**

```python
from finch.problem_discovery.discover import (
    ProblemDraft,
    ProblemEvidenceDraft,
    build_problem,
)
from finch.problem_discovery.models import EvidenceLevel
from datetime import UTC, datetime


def _now():
    return datetime(2026, 11, 1, tzinfo=UTC)


def test_n05_same_author_id_two_refs_caps_e1():
    draft = ProblemDraft(
        title="t",
        evidence=[
            ProblemEvidenceDraft(
                source_ref="s1",
                quote="alpha",
                evidence_level="e2",
                author_id="alice",
                author_identity_confidence="stated",
            ),
            ProblemEvidenceDraft(
                source_ref="s2",
                quote="beta",
                evidence_level="e2",
                author_id="alice",
                author_identity_confidence="stated",
            ),
        ],
    )
    p = build_problem(
        draft,
        source_texts={"s1": "alpha here", "s2": "beta there"},
        urls={},
        now=_now(),
    )
    assert all(e.evidence_level == EvidenceLevel.E1 for e in p.evidence)


def test_n05_empty_authors_two_refs_caps_e1():
    draft = ProblemDraft(
        title="t",
        evidence=[
            ProblemEvidenceDraft(source_ref="s1", quote="alpha", evidence_level="e2"),
            ProblemEvidenceDraft(source_ref="s2", quote="beta", evidence_level="e2"),
        ],
    )
    p = build_problem(
        draft,
        source_texts={"s1": "alpha here", "s2": "beta there"},
        urls={},
        now=_now(),
    )
    assert all(e.evidence_level == EvidenceLevel.E1 for e in p.evidence)


def test_n05_two_stated_author_ids_allow_e2():
    draft = ProblemDraft(
        title="t",
        evidence=[
            ProblemEvidenceDraft(
                source_ref="s1",
                quote="alpha",
                evidence_level="e2",
                author_id="alice",
                author_identity_confidence="stated",
            ),
            ProblemEvidenceDraft(
                source_ref="s2",
                quote="beta",
                evidence_level="e2",
                author_id="bob",
                author_identity_confidence="stated",
            ),
        ],
    )
    p = build_problem(
        draft,
        source_texts={"s1": "alpha here", "s2": "beta there"},
        urls={},
        now=_now(),
    )
    assert {e.evidence_level for e in p.evidence} == {EvidenceLevel.E2}
```

Update existing `test_build_problem_two_refs_allow_e2` to use distinct `author_id` + `stated` (not bare `author=` alone).

- [ ] **Step 2: Run — expect FAIL on same-author / empty-author cases (still E2)**

Run: `uv run pytest tests/unit/test_problem_discovery_admission.py -k n05 -v`

- [ ] **Step 3: Implement independent-author cap**

Replace `_public_level_cap` usage:

```python
def _independent_author_ids(pending: list[tuple[ProblemEvidenceDraft, str]]) -> set[str]:
    ids: set[str] = set()
    for ev, _ in pending:
        aid = ev.author_id.strip()
        conf = (ev.author_identity_confidence or "unknown").strip().lower()
        if aid and conf in {"stated", "verified"}:
            ids.add(aid)
    return ids


def _public_level_cap(
    *,
    is_marketing: bool,
    independent_authors: int,
) -> EvidenceLevel:
    if is_marketing:
        return EvidenceLevel.E0
    if independent_authors >= 2:
        return EvidenceLevel.E2
    return EvidenceLevel.E1
```

In `build_problem`, stop using `distinct_refs` / raw `author` for E2. Compute `independent_authors=len(_independent_author_ids(pending))` once per card and pass into cap. Still copy `author_id` / confidence onto `ProblemEvidence`.

- [ ] **Step 4: Run discover + admission N05 tests — PASS**

Run: `uv run pytest tests/unit/test_problem_discovery_discover.py tests/unit/test_problem_discovery_admission.py -k "n05 or two_refs or marketing or caps" -v`

- [ ] **Step 5: Commit**

```bash
git add src/finch/problem_discovery/discover.py \
  tests/unit/test_problem_discovery_discover.py \
  tests/unit/test_problem_discovery_admission.py
git commit -m "fix(problem-discovery): require independent authors for E2 cap"
```

---

### Task 4: Wire admit into `validate_upsert` and `discover_problems`

**Files:**
- Modify: `src/finch/problem_discovery/shadow.py`
- Modify: `src/finch/problem_discovery/discover.py` (`discover_problems` loop)
- Test: `tests/unit/test_problem_discovery_admission.py`, `tests/unit/test_problem_discovery_shadow_experiment.py` if assertions need admission

**Interfaces:**
- Consumes: `admit_problem`, `build_problem`
- Produces: upserted problems always have admission set; accepted protection helper

- [ ] **Step 1: Write failing integration-style unit test**

```python
from finch.problem_discovery.shadow import validate_upsert
from finch.problem_discovery.repository import (
    ProblemRepository,
    DiscussionRepository,
    DiscoveryRunRepository,
)
from finch.problem_discovery.importing import import_discussion
from finch.sources.store import ArtifactRepository
from finch.storage.workspace import Workspace
from finch.problem_discovery.models import Admission


def test_validate_upsert_quarantines_invented_quote(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    import_discussion(
        ws,
        url="https://example.com/x",
        text="Real body about session restore failure.",
        title="seed",
        author="u",
    )
    # resolve discussion id from repo
    disc = DiscussionRepository(ws).list_all()[0]
    drafts = {
        "problems": [
            {
                "title": "Invented",
                "evidence": [
                    {
                        "source_ref": disc.discussion_id,
                        "quote": "zzz-not-in-body-zzzz",
                        "claim": "fake",
                    }
                ],
            }
        ]
    }
    out = validate_upsert(
        drafts,
        artifact_repo=ArtifactRepository(ws),
        discussion_repo=DiscussionRepository(ws),
        problem_repo=ProblemRepository(ws, shadow=True),
        run_repo=DiscoveryRunRepository(ws, shadow=True),
    )
    assert out.kept_count == 1
    assert out.problems[0].admission == Admission.QUARANTINED
    assert out.problems[0].evidence == []
```

Add second test: good quote → `VERIFIED`.

Add accepted-protection test: existing accepted card; upsert without force keeps `accepted` (implement `force: bool = False` on `validate_upsert`).

- [ ] **Step 2: Run — expect FAIL (still verified/default or no admit call)**

- [ ] **Step 3: Wire admit**

In `validate_upsert`, after `build_problem`:

```python
problem = admit_problem(problem)
existing = problem_repo.get(problem.problem_id)
if (
    existing is not None
    and existing.admission == Admission.ACCEPTED
    and not force
    and problem.admission != Admission.ACCEPTED
):
    problem.admission = Admission.ACCEPTED
    problem.admission_reason = existing.admission_reason or "human_accepted"
problem_repo.upsert(problem)
```

Add `force: bool = False` parameter to `validate_upsert`.

In `discover_problems`, after `build_problem`:

```python
problem = admit_problem(problem)
problem_repo.upsert(problem)
```

- [ ] **Step 4: Run shadow + admission tests — PASS**

Run: `uv run pytest tests/unit/test_problem_discovery_admission.py tests/unit/test_problem_discovery_shadow_experiment.py tests/unit/test_problem_discovery_discover.py -v`

- [ ] **Step 5: Commit**

```bash
git add src/finch/problem_discovery/shadow.py src/finch/problem_discovery/discover.py \
  tests/unit/test_problem_discovery_admission.py
git commit -m "feat(problem-discovery): admit on validate-upsert and discover"
```

---

### Task 5: Default list/summary filter + `admit` CLI

**Files:**
- Modify: `src/finch/problem_discovery/repository.py` (add helper)
- Modify: `src/finch/problem_discovery/summary.py` (filter before counting)
- Modify: `src/finch/cli.py`
- Test: `tests/unit/test_problem_discovery_admission.py`

**Interfaces:**
- Produces: `filter_by_admission(problems, admission: str) -> list[Problem]` where `admission` is `verified,accepted` (default), `all`, or comma list
- CLI: `list --admission`, `summary --admission`, `admit <id> --accepted [--force]`

- [ ] **Step 1: Write failing filter tests**

```python
from finch.problem_discovery.models import Admission, Problem
from finch.problem_discovery.repository import filter_by_admission


def test_filter_default_hides_quarantined():
    items = [
        Problem(problem_id="1", title="a", admission=Admission.VERIFIED),
        Problem(problem_id="2", title="b", admission=Admission.QUARANTINED),
        Problem(problem_id="3", title="c", admission=Admission.ACCEPTED),
    ]
    out = filter_by_admission(items, "verified,accepted")
    assert [p.problem_id for p in out] == ["1", "3"]


def test_filter_all_returns_everything():
    items = [
        Problem(problem_id="1", title="a", admission=Admission.QUARANTINED),
    ]
    assert len(filter_by_admission(items, "all")) == 1
```

- [ ] **Step 2: Run — FAIL**

- [ ] **Step 3: Implement helper + CLI**

In `repository.py`:

```python
def filter_by_admission(
    problems: list[Problem], admission: str = "verified,accepted"
) -> list[Problem]:
    key = admission.strip().lower()
    if key in {"", "all", "*"}:
        return list(problems)
    allowed = {part.strip() for part in key.split(",") if part.strip()}
    return [p for p in problems if p.admission.value in allowed]
```

Update `problem_discovery_list` / `summary` to accept:

```python
admission: str = typer.Option(
    "verified,accepted",
    "--admission",
    help="Filter by admission (default verified,accepted; use all for quarantine)",
)
```

Apply `filter_by_admission` after `list_all()`.

For `summary.py` `build_summary`: add optional `admission: str = "verified,accepted"` and filter problems before metrics.

Add CLI command:

```python
@problem_discovery_app.command("admit")
def problem_discovery_admit(
    problem_id: str,
    accepted: bool = typer.Option(False, "--accepted"),
    force: bool = typer.Option(False, "--force"),
    shadow: bool = typer.Option(False, "--shadow"),
) -> None:
    ...
```

Load problem, set `admission=accepted`, `admission_reason=human_accepted`, upsert (if already accepted, no-op; if verified→accepted always allowed; quarantined→accepted allowed; downgrades need force — for this command only upgrading to accepted).

- [ ] **Step 4: Run unit tests + a quick CLI smoke**

Run: `uv run pytest tests/unit/test_problem_discovery_admission.py tests/unit/test_problem_discovery_summary.py -v`  
Smoke (tmp workspace if needed): ensure `list --help` shows `--admission`.

- [ ] **Step 5: Commit**

```bash
git add src/finch/problem_discovery/repository.py src/finch/problem_discovery/summary.py \
  src/finch/cli.py tests/unit/test_problem_discovery_admission.py
git commit -m "feat(problem-discovery): default inbox filters by admission"
```

---

### Task 6: Full regression + local write-policy report note

**Files:**
- Modify: none required if green; optional docstring in `skills/problem-discovery-shadow/SKILL.md` one-liner that validate-upsert now sets admission (only if you want Skill awareness — keep ≤5 lines; skip if YAGNI)

- [ ] **Step 1: Run full PD unit suite**

Run: `uv run pytest tests/unit/test_problem_discovery_*.py -v`  
Expected: all PASS

- [ ] **Step 2: Run ruff on touched paths**

Run: `uv run ruff check src/finch/problem_discovery src/finch/cli.py tests/unit/test_problem_discovery_admission.py`

- [ ] **Step 3: Write local report path (optional, under gitignored evals)**

If `evals/` exists locally, write `evals/skill-first-pd/runs/phase-1/write-policy-report.json` summarizing N05/admit pytest results. Do not force-commit (evals gitignored).

- [ ] **Step 4: Final commit only if docs/skill touch**

Otherwise skip; suite green is the gate.

---

## Spec coverage checklist

| Spec requirement | Task |
|------------------|------|
| `admission` parallel to `status` | Task 1 |
| Legacy YAML infer admission | Task 1 |
| `admit_problem` structure gate | Task 2 |
| N05 independent authors for E2 | Task 3 |
| validate-upsert + discover wire | Task 4 |
| Accepted protection on upsert | Task 4 |
| Default list/summary filter | Task 5 |
| `admit --accepted` CLI | Task 5 |
| No Product–Problem | (omitted intentionally) |
| No Trace / Phase 2–5 | (omitted intentionally) |

## Placeholder / consistency self-review

- No TBD steps; signatures use `Admission`, `admit_problem`, `filter_by_admission`, `force` on `validate_upsert`.
- Existing `test_build_problem_two_refs_allow_e2` updated in Task 3 to match new E2 rules.
- Claim graph / semantic support check explicitly out of scope.
