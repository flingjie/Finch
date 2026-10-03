# Expression Methods Library Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist `ArticleReport` by default, let users save selected transferable methods into a small `expression_methods` library (with merge suggestions), and record a three-way verdict when finishing an `expression-practice` session that drills one method.

**Architecture:** `article` owns report persistence + `show`. New package `expression_methods` owns method CRUD, LLM merge candidates, and practice logs. `practice` only gains optional `method_id` / verdict fields; CLI wires method-card context into diagnose and appends logs after finish. No VoiceProfile / practice-profile writes; no auto-recommend.

**Tech Stack:** Python 3.12+, Pydantic 2, typer, Workspace YAML, `StructuredInferenceRunner`, pytest, ruff, mypy. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-03-expression-methods-library-design.md`

## Global Constraints

- Python 3.12+; ruff `line-length 100`, selects `E,F,I,B,UP`; `uv run mypy src` must stay clean.
- LLM never outputs `total` / numeric scores; merge suggest returns only `{method_id, reason}` candidates.
- Method library stays separate from `practice-profile.yaml` and `voice-profile.yaml` (no shared write paths).
- Bare `methods save` without `--merge` / `--as-new` must not persist; exit code `2` means “pending confirmation”.
- Style stays inside `ArticleReport`; do not auto-update VoiceProfile.
- Do not inject methods into `drafts` / `connect` default pipelines.
- Subprocess discipline unchanged (existing runner); args as arrays already handled by runner.
- Before every commit: `uv run pytest` (touched tests), `uv run ruff check .`, `uv run mypy src`.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `src/finch/article/repository.py` | `ArticleReportRepository` Workspace CRUD | Create |
| `src/finch/article/service.py` | unchanged analyze logic | No logic change |
| `src/finch/article/__init__.py` | docstring: reports may persist | Modify |
| `src/finch/expression_methods/__init__.py` | package marker | Create |
| `src/finch/expression_methods/models.py` | `ExpressionMethod` + nested | Create |
| `src/finch/expression_methods/repository.py` | Workspace collection | Create |
| `src/finch/expression_methods/service.py` | save / suggest / merge / log | Create |
| `src/finch/practice/models.py` | `method_id` / verdict fields | Modify |
| `src/finch/practice/service.py` | start/finish method args | Modify |
| `src/finch/cli.py` | article persist/show; `methods_*`; practice flags | Modify |
| `tests/unit/test_article_report_repository.py` | report upsert / get | Create |
| `tests/unit/test_cli_article.py` | `--no-save`, persist, show, id in render | Modify |
| `tests/unit/test_expression_methods_models.py` | model round-trip | Create |
| `tests/unit/test_expression_methods_service.py` | save/merge/suggest/log | Create |
| `tests/unit/test_cli_methods.py` | methods CLI | Create |
| `tests/unit/test_practice_service.py` | method + verdict | Modify |
| `tests/unit/test_cli_practice.py` | `--method` / `--verdict` | Modify |
| `skills/article_analysis/SKILL.md` | save-method handoff | Modify |
| `skills/article_analysis/references/output-contract.md` | persist note | Modify |
| `skills/expression-practice/SKILL.md` | `--method` / verdict | Modify |
| `docs/product-contract.md` | article persist + methods library | Modify |
| `CLAUDE.md` / `AGENTS.md` | CLI surface | Modify |
| Spec status line | → 已实现计划 | Modify (last) |

---

### Task 1: Persist `ArticleReport` + `article show`

**Files:**
- Create: `src/finch/article/repository.py`
- Modify: `src/finch/article/__init__.py`
- Modify: `src/finch/cli.py` (`article_analyze`, `_render_article_report`, add `article_show`)
- Create: `tests/unit/test_article_report_repository.py`
- Modify: `tests/unit/test_cli_article.py`

**Interfaces:**
- Produces: `ArticleReportRepository.upsert(report) -> None`, `.get(report_id) -> ArticleReport | None`, `.list_all() -> list[ArticleReport]`
- Collection dir name: `"article_reports"`
- Consumes: existing `ArticleReport`, `Workspace`

- [ ] **Step 1: Write failing repository tests**

Create `tests/unit/test_article_report_repository.py`:

```python
"""ArticleReport Workspace persistence."""

from finch.article.models import (
    ArticleReport,
    AudienceChange,
    Effectiveness,
    ExpressionTask,
    TransferableMethod,
)
from finch.article.repository import ArticleReportRepository
from finch.storage.workspace import Workspace


def _report(**kw) -> ArticleReport:
    base = dict(
        id="article_abc",
        source_type="text",
        source_ref=None,
        content_hash="hash1",
        expression_task=ExpressionTask(topic="t", primary_task="解释"),
        audience_change=AudienceChange(
            who="dev", before="a", after="b", fit_check="ok"
        ),
        effectiveness=Effectiveness(
            clarity="c", concreteness="c", credibility="c", actionability="n/a"
        ),
        transferable_methods=[
            TransferableMethod(
                method="m1",
                why_effective_here="w",
                when_to_use="u",
                mini_exercise="e",
            ),
            TransferableMethod(
                method="m2",
                why_effective_here="w",
                when_to_use="u",
                mini_exercise="e",
            ),
        ],
    )
    base.update(kw)
    return ArticleReport(**base)


def test_upsert_and_get(tmp_path):
    repo = ArticleReportRepository(Workspace(tmp_path))
    repo.upsert(_report())
    got = repo.get("article_abc")
    assert got is not None
    assert got.content_hash == "hash1"
    assert len(got.transferable_methods) == 2


def test_upsert_overwrites_same_id(tmp_path):
    repo = ArticleReportRepository(Workspace(tmp_path))
    repo.upsert(_report(content_hash="h1"))
    repo.upsert(_report(content_hash="h2"))
    assert repo.get("article_abc").content_hash == "h2"
```

- [ ] **Step 2: Run tests — expect fail**

Run: `uv run pytest tests/unit/test_article_report_repository.py -v`  
Expected: FAIL with `ModuleNotFoundError` or import error for `finch.article.repository`

- [ ] **Step 3: Implement repository**

Create `src/finch/article/repository.py`:

```python
"""ArticleReport 仓库（文件工作区，原子写）。"""

from __future__ import annotations

from pathlib import Path

from finch.article.models import ArticleReport
from finch.storage.workspace import Workspace


class ArticleReportRepository:
    def __init__(self, workspace: Workspace) -> None:
        self.ws = workspace
        self._dir = workspace.dir("article_reports")

    def upsert(self, report: ArticleReport) -> None:
        path = Path(self._dir) / f"{Workspace.safe_filename(report.id)}.yaml"
        self.ws.write_yaml(path, report)

    def get(self, report_id: str) -> ArticleReport | None:
        path = Path(self._dir) / f"{Workspace.safe_filename(report_id)}.yaml"
        return self.ws.read_yaml(path, ArticleReport)

    def list_all(self) -> list[ArticleReport]:
        out: list[ArticleReport] = []
        for path in sorted(Path(self._dir).glob("*.yaml")):
            row = self.ws.read_yaml(path, ArticleReport)
            if row is not None:
                out.append(row)
        return out
```

Update `src/finch/article/__init__.py` docstring to:  
`"""article_analysis：文章表达分析（训练 Skill；报告默认落库）。"""`

- [ ] **Step 4: Run repository tests — expect pass**

Run: `uv run pytest tests/unit/test_article_report_repository.py -v`  
Expected: PASS

- [ ] **Step 5: Extend CLI tests for persist / `--no-save` / show / render id**

Append to `tests/unit/test_cli_article.py`:

```python
def test_article_analyze_persists_by_default(monkeypatch, tmp_path):
    _patch(monkeypatch, _settings(tmp_path))
    monkeypatch.setattr(cli, "ArticleAnalysisService", lambda runner: _FakeService())
    r = CliRunner().invoke(app, ["article", "analyze", "--text", "hello"])
    assert r.exit_code == 0, r.output
    assert "id: article_x" in r.output or "report id" in r.output.casefold() or "article_x" in r.output
    from finch.article.repository import ArticleReportRepository
    from finch.storage.workspace import Workspace

    got = ArticleReportRepository(Workspace(tmp_path)).get("article_x")
    assert got is not None


def test_article_analyze_no_save(monkeypatch, tmp_path):
    _patch(monkeypatch, _settings(tmp_path))
    monkeypatch.setattr(cli, "ArticleAnalysisService", lambda runner: _FakeService())
    r = CliRunner().invoke(
        app, ["article", "analyze", "--text", "hello", "--no-save"]
    )
    assert r.exit_code == 0, r.output
    from finch.article.repository import ArticleReportRepository
    from finch.storage.workspace import Workspace

    assert ArticleReportRepository(Workspace(tmp_path)).get("article_x") is None


def test_article_show(monkeypatch, tmp_path):
    _patch(monkeypatch, _settings(tmp_path))
    monkeypatch.setattr(cli, "ArticleAnalysisService", lambda runner: _FakeService())
    CliRunner().invoke(app, ["article", "analyze", "--text", "hello"])
    r = CliRunner().invoke(app, ["article", "show", "article_x"])
    assert r.exit_code == 0, r.output
    assert "可借鉴方法" in r.output


def test_article_show_missing(monkeypatch, tmp_path):
    _patch(monkeypatch, _settings(tmp_path))
    r = CliRunner().invoke(app, ["article", "show", "missing"])
    assert r.exit_code == 1
```

Also update `test_article_analyze_text_human` (or rely on new test) to assert numbered methods like `1.` / `[1]` appear — match whatever render format you implement in Step 6.

- [ ] **Step 6: Wire CLI persist + show + render**

In `src/finch/cli.py`:

1. Import `ArticleReportRepository`.
2. Add to `article_analyze`:
   - `no_save: bool = typer.Option(False, "--no-save", help="不落库报告")`
   - After successful analyze, if not `no_save`: `ArticleReportRepository(ws).upsert(report)`
   - Update docstring: remove「不落库」.
3. Change `_render_article_report` header / methods section to include id and 1-based indices:

```python
    lines = [
        "# 文章表达分析",
        "",
        f"id: {report.id}",
        "",
        f"## 表达任务{inferred}",
        # ... unchanged until 可借鉴方法 ...
    ]
    # In 可借鉴方法 loop, enumerate from 1:
    for i, m in enumerate(report.transferable_methods, start=1):
        lines += [
            f"- **[{i}] {m.method}**",
            f"  为何有效：{m.why_effective_here}",
            f"  适用：{m.when_to_use}",
            f"  练习：{m.mini_exercise}",
        ]
    lines += [
        "",
        "下一步：finch methods save --report <id> --index <n>",
    ]
```

4. Add command:

```python
@article_app.command("show")
def article_show(
    report_id: str = typer.Argument(..., help="report id"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """回看已落库的文章分析报告。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    report = ArticleReportRepository(ws).get(report_id)
    if report is None:
        typer.echo(f"report not found: {report_id}")
        raise typer.Exit(code=1)
    if as_json:
        typer.echo(report.model_dump_json(indent=2))
    else:
        typer.echo(_render_article_report(report))
```

- [ ] **Step 7: Run article tests**

Run: `uv run pytest tests/unit/test_article_report_repository.py tests/unit/test_cli_article.py tests/unit/test_article_service.py -v`  
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add src/finch/article/repository.py src/finch/article/__init__.py src/finch/cli.py \
  tests/unit/test_article_report_repository.py tests/unit/test_cli_article.py
git commit -m "$(cat <<'EOF'
feat(article): persist ArticleReport by default and add show

EOF
)"
```

---

### Task 2: `ExpressionMethod` models + repository

**Files:**
- Create: `src/finch/expression_methods/__init__.py`
- Create: `src/finch/expression_methods/models.py`
- Create: `src/finch/expression_methods/repository.py`
- Create: `tests/unit/test_expression_methods_models.py`

**Interfaces:**
- Produces: `MethodVerdict = Literal["worth_reuse","practice_again","not_for_me"]`
- Produces: `MethodSource`, `MethodPracticeLog`, `ExpressionMethod`, `MergeCandidate`, `MergeSuggestion`
- Produces: `ExpressionMethodRepository.upsert/get/list_all` on collection `"expression_methods"`

- [ ] **Step 1: Write failing model tests**

Create `tests/unit/test_expression_methods_models.py`:

```python
"""expression_methods 数据模型。"""

from datetime import UTC, datetime

from finch.expression_methods.models import (
    ExpressionMethod,
    MethodPracticeLog,
    MethodSource,
)


def test_expression_method_round_trip():
    now = datetime.now(UTC)
    m = ExpressionMethod(
        id="emethod_1",
        title="用一次具体失败引出问题",
        why_effective="先让读者看到损失",
        when_to_use="经验复盘",
        boundaries="",
        mini_exercise="写一个失败开头",
        sources=[
            MethodSource(
                report_id="article_x",
                method_index=2,
                excerpt="",
                source_ref=None,
            )
        ],
        practice_logs=[
            MethodPracticeLog(
                session_id="practice_1",
                verdict="worth_reuse",
                note="清楚多了",
                at=now,
            )
        ],
        created_at=now,
        updated_at=now,
    )
    data = m.model_dump(mode="json")
    assert ExpressionMethod(**data).title == m.title
    assert data["practice_logs"][0]["verdict"] == "worth_reuse"
```

- [ ] **Step 2: Run — expect fail**

Run: `uv run pytest tests/unit/test_expression_methods_models.py -v`  
Expected: FAIL import

- [ ] **Step 3: Implement models + repository**

`src/finch/expression_methods/__init__.py`:

```python
"""表达方法库：从 ArticleReport 选中可迁移方法，供练习与复用。"""
```

`src/finch/expression_methods/models.py`:

```python
"""表达方法库模型。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

MethodVerdict = Literal["worth_reuse", "practice_again", "not_for_me"]


class MethodSource(BaseModel):
    report_id: str
    method_index: int  # 1-based into ArticleReport.transferable_methods
    excerpt: str = ""
    source_ref: str | None = None


class MethodPracticeLog(BaseModel):
    session_id: str
    verdict: MethodVerdict
    note: str = ""
    at: datetime


class ExpressionMethod(BaseModel):
    id: str
    title: str
    why_effective: str
    when_to_use: str
    boundaries: str = ""
    mini_exercise: str = ""
    sources: list[MethodSource] = Field(default_factory=list)
    practice_logs: list[MethodPracticeLog] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class MergeCandidate(BaseModel):
    method_id: str
    reason: str


class MergeSuggestion(BaseModel):
    candidates: list[MergeCandidate] = Field(default_factory=list, max_length=3)
```

`src/finch/expression_methods/repository.py`:

```python
"""ExpressionMethod 仓库（文件工作区，原子写）。"""

from __future__ import annotations

from pathlib import Path

from finch.expression_methods.models import ExpressionMethod
from finch.storage.workspace import Workspace


class ExpressionMethodRepository:
    def __init__(self, workspace: Workspace) -> None:
        self.ws = workspace
        self._dir = workspace.dir("expression_methods")

    def upsert(self, method: ExpressionMethod) -> None:
        path = Path(self._dir) / f"{Workspace.safe_filename(method.id)}.yaml"
        self.ws.write_yaml(path, method)

    def get(self, method_id: str) -> ExpressionMethod | None:
        path = Path(self._dir) / f"{Workspace.safe_filename(method_id)}.yaml"
        return self.ws.read_yaml(path, ExpressionMethod)

    def list_all(self) -> list[ExpressionMethod]:
        out: list[ExpressionMethod] = []
        for path in sorted(Path(self._dir).glob("*.yaml")):
            row = self.ws.read_yaml(path, ExpressionMethod)
            if row is not None:
                out.append(row)
        return out
```

- [ ] **Step 4: Run model tests — expect pass**

Run: `uv run pytest tests/unit/test_expression_methods_models.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/finch/expression_methods tests/unit/test_expression_methods_models.py
git commit -m "$(cat <<'EOF'
feat(expression_methods): add ExpressionMethod models and repository

EOF
)"
```

---

### Task 3: `ExpressionMethodService` (save / suggest / merge / log)

**Files:**
- Create: `src/finch/expression_methods/service.py`
- Create: `tests/unit/test_expression_methods_service.py`

**Interfaces:**
- Consumes: `ArticleReportRepository`, `ExpressionMethodRepository`, `StructuredInferenceRunner`
- Produces:
  - `from_report(report, index: int) -> ExpressionMethod` (unsaved draft fields; assigns new id + timestamps)
  - `suggest_merges(candidate: ExpressionMethod) -> MergeSuggestion`
  - `save_as_new(report_id: str, index: int) -> ExpressionMethod`
  - `merge_into(target_id: str, report_id: str, index: int) -> ExpressionMethod`
  - `append_practice_log(method_id, session_id, verdict, note="") -> ExpressionMethod`
- Field map from `TransferableMethod`: `method→title`, `why_effective_here→why_effective`, `when_to_use`, `mini_exercise`; `boundaries=""`
- Idempotent source: same `(report_id, method_index)` not duplicated
- Index is **1-based**; out of range → `ValueError`

- [ ] **Step 1: Write failing service tests**

Create `tests/unit/test_expression_methods_service.py`:

```python
"""ExpressionMethodService：from_report / save / merge / log."""

from datetime import UTC, datetime

from finch.article.models import (
    ArticleReport,
    AudienceChange,
    Effectiveness,
    ExpressionTask,
    TransferableMethod,
)
from finch.article.repository import ArticleReportRepository
from finch.expression_methods.models import (
    ExpressionMethod,
    MergeCandidate,
    MergeSuggestion,
    MethodSource,
)
from finch.expression_methods.repository import ExpressionMethodRepository
from finch.expression_methods.service import ExpressionMethodService
from finch.storage.workspace import Workspace


class FakeRunner:
    def __init__(self, suggestion: MergeSuggestion | None = None):
        self.suggestion = suggestion or MergeSuggestion(candidates=[])
        self.prompts: list[str] = []

    def run(self, prompt, output_model, **kw):
        self.prompts.append(prompt)
        if output_model is MergeSuggestion:
            return self.suggestion
        raise AssertionError(output_model)


def _report(tmp_path) -> ArticleReport:
    report = ArticleReport(
        id="article_x",
        source_type="url",
        source_ref="https://example.com/a",
        content_hash="h",
        expression_task=ExpressionTask(topic="t", primary_task="解释"),
        audience_change=AudienceChange(
            who="dev", before="a", after="b", fit_check="ok"
        ),
        effectiveness=Effectiveness(
            clarity="c", concreteness="c", credibility="c", actionability="n/a"
        ),
        transferable_methods=[
            TransferableMethod(
                method="失败开场",
                why_effective_here="先见损失",
                when_to_use="复盘",
                mini_exercise="写失败开头",
            ),
            TransferableMethod(
                method="先结果后机制",
                why_effective_here="降低抽象",
                when_to_use="解释概念",
                mini_exercise="先写后果",
            ),
        ],
    )
    ArticleReportRepository(Workspace(tmp_path)).upsert(report)
    return report


def _svc(tmp_path, runner=None) -> ExpressionMethodService:
    ws = Workspace(tmp_path)
    return ExpressionMethodService(
        ExpressionMethodRepository(ws),
        ArticleReportRepository(ws),
        runner or FakeRunner(),
    )


def test_save_as_new_maps_fields(tmp_path):
    _report(tmp_path)
    m = _svc(tmp_path).save_as_new("article_x", 2)
    assert m.title == "先结果后机制"
    assert m.why_effective == "降低抽象"
    assert m.boundaries == ""
    assert m.sources[0].method_index == 2
    assert m.sources[0].source_ref == "https://example.com/a"
    assert ExpressionMethodRepository(Workspace(tmp_path)).get(m.id) is not None


def test_save_index_out_of_range(tmp_path):
    _report(tmp_path)
    try:
        _svc(tmp_path).save_as_new("article_x", 9)
    except ValueError as e:
        assert "index" in str(e).casefold()
        return
    raise AssertionError("expected ValueError")


def test_merge_appends_source_idempotent(tmp_path):
    _report(tmp_path)
    svc = _svc(tmp_path)
    a = svc.save_as_new("article_x", 1)
    b = svc.merge_into(a.id, "article_x", 2)
    assert len(b.sources) == 2
    b2 = svc.merge_into(a.id, "article_x", 2)
    assert len(b2.sources) == 2


def test_suggest_merges_uses_runner(tmp_path):
    _report(tmp_path)
    svc = _svc(tmp_path)
    existing = svc.save_as_new("article_x", 1)
    runner = FakeRunner(
        MergeSuggestion(
            candidates=[
                MergeCandidate(method_id=existing.id, reason="同为失败开场")
            ]
        )
    )
    svc2 = _svc(tmp_path, runner)
    draft = svc2.from_report(
        ArticleReportRepository(Workspace(tmp_path)).get("article_x"), 1
    )
    # from_report on same index — for suggest use index 2 as "new" candidate
    draft = svc2.from_report(
        ArticleReportRepository(Workspace(tmp_path)).get("article_x"), 2
    )
    sug = svc2.suggest_merges(draft)
    assert sug.candidates[0].method_id == existing.id
    assert runner.prompts


def test_append_practice_log(tmp_path):
    _report(tmp_path)
    svc = _svc(tmp_path)
    m = svc.save_as_new("article_x", 1)
    m = svc.append_practice_log(m.id, "practice_1", "worth_reuse", note="好用")
    assert m.practice_logs[-1].verdict == "worth_reuse"
    assert m.practice_logs[-1].note == "好用"
```

- [ ] **Step 2: Run — expect fail**

Run: `uv run pytest tests/unit/test_expression_methods_service.py -v`  
Expected: FAIL import `ExpressionMethodService`

- [ ] **Step 3: Implement service**

Create `src/finch/expression_methods/service.py`:

```python
"""ExpressionMethodService：从报告选中方法、合并建议、练习反馈。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

from finch.article.models import ArticleReport
from finch.article.repository import ArticleReportRepository
from finch.expression_methods.models import (
    ExpressionMethod,
    MergeSuggestion,
    MethodPracticeLog,
    MethodSource,
    MethodVerdict,
)
from finch.expression_methods.repository import ExpressionMethodRepository
from finch.llm.base import StructuredInferenceRunner

_MERGE_PROMPT = """\
You suggest whether a new transferable writing method should merge into an existing
library entry. Compare by transferable ACTION (not surface labels). Return at most
3 candidates; empty list if none are synonyms.

## New method
title: {title}
why_effective: {why_effective}
when_to_use: {when_to_use}
boundaries: {boundaries}

## Existing methods
{existing}

Respond with JSON matching the schema: candidates (list of {{method_id, reason}}).
"""


class ExpressionMethodService:
    def __init__(
        self,
        methods: ExpressionMethodRepository,
        reports: ArticleReportRepository,
        runner: StructuredInferenceRunner,
    ) -> None:
        self.methods = methods
        self.reports = reports
        self.runner = runner

    def from_report(self, report: ArticleReport, index: int) -> ExpressionMethod:
        methods = report.transferable_methods
        if index < 1 or index > len(methods):
            raise ValueError(
                f"index out of range: {index} (report has {len(methods)} methods)"
            )
        tm = methods[index - 1]
        now = datetime.now(UTC)
        return ExpressionMethod(
            id=f"emethod_{uuid4().hex[:8]}",
            title=tm.method,
            why_effective=tm.why_effective_here,
            when_to_use=tm.when_to_use,
            boundaries="",
            mini_exercise=tm.mini_exercise,
            sources=[
                MethodSource(
                    report_id=report.id,
                    method_index=index,
                    excerpt="",
                    source_ref=report.source_ref,
                )
            ],
            created_at=now,
            updated_at=now,
        )

    def suggest_merges(self, candidate: ExpressionMethod) -> MergeSuggestion:
        existing = self.methods.list_all()
        if not existing:
            return MergeSuggestion(candidates=[])
        lines = []
        for m in existing:
            lines.append(
                f"- id={m.id} | title={m.title} | when_to_use={m.when_to_use} | "
                f"boundaries={m.boundaries}"
            )
        return cast(
            MergeSuggestion,
            self.runner.run(
                _MERGE_PROMPT.format(
                    title=candidate.title,
                    why_effective=candidate.why_effective,
                    when_to_use=candidate.when_to_use,
                    boundaries=candidate.boundaries,
                    existing="\n".join(lines),
                ),
                MergeSuggestion,
            ),
        )

    def save_as_new(self, report_id: str, index: int) -> ExpressionMethod:
        report = self._require_report(report_id)
        method = self.from_report(report, index)
        self.methods.upsert(method)
        return method

    def merge_into(
        self, target_id: str, report_id: str, index: int
    ) -> ExpressionMethod:
        target = self.methods.get(target_id)
        if target is None:
            raise KeyError(target_id)
        report = self._require_report(report_id)
        draft = self.from_report(report, index)
        src = draft.sources[0]
        if any(
            s.report_id == src.report_id and s.method_index == src.method_index
            for s in target.sources
        ):
            return target
        target = target.model_copy(
            update={
                "sources": [*target.sources, src],
                "updated_at": datetime.now(UTC),
            }
        )
        self.methods.upsert(target)
        return target

    def append_practice_log(
        self,
        method_id: str,
        session_id: str,
        verdict: MethodVerdict,
        note: str = "",
    ) -> ExpressionMethod:
        method = self.methods.get(method_id)
        if method is None:
            raise KeyError(method_id)
        log = MethodPracticeLog(
            session_id=session_id,
            verdict=verdict,
            note=note,
            at=datetime.now(UTC),
        )
        method = method.model_copy(
            update={
                "practice_logs": [*method.practice_logs, log],
                "updated_at": datetime.now(UTC),
            }
        )
        self.methods.upsert(method)
        return method

    def _require_report(self, report_id: str) -> ArticleReport:
        report = self.reports.get(report_id)
        if report is None:
            raise KeyError(report_id)
        return report
```

- [ ] **Step 4: Run service tests — expect pass**

Run: `uv run pytest tests/unit/test_expression_methods_service.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/finch/expression_methods/service.py tests/unit/test_expression_methods_service.py
git commit -m "$(cat <<'EOF'
feat(expression_methods): save, merge suggest, and practice log service

EOF
)"
```

---

### Task 4: `finch methods` CLI

**Files:**
- Modify: `src/finch/cli.py` (register `methods_app`; commands `save` / `list` / `show`)
- Create: `tests/unit/test_cli_methods.py`

**Interfaces:**
- `methods save --report <id> --index <n>` → suggest only → exit `2` (no write)
- `methods save --report <id> --index <n> --as-new` → write new
- `methods save --report <id> --index <n> --merge <method-id>` → merge
- `--as-new` and `--merge` mutually exclusive; both absent → suggest path
- Missing report → exit `1` with `report not found`
- `list` / `show` for browsing

- [ ] **Step 1: Write failing CLI tests**

Create `tests/unit/test_cli_methods.py`:

```python
"""CLI tests for finch methods."""

from typer.testing import CliRunner

from finch import cli
from finch.article.models import (
    ArticleReport,
    AudienceChange,
    Effectiveness,
    ExpressionTask,
    TransferableMethod,
)
from finch.article.repository import ArticleReportRepository
from finch.cli import app
from finch.expression_methods.models import MergeSuggestion
from finch.settings import Paths, Settings
from finch.storage.workspace import Workspace


def _settings(tmp_path):
    return Settings(paths=Paths(var_dir=tmp_path))


def _patch(monkeypatch, settings):
    monkeypatch.setattr(cli, "load_settings", lambda: settings)


def _seed_report(tmp_path) -> None:
    ArticleReportRepository(Workspace(tmp_path)).upsert(
        ArticleReport(
            id="article_x",
            source_type="text",
            content_hash="h",
            expression_task=ExpressionTask(topic="t", primary_task="解释"),
            audience_change=AudienceChange(
                who="d", before="a", after="b", fit_check="ok"
            ),
            effectiveness=Effectiveness(
                clarity="c",
                concreteness="c",
                credibility="c",
                actionability="n/a",
            ),
            transferable_methods=[
                TransferableMethod(
                    method="m1",
                    why_effective_here="w",
                    when_to_use="u",
                    mini_exercise="e",
                ),
                TransferableMethod(
                    method="m2",
                    why_effective_here="w",
                    when_to_use="u",
                    mini_exercise="e",
                ),
            ],
        )
    )


class _FakeRunner:
    def run(self, prompt, output_model, **kw):
        if output_model is MergeSuggestion:
            return MergeSuggestion(candidates=[])
        raise AssertionError(output_model)


def test_methods_save_suggest_only_no_write(monkeypatch, tmp_path):
    _patch(monkeypatch, _settings(tmp_path))
    _seed_report(tmp_path)
    monkeypatch.setattr(cli, "create_runner", lambda *a, **k: _FakeRunner())
    monkeypatch.setattr(cli, "CodexRunner", _FakeRunner)
    r = CliRunner().invoke(
        app, ["methods", "save", "--report", "article_x", "--index", "1"]
    )
    assert r.exit_code == 2, r.output
    from finch.expression_methods.repository import ExpressionMethodRepository

    assert ExpressionMethodRepository(Workspace(tmp_path)).list_all() == []


def test_methods_save_as_new(monkeypatch, tmp_path):
    _patch(monkeypatch, _settings(tmp_path))
    _seed_report(tmp_path)
    monkeypatch.setattr(cli, "create_runner", lambda *a, **k: _FakeRunner())
    monkeypatch.setattr(cli, "CodexRunner", _FakeRunner)
    r = CliRunner().invoke(
        app,
        ["methods", "save", "--report", "article_x", "--index", "2", "--as-new"],
    )
    assert r.exit_code == 0, r.output
    from finch.expression_methods.repository import ExpressionMethodRepository

    methods = ExpressionMethodRepository(Workspace(tmp_path)).list_all()
    assert len(methods) == 1
    assert methods[0].title == "m2"


def test_methods_save_missing_report(monkeypatch, tmp_path):
    _patch(monkeypatch, _settings(tmp_path))
    monkeypatch.setattr(cli, "create_runner", lambda *a, **k: _FakeRunner())
    monkeypatch.setattr(cli, "CodexRunner", _FakeRunner)
    r = CliRunner().invoke(
        app, ["methods", "save", "--report", "missing", "--index", "1", "--as-new"]
    )
    assert r.exit_code == 1
    assert "report not found" in r.output
```

- [ ] **Step 2: Run — expect fail**

Run: `uv run pytest tests/unit/test_cli_methods.py -v`  
Expected: FAIL (unknown command `methods` or similar)

- [ ] **Step 3: Implement CLI**

Near other typer apps in `src/finch/cli.py`:

```python
methods_app = typer.Typer(help="表达方法库（从文章分析选中可迁移方法）")
app.add_typer(methods_app, name="methods")
```

Add imports for `ExpressionMethodService`, `ExpressionMethodRepository`, `MergeSuggestion` as needed.

Implement commands (place near article commands):

```python
def _methods_service(ws, settings):
    runner = cast(CodexRunner, create_runner(settings.llm, "critique") or CodexRunner())
    return ExpressionMethodService(
        ExpressionMethodRepository(ws),
        ArticleReportRepository(ws),
        runner,
    )


@methods_app.command("save")
def methods_save(
    report: str = typer.Option(..., "--report", help="ArticleReport id"),
    index: int = typer.Option(..., "--index", help="1-based transferable_methods index"),
    as_new: bool = typer.Option(False, "--as-new", help="强制新建"),
    merge: str | None = typer.Option(None, "--merge", help="合并到已有 method id"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """保存选中方法：默认只打印合并候选（exit 2）；--as-new / --merge 才写入。"""
    if as_new and merge:
        typer.echo("use only one of --as-new / --merge")
        raise typer.Exit(code=1)
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    svc = _methods_service(ws, settings)
    try:
        if as_new:
            method = svc.save_as_new(report, index)
        elif merge:
            method = svc.merge_into(merge, report, index)
        else:
            report_obj = ArticleReportRepository(ws).get(report)
            if report_obj is None:
                typer.echo(f"report not found: {report}")
                raise typer.Exit(code=1)
            draft = svc.from_report(report_obj, index)
            suggestion = svc.suggest_merges(draft)
            if as_json:
                typer.echo(suggestion.model_dump_json(indent=2))
            else:
                typer.echo(f"pending method: {draft.title}")
                if not suggestion.candidates:
                    typer.echo("no merge candidates")
                for c in suggestion.candidates:
                    typer.echo(f"- {c.method_id}: {c.reason}")
                typer.echo(
                    "确认：finch methods save --report "
                    f"{report} --index {index} --as-new"
                    "  或  --merge <method-id>"
                )
            raise typer.Exit(code=2)
    except KeyError as exc:
        key = str(exc).strip("'")
        if key == report or "report" in str(exc).casefold():
            typer.echo(f"report not found: {report}")
        else:
            typer.echo(f"method not found: {key}")
        raise typer.Exit(code=1) from None
    except ValueError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc
    if as_json:
        typer.echo(method.model_dump_json(indent=2))
    else:
        typer.echo(f"id: {method.id}")
        typer.echo(f"title: {method.title}")
        typer.echo(f"sources: {len(method.sources)}")


@methods_app.command("list")
def methods_list(
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """列出表达方法库。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    rows = ExpressionMethodRepository(ws).list_all()
    if as_json:
        import json

        typer.echo(
            json.dumps([r.model_dump(mode="json") for r in rows], ensure_ascii=False, indent=2)
        )
        return
    if not rows:
        typer.echo("(empty)")
        return
    for m in rows:
        typer.echo(
            f"{m.id}\t{m.title}\tsources={len(m.sources)}\tlogs={len(m.practice_logs)}"
        )


@methods_app.command("show")
def methods_show(
    method_id: str = typer.Argument(..., help="method id"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """展示一个表达方法（来源 + 练习记录）。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    m = ExpressionMethodRepository(ws).get(method_id)
    if m is None:
        typer.echo(f"method not found: {method_id}")
        raise typer.Exit(code=1)
    if as_json:
        typer.echo(m.model_dump_json(indent=2))
        return
    typer.echo(f"id: {m.id}")
    typer.echo(f"title: {m.title}")
    typer.echo(f"why_effective: {m.why_effective}")
    typer.echo(f"when_to_use: {m.when_to_use}")
    typer.echo(f"boundaries: {m.boundaries}")
    typer.echo(f"mini_exercise: {m.mini_exercise}")
    for s in m.sources:
        typer.echo(
            f"source: report={s.report_id} index={s.method_index} ref={s.source_ref}"
        )
    for log in m.practice_logs:
        typer.echo(
            f"practice: session={log.session_id} verdict={log.verdict} note={log.note}"
        )
```

Fix KeyError handling so `save_as_new` missing report prints `report not found: …` (service raises `KeyError(report_id)`).

- [ ] **Step 4: Run CLI tests**

Run: `uv run pytest tests/unit/test_cli_methods.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/finch/cli.py tests/unit/test_cli_methods.py
git commit -m "$(cat <<'EOF'
feat(cli): add finch methods save/list/show

EOF
)"
```

---

### Task 5: Wire `expression-practice` to methods

**Files:**
- Modify: `src/finch/practice/models.py`
- Modify: `src/finch/practice/service.py`
- Modify: `src/finch/cli.py` (`practice_start`, `practice_diagnose`, `practice_finish`, `practice_show`)
- Modify: `tests/unit/test_practice_service.py`
- Modify: `tests/unit/test_cli_practice.py`

**Interfaces:**
- `PracticeSession.method_id: str | None = None`
- `PracticeSession.method_verdict: MethodVerdict | None = None` — import Literal twin or reuse string literals in practice models to avoid hard dependency cycle; duplicate the Literal in `practice.models` is OK
- `PracticeSession.method_verdict_note: str = ""`
- `PracticeService.start(..., method_id: str | None = None)`
- `PracticeService.finish(..., method_verdict=None, method_verdict_note="")`  
  - if `session.method_id` and `method_verdict is None` → `ValueError("method_verdict required")`
- CLI `practice start --method`: validate method exists before start
- CLI `practice diagnose`: if session has `method_id`, prepend method card to `context` (unless already provided — still prepend)
- CLI `practice finish --verdict/--note`: after successful finish, call `ExpressionMethodService.append_practice_log`

Method card context string format:

```text
## Expression method drill
id: {id}
title: {title}
why_effective: {why_effective}
when_to_use: {when_to_use}
boundaries: {boundaries}
mini_exercise: {mini_exercise}
Do NOT score whether the technique was used; diagnose clarity of purpose for the reader.
```

- [ ] **Step 1: Write failing practice service tests**

Append to `tests/unit/test_practice_service.py`:

```python
def test_start_with_method_id(tmp_path):
    svc = _service(tmp_path)
    s = svc.start(initial_attempt="初稿", method_id="emethod_1")
    assert s.method_id == "emethod_1"


def test_finish_requires_verdict_when_method(tmp_path):
    svc = _service(tmp_path)
    s = svc.start(initial_attempt="初稿", method_id="emethod_1")
    try:
        svc.finish(s.id, "最终版")
    except ValueError as e:
        assert "verdict" in str(e).casefold()
        return
    raise AssertionError("expected ValueError")


def test_finish_with_verdict(tmp_path):
    svc = _service(tmp_path)
    s = svc.start(initial_attempt="初稿", method_id="emethod_1")
    s = svc.finish(
        s.id, "最终版", method_verdict="worth_reuse", method_verdict_note="再用"
    )
    assert s.status == "finished"
    assert s.method_verdict == "worth_reuse"
    assert s.method_verdict_note == "再用"
```

- [ ] **Step 2: Run — expect fail**

Run: `uv run pytest tests/unit/test_practice_service.py -v`  
Expected: FAIL (unexpected kwarg / missing fields)

- [ ] **Step 3: Extend models + service**

In `src/finch/practice/models.py` add:

```python
method_id: str | None = None
method_verdict: Literal["worth_reuse", "practice_again", "not_for_me"] | None = None
method_verdict_note: str = ""
```

In `PracticeService.start` accept `method_id: str | None = None` and pass into `PracticeSession(...)`.

In `PracticeService.finish`:

```python
def finish(
    self,
    session_id: str,
    final_expression: str,
    *,
    method_verdict: Literal["worth_reuse", "practice_again", "not_for_me"] | None = None,
    method_verdict_note: str = "",
) -> PracticeSession:
    session = self._require_started(session_id)
    if session.method_id and method_verdict is None:
        raise ValueError("method_verdict required when session has method_id")
    # ... existing lesson LLM ...
    session = session.model_copy(
        update={
            "final_expression": final_expression,
            "lesson": lesson.lesson,
            "status": "finished",
            "method_verdict": method_verdict,
            "method_verdict_note": method_verdict_note,
            "updated_at": datetime.now(UTC),
        }
    )
    self.sessions.upsert(session)
    return session
```

- [ ] **Step 4: Run practice service tests — expect pass**

Run: `uv run pytest tests/unit/test_practice_service.py -v`  
Expected: PASS

- [ ] **Step 5: CLI practice tests**

In `tests/unit/test_cli_practice.py`, add tests that:

1. Seed an `ExpressionMethod` in workspace.
2. `practice start --method <id> --attempt "..."` → session JSON includes `method_id`.
3. Missing method id → exit 1.
4. `finish` without `--verdict` on method session → exit 1.
5. `finish --verdict worth_reuse` → method `practice_logs` has one entry.

Follow existing monkeypatch patterns in that file for settings / runner.

- [ ] **Step 6: Wire practice CLI**

`practice_start`: add `--method`; if set, `ExpressionMethodRepository(ws).get` or fail; pass `method_id` to `start`.

`practice_diagnose`: load session first (or after diagnose). Prefer: before diagnose, if session has method_id, build card and set  
`context = card + ("\n\n" + context if context else "")`.

`practice_finish`: add  
`--verdict` optional, `--note` optional; pass into `finish`; on success if `session.method_id` and verdict:  
`ExpressionMethodService(...).append_practice_log(...)`.

`practice_show`: print `method_id` / `method_verdict` when present.

- [ ] **Step 7: Run practice + methods tests**

Run: `uv run pytest tests/unit/test_practice_service.py tests/unit/test_cli_practice.py tests/unit/test_expression_methods_service.py tests/unit/test_cli_methods.py -v`  
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add src/finch/practice/models.py src/finch/practice/service.py src/finch/cli.py \
  tests/unit/test_practice_service.py tests/unit/test_cli_practice.py
git commit -m "$(cat <<'EOF'
feat(practice): drill expression methods and record reuse verdict

EOF
)"
```

---

### Task 6: Skills + product docs

**Files:**
- Modify: `skills/article_analysis/SKILL.md`
- Modify: `skills/article_analysis/references/output-contract.md`
- Modify: `skills/expression-practice/SKILL.md`
- Modify: `docs/product-contract.md` (Skill 边界段)
- Modify: `CLAUDE.md` (CLI surface)
- Modify: `AGENTS.md` (命令段 if present)

- [ ] **Step 1: Update article_analysis Skill**

In `SKILL.md`, note:

- 报告默认落库（`--no-save` 可跳过）；呈现含 `id` 与方法序号 `[1]…`。
- 用户要保存方法时：`finch methods save --report <id> --index <n>`，再按提示 `--as-new` 或 `--merge`。
- 仍不自动写 VoiceProfile；风格观察留在报告内。

In `output-contract.md`, replace「即算即打印，不写 Workspace」with：  
默认写入 `article_reports`；`--no-save` 跳过。方法库是独立 `expression_methods` 集合，仅用户选中后写入。

- [ ] **Step 2: Update expression-practice Skill**

Add flow branch:

1. （可选）`finch methods list` 选方法。  
2. `finch practice start --method <id> --attempt "..."`（可同时 `--idea`）。  
3. diagnose / save 不变。  
4. `finch practice finish --final "..." --verdict worth_reuse|practice_again|not_for_me [--note "..."]`。  
5. 反馈优先「是否值得再用」，不检查「有没有用上技巧」。

- [ ] **Step 3: Update product-contract + CLAUDE + AGENTS**

`docs/product-contract.md` Skill 边界：将  
`article_analysis` 只读…  
改为说明：默认落库报告；选中方法进入表达方法库；分析他人风格仍不得自动写入 VoiceProfile；方法库 ≠ PracticeProfile。

`CLAUDE.md` CLI surface: add `finch article show`, `finch methods …`, practice `--method` / `--verdict`.

`AGENTS.md`: same CLI mentions if the connect/practice bullet list is the right place — add one short bullet under 命令.

- [ ] **Step 4: Lint + focused tests**

Run:

```bash
uv run ruff check .
uv run mypy src
uv run pytest tests/unit/test_article_report_repository.py tests/unit/test_cli_article.py \
  tests/unit/test_expression_methods_models.py tests/unit/test_expression_methods_service.py \
  tests/unit/test_cli_methods.py tests/unit/test_practice_service.py tests/unit/test_cli_practice.py -v
```

Expected: all PASS / clean

- [ ] **Step 5: Commit**

```bash
git add skills/article_analysis skills/expression-practice docs/product-contract.md \
  CLAUDE.md AGENTS.md
git commit -m "$(cat <<'EOF'
docs: document expression methods library and article report persistence

EOF
)"
```

---

### Task 7: Spec status + plan self-check

**Files:**
- Modify: `docs/superpowers/specs/2026-10-03-expression-methods-library-design.md` status line

- [ ] **Step 1: Update spec status**

Change header status to:  
`状态：已确认（实现计划见 docs/superpowers/plans/2026-10-03-expression-methods-library.md）`

- [ ] **Step 2: Manual smoke (optional if LLM available)**

```bash
uv run finch article analyze --text "先说一次部署失败：服务重复执行把库存扣成了负数。下面解释幂等为什么重要。"
# note report id
uv run finch methods save --report <id> --index 1
# expect exit 2
uv run finch methods save --report <id> --index 1 --as-new
uv run finch practice start --method <emethod_id> --attempt "我们上线时重复回调把库存扣两次……"
uv run finch practice diagnose <session_id>
uv run finch practice finish <session_id> --final "……" --verdict worth_reuse --note "值得再用"
uv run finch methods show <emethod_id>
```

Confirm `voice-profile.yaml` / `practice-profile.yaml` untouched.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-10-03-expression-methods-library-design.md
git commit -m "$(cat <<'EOF'
docs: point expression methods design at implementation plan

EOF
)"
```

---

## Spec coverage checklist (plan author)

| Spec requirement | Task |
|---|---|
| Default persist ArticleReport + `--no-save` | Task 1 |
| `article show` + id / method indices in render | Task 1 |
| `ExpressionMethod` separate from practice-profile | Task 2 |
| Field map + empty boundaries | Task 3 |
| Merge suggest + `--as-new` / `--merge`; bare save no write exit 2 | Task 3–4 |
| Idempotent `(report_id, method_index)` | Task 3 |
| Practice method context; existing diagnose | Task 5 |
| Verdict three-way + practice_logs | Task 5 |
| Skills / product contract / no Voice auto-write | Task 6 |
| No auto-recommend / vector / mastery | Out of scope (Global Constraints) |
