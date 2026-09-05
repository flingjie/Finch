# Daily Work Budget — Phase A (P0) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `finch daily` ingest all new commits incrementally (per-SHA), but deep-process only a bounded budget per run; excess goes to a durable backlog instead of being dropped.

**Architecture:** A pre-graph `Ingestor` discovers new commits via a per-SHA ledger, fetches details only for a bounded candidate set, groups, and deterministically scores/sorts to select ≤`max_change_groups` groups for extraction. The graph stays pure; the extract node marks the ledger `extracted`/`failed`. A pure `select_planning_evidence` trims cards before `plan_topics`.

**Tech Stack:** Python 3.12, Pydantic 2, SQLModel (SQLite, `payload_json` pattern), typer, `gh` CLI, `uv`.

## Global Constraints

- Python 3.12+; lint `uv run ruff check .` (E,F,I,B,UP; line-length 100), types `uv run mypy src`, tests `uv run pytest`.
- Deterministic totals invariant: `priority`/`score` are computed in code; LLM output never carries a `total`.
- Subprocess discipline: args as arrays, per-call timeouts, JSON output validated via Pydantic.
- SQLModel records store `payload_json` and upsert via `session.merge` (idempotent).
- Graph runtime stays sequential/deterministic; bounded `ThreadPoolExecutor` only inside nodes via `pool.map`.
- Bilingual (Chinese/English) docstrings; match surrounding files.

---

### Task 1: Add `daily_budget` config

**Files:**
- Modify: `src/finch/settings.py`
- Test: `tests/unit/test_settings.py`

**Interfaces:**
- Produces: `DailyBudget` model (fields: `max_detail_fetches: int = 40`, `max_change_groups: int = 12`, `max_planning_events: int = 12`, `max_evidence_cards_for_planning: int = 36`, `max_estimated_prompt_bytes: int = 40000`, `age_bonus_max_days: int = 7`, `max_extract_retries: int = 3`, `sort_weights: DailyBudgetWeights`). `DailyBudgetWeights` (fields: `core_source=0.25`, `churn=0.20`, `keyword=0.15`, `cross_module=0.10`, `novelty=0.15`, `age_bonus=0.15`). `Settings.daily_budget: DailyBudget`.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_settings.py`:

```python
from finch.settings import DailyBudget, DailyBudgetWeights


def test_daily_budget_defaults():
    s = load_settings(Path("finch.example.yaml"))
    b = s.daily_budget
    assert b.max_detail_fetches == 40
    assert b.max_change_groups == 12
    assert b.max_planning_events == 12
    assert b.max_evidence_cards_for_planning == 36
    assert b.max_estimated_prompt_bytes == 40000
    assert b.age_bonus_max_days == 7
    assert b.max_extract_retries == 3
    w = b.sort_weights
    assert isinstance(w, DailyBudgetWeights)
    assert (w.core_source, w.churn, w.keyword) == (0.25, 0.20, 0.15)
    assert (w.cross_module, w.novelty, w.age_bonus) == (0.10, 0.15, 0.15)


def test_daily_budget_rejects_nonpositive():
    with pytest.raises(ValidationError):
        DailyBudget(max_change_groups=0)
    with pytest.raises(ValidationError):
        DailyBudget(max_extract_retries=0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_settings.py::test_daily_budget_defaults -v`
Expected: FAIL — `ImportError: cannot import name 'DailyBudget'`

- [ ] **Step 3: Write minimal implementation**

In `src/finch/settings.py`, add after `ExtractionSettings`:

```python
class DailyBudgetWeights(BaseModel):
    """select_groups 的确定性排序权重（priority + age，和为 1）。"""

    core_source: float = 0.25
    churn: float = 0.20
    keyword: float = 0.15
    cross_module: float = 0.10
    novelty: float = 0.15
    age_bonus: float = 0.15


class DailyBudget(BaseModel):
    """每日深度处理预算（阶段 A：有界工作量）。"""

    max_detail_fetches: int = Field(default=40, ge=1)
    max_change_groups: int = Field(default=12, ge=1)
    max_planning_events: int = Field(default=12, ge=1)
    max_evidence_cards_for_planning: int = Field(default=36, ge=1)
    max_estimated_prompt_bytes: int = Field(default=40000, ge=1)
    age_bonus_max_days: int = Field(default=7, ge=1)
    max_extract_retries: int = Field(default=3, ge=1)
    sort_weights: DailyBudgetWeights = Field(default_factory=DailyBudgetWeights)
```

Add the field to `Settings`:

```python
class Settings(BaseModel):
    ...
    extraction: ExtractionSettings = Field(default_factory=ExtractionSettings)
    daily_budget: DailyBudget = Field(default_factory=DailyBudget)
```

Also add `daily_budget` to `finch.yaml` under `extraction:` (or leave it — defaults are used when absent). For explicitness, append to `finch.yaml`:

```yaml
daily_budget:
  max_detail_fetches: 40
  max_change_groups: 12
  max_planning_events: 12
  max_evidence_cards_for_planning: 36
  max_estimated_prompt_bytes: 40000
  age_bonus_max_days: 7
  max_extract_retries: 3
  sort_weights:
    core_source: 0.25
    churn: 0.20
    keyword: 0.15
    cross_module: 0.10
    novelty: 0.15
    age_bonus: 0.15
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_settings.py -v`
Expected: PASS (all settings tests, including the two new ones).

- [ ] **Step 5: Commit**

```bash
git add src/finch/settings.py finch.yaml tests/unit/test_settings.py
git commit -m "feat(settings): add daily_budget config for bounded daily work"
```

---

### Task 2: Ledger + cursor records and repositories

**Files:**
- Modify: `src/finch/storage/repositories.py`
- Test: `tests/unit/test_ingestion_repository.py` (new)

**Interfaces:**
- Produces:
  - `CommitIngestionRecord` (SQLModel table; composite PK `(repository, sha)`; columns `status`, `group_id: str | None`, `discovered_at: datetime`, `authored_at: datetime`, `retry_count: int = 0`, `payload_json`, `updated_at`).
  - `RepoCursorRecord` (table; PK `repository`; columns `last_synced_sha: str | None`, `last_synced_at`).
  - `CommitIngestionRepository(store)` with: `known_shas(repo) -> set[str]`, `upsert_pending(repo, summaries: list[CommitSummary])`, `store_detail(repo, detail: CommitDetail)`, `mark_skipped(repo, sha)`, `mark_extracted(repo, shas: list[str])`, `mark_failed(repo, shas: list[str], max_retries: int)`, `list_pending(repo) -> list[CommitIngestionRecord]`, `list_grouped(repo) -> list[CommitIngestionRecord]` (includes `failed`).
  - `RepoCursorRepository(store)` with: `get_sha(repo) -> str | None`, `advance(repo, sha: str | None)`.
- Consumes: `Store`, `CommitSummary`/`CommitDetail` from `finch.github.models`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_ingestion_repository.py`:

```python
from datetime import UTC, datetime, timedelta

from finch.github.models import CommitDetail, CommitFile, CommitSummary
from finch.storage.database import Store
from finch.storage.repositories import (
    CommitIngestionRecord,
    CommitIngestionRepository,
    RepoCursorRepository,
)

REPO = "flingjie/FDE-Gym"


def _summary(sha, message="feat: x", when=None):
    return CommitSummary(
        sha=sha, message=message,
        author_date=when or datetime(2026, 9, 1, tzinfo=UTC),
        html_url="u", parents=[],
    )


def _detail(sha):
    return CommitDetail(
        sha=sha, message="feat: x", author_date=datetime(2026, 9, 1, tzinfo=UTC),
        html_url="u", parents=[],
        files=[CommitFile(filename="src/a.py", status="modified", additions=5, deletions=1)],
        stats={},
    )


def test_upsert_pending_and_known_shas(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    repo = CommitIngestionRepository(store)
    repo.upsert_pending(REPO, [_summary("a" * 40), _summary("b" * 40)])
    assert repo.known_shas(REPO) == {"a" * 40, "b" * 40}
    # 幂等：同 sha 再次 upsert 不产生重复行
    repo.upsert_pending(REPO, [_summary("a" * 40)])
    assert repo.known_shas(REPO) == {"a" * 40, "b" * 40}


def test_store_detail_upgrades_to_grouped(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    repo = CommitIngestionRepository(store)
    repo.upsert_pending(REPO, [_summary("a" * 40)])
    repo.store_detail(REPO, _detail("a" * 40))
    grouped = repo.list_grouped(REPO)
    assert [r.sha for r in grouped] == ["a" * 40]
    assert grouped[0].status == "grouped"
    parsed = CommitDetail.model_validate_json(grouped[0].payload_json)
    assert [f.filename for f in parsed.files] == ["src/a.py"]


def test_mark_extracted_and_skipped(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    repo = CommitIngestionRepository(store)
    repo.upsert_pending(REPO, [_summary("a" * 40), _summary("b" * 40)])
    repo.mark_extracted(REPO, ["a" * 40])
    repo.mark_skipped(REPO, "b" * 40)
    assert repo.list_grouped(REPO) == []
    assert repo.list_pending(REPO) == []


def test_mark_failed_retries_then_skips(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    repo = CommitIngestionRepository(store)
    repo.upsert_pending(REPO, [_summary("a" * 40)])
    repo.store_detail(REPO, _detail("a" * 40))
    for _ in range(2):
        repo.mark_failed(REPO, ["a" * 40], max_retries=3)
    assert repo.list_grouped(REPO)[0].status == "failed"
    repo.mark_failed(REPO, ["a" * 40], max_retries=3)  # 第 3 次 → skipped
    assert repo.list_grouped(REPO) == []


def test_cursor_roundtrip(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    repo = RepoCursorRepository(store)
    assert repo.get_sha(REPO) is None
    repo.advance(REPO, "a" * 40)
    assert repo.get_sha(REPO) == "a" * 40
    repo.advance(REPO, "b" * 40)
    assert repo.get_sha(REPO) == "b" * 40
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_ingestion_repository.py -v`
Expected: FAIL — `ImportError: cannot import name 'CommitIngestionRepository'`

- [ ] **Step 3: Write minimal implementation**

In `src/finch/storage/repositories.py`, add after the `EvidenceCardRecord`/`EvidenceRepository` block (before `DraftRecord`):

```python
class CommitIngestionRecord(SQLModel, table=True):
    """commit 摄取 ledger（阶段 A）：per-SHA 增量状态。"""

    repository: str = Field(primary_key=True)
    sha: str = Field(primary_key=True)
    status: str
    group_id: str | None = None
    discovered_at: datetime
    authored_at: datetime
    retry_count: int = 0
    payload_json: str
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RepoCursorRecord(SQLModel, table=True):
    """每仓库 SHA 游标（非权威优化：HEAD 等于 last_synced_sha 时跳过 list_commits）。"""

    repository: str = Field(primary_key=True)
    last_synced_sha: str | None = None
    last_synced_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CommitIngestionRepository:
    """commit 摄取 ledger 仓储：pending → grouped → extracted / skipped / failed。"""

    PENDING = "pending"
    GROUPED = "grouped"
    EXTRACTED = "extracted"
    SKIPPED = "skipped"
    FAILED = "failed"

    def __init__(self, store: Store) -> None:
        self.store = store

    def known_shas(self, repository: str) -> set[str]:
        with Session(self.store.engine) as session:
            stmt = select(CommitIngestionRecord.sha).where(
                CommitIngestionRecord.repository == repository
            )
            return set(session.exec(stmt))

    def upsert_pending(self, repository: str, summaries: list) -> None:
        if not summaries:
            return
        now = datetime.now(UTC)
        with Session(self.store.engine) as session:
            for s in summaries:
                session.merge(
                    CommitIngestionRecord(
                        repository=repository,
                        sha=s.sha,
                        status=self.PENDING,
                        discovered_at=now,
                        authored_at=s.author_date,
                        retry_count=0,
                        payload_json=s.model_dump_json(),
                        updated_at=now,
                    )
                )
            session.commit()

    def store_detail(self, repository: str, detail) -> None:
        with Session(self.store.engine) as session:
            record = session.get(CommitIngestionRecord, (repository, detail.sha))
            if record is None:
                raise KeyError((repository, detail.sha))
            record.status = self.GROUPED
            record.authored_at = detail.author_date
            record.payload_json = detail.model_dump_json()
            record.updated_at = datetime.now(UTC)
            session.add(record)
            session.commit()

    def mark_skipped(self, repository: str, sha: str) -> None:
        self._set_statuses(repository, [sha], self.SKIPPED)

    def mark_extracted(self, repository: str, shas: list[str]) -> None:
        self._set_statuses(repository, shas, self.EXTRACTED)

    def mark_failed(self, repository: str, shas: list[str], max_retries: int) -> None:
        with Session(self.store.engine) as session:
            for sha in shas:
                record = session.get(CommitIngestionRecord, (repository, sha))
                if record is None:
                    continue
                record.retry_count += 1
                record.status = (
                    self.SKIPPED if record.retry_count >= max_retries else self.FAILED
                )
                record.updated_at = datetime.now(UTC)
                session.add(record)
            session.commit()

    def list_pending(self, repository: str) -> list[CommitIngestionRecord]:
        return self._list_by_status(repository, [self.PENDING])

    def list_grouped(self, repository: str) -> list[CommitIngestionRecord]:
        """返回可提取的 commit（含 failed，供下一轮重试）。"""
        return self._list_by_status(repository, [self.GROUPED, self.FAILED])

    def _list_by_status(
        self, repository: str, statuses: list[str]
    ) -> list[CommitIngestionRecord]:
        with Session(self.store.engine) as session:
            stmt = select(CommitIngestionRecord).where(
                CommitIngestionRecord.repository == repository,
                CommitIngestionRecord.status.in_(statuses),
            )
            return list(session.exec(stmt))

    def _set_statuses(self, repository: str, shas: list[str], status: str) -> None:
        if not shas:
            return
        with Session(self.store.engine) as session:
            for sha in shas:
                record = session.get(CommitIngestionRecord, (repository, sha))
                if record is None:
                    continue
                record.status = status
                record.updated_at = datetime.now(UTC)
                session.add(record)
            session.commit()


class RepoCursorRepository:
    """每仓库 SHA 游标仓储（非权威，仅用于零新 commit 快速返回）。"""

    def __init__(self, store: Store) -> None:
        self.store = store

    def get_sha(self, repository: str) -> str | None:
        with Session(self.store.engine) as session:
            record = session.get(RepoCursorRecord, repository)
            return record.last_synced_sha if record is not None else None

    def advance(self, repository: str, sha: str | None) -> None:
        if sha is None:
            return
        with Session(self.store.engine) as session:
            session.merge(
                RepoCursorRecord(
                    repository=repository,
                    last_synced_sha=sha,
                    last_synced_at=datetime.now(UTC),
                )
            )
            session.commit()
```

Add the needed imports at the top of `repositories.py` (add `CommitDetail`/`CommitSummary` if not already imported):

```python
from finch.github.models import CommitDetail, CommitSummary
```

(Note: `datetime`, `UTC`, `Field`, `Session`, `select`, `SQLModel` are already imported in `repositories.py`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_ingestion_repository.py -v`
Expected: PASS (all 6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/finch/storage/repositories.py tests/unit/test_ingestion_repository.py
git commit -m "feat(storage): add commit ingestion ledger and per-repo cursor repositories"
```

---

### Task 3: Deterministic budget scoring (`budget.py`)

**Files:**
- Create: `src/finch/evidence/budget.py`
- Test: `tests/unit/test_budget.py` (new)

**Interfaces:**
- Produces:
  - Signal helpers: `core_source_ratio(files) -> float`, `churn_score(files) -> float`, `keyword_score(message) -> float`, `cross_module_score(files) -> float`, `novelty_score(tokens: set[str], existing_topics: set[str]) -> float`, `age_bonus(discovered_at: datetime, now: datetime, max_days: int) -> float`.
  - `rank_pending(items: list[tuple[CommitSummary, datetime]], existing_topics: set[str], settings: DailyBudget, now: datetime) -> list[CommitSummary]`
  - `select_groups(groups: list[list[CommitDetail]], existing_topics: set[str], settings: DailyBudget, discovered_at: dict[str, datetime], now: datetime) -> list[list[CommitDetail]]`
- Consumes: `CommitSummary`/`CommitDetail` (`finch.github.models`), `DailyBudget` (`finch.settings`), `_render_commits` (`finch.evidence.extractor`).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_budget.py`:

```python
from datetime import UTC, datetime, timedelta

from finch.evidence.budget import (
    age_bonus,
    churn_score,
    core_source_ratio,
    cross_module_score,
    keyword_score,
    novelty_score,
    rank_pending,
    select_groups,
)
from finch.github.models import CommitDetail, CommitFile, CommitSummary
from finch.settings import DailyBudget

NOW = datetime(2026, 9, 5, tzinfo=UTC)


def _files(*paths):
    return [CommitFile(filename=p, status="modified", additions=10, deletions=2) for p in paths]


def _detail(sha, files, message="feat: node-ize orchestrator"):
    return CommitDetail(
        sha=sha, message=message, author_date=datetime(2026, 9, 1, tzinfo=UTC),
        html_url="u", parents=[], files=files, stats={},
    )


def test_core_source_ratio():
    assert core_source_ratio(_files("src/a.py", "docs/readme.md")) == 0.5
    assert core_source_ratio(_files("tests/test_a.py")) == 0.0
    assert core_source_ratio(_files("package-lock.json")) == 0.0
    assert core_source_ratio([]) == 0.0


def test_churn_score_monotonic():
    small = churn_score(_files("a.py"))
    big = churn_score([CommitFile(filename="a.py", status="modified", additions=500, deletions=500)])
    assert 0.0 < small < big <= 1.0


def test_keyword_score():
    assert keyword_score("fix: handle retry") == 1.0
    assert keyword_score("docs: update") == 0.0


def test_cross_module_score():
    assert cross_module_score(_files("src/a.py", "src/b.py", "tests/t.py")) == 1.0
    assert cross_module_score(_files("src/a.py")) == 1.0 / 3


def test_novelty_score():
    assert novelty_score({"agent", "harness"}, {"agent reliability"}) < 1.0
    assert novelty_score({"agent", "harness"}, set()) == 1.0
    assert novelty_score({"unrelated"}, {"agent reliability"}) == 1.0


def test_age_bonus():
    old = NOW - timedelta(days=7)
    assert age_bonus(old, NOW, 7) == 1.0
    assert age_bonus(NOW, NOW, 7) == 0.0
    assert age_bonus(NOW - timedelta(days=3), NOW, 7) == 3.0 / 7


def test_rank_pending_sorts_by_signal():
    s1 = CommitSummary(sha="a" * 40, message="fix: handle retry",
                       author_date=datetime(2026, 9, 1, tzinfo=UTC), html_url="u", parents=[])
    s2 = CommitSummary(sha="b" * 40, message="docs: tweak readme",
                       author_date=datetime(2026, 9, 1, tzinfo=UTC), html_url="u", parents=[])
    out = rank_pending([(s2, NOW), (s1, NOW)], set(), DailyBudget(), NOW)
    assert out[0].sha == "a" * 40  # fix 关键词优先于 docs


def test_select_groups_respects_group_cap():
    groups = [[_detail("a" * 40, _files("src/a.py"))] for _ in range(20)]
    budget = DailyBudget(max_change_groups=3)
    selected = select_groups(groups, set(), budget, {}, NOW)
    assert len(selected) == 3


def test_select_groups_prefers_source_over_docs():
    docs = [_detail("d" * 40, _files("docs/x.md"), message="docs: x")]
    src = [_detail("s" * 40, _files("src/a.py"), message="fix: y")]
    selected = select_groups([docs, src], set(), DailyBudget(max_change_groups=1), {}, NOW)
    assert selected == [src]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_budget.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'finch.evidence.budget'`

- [ ] **Step 3: Write minimal implementation**

Create `src/finch/evidence/budget.py`:

```python
"""每日预算选择：确定性排序信号 + rank_pending / select_groups（阶段 A）。"""

import re
from datetime import datetime
from math import log1p

from ..github.models import CommitDetail, CommitSummary
from ..settings import DailyBudget
from .extractor import _render_commits

_STOPWORDS = {
    "with", "from", "into", "this", "that", "they", "were", "have", "will",
    "your", "more", "some", "also", "been", "phase",
}

_KEYWORDS = ("fix", "refactor", "perf", "performance", "architect", "feat", "optimiz")

_LOCKFILE_MARKERS = ("package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
                     "Cargo.lock", "Gemfile.lock", "go.sum", "uv.lock")
_DEP_MARKERS = (*_LOCKFILE_MARKERS, "requirements", "package.json", "pyproject.toml",
                "go.mod", "cargo.toml", "composer.json", "gemfile")
_DOC_SUFFIXES = (".md", ".rst", ".txt", ".adoc")
_TEST_MARKERS = ("test", "spec", "__tests__")

_CROSS_MODULE_FULL = 3
_CHURN_LOG_NORM = log1p(1000)


def _significant_words(msg: str) -> set[str]:
    body = msg.partition(":")[2] if ":" in msg else msg
    words = re.findall(r"[a-z][a-z0-9-]{3,}", body.lower())
    return {w for w in words if w not in _STOPWORDS}


def _path_tokens(files: list) -> set[str]:
    tokens: set[str] = set()
    for f in files:
        for part in f.filename.lower().split("/"):
            if part:
                tokens.add(part)
    return tokens


def _is_doc(path: str) -> bool:
    lower = path.lower()
    return lower.endswith(_DOC_SUFFIXES) or lower.startswith("docs/") or "readme" in lower


def _is_test(path: str) -> bool:
    return any(m in path.lower() for m in _TEST_MARKERS)


def _is_dep(path: str) -> bool:
    return any(m in path.lower() for m in _DEP_MARKERS)


def core_source_ratio(files: list) -> float:
    """改核心源码（非 doc/test/dep）的文件占比。"""
    if not files:
        return 0.0
    src = sum(
        1 for f in files
        if not (_is_doc(f.filename) or _is_test(f.filename) or _is_dep(f.filename))
    )
    return src / len(files)


def churn_score(files: list) -> float:
    """Σ(additions+deletions) 的对数尺度归一。"""
    total = sum(f.additions + f.deletions for f in files)
    return min(log1p(total) / _CHURN_LOG_NORM, 1.0)


def keyword_score(message: str) -> float:
    return 1.0 if any(k in message.lower() for k in _KEYWORDS) else 0.0


def cross_module_score(files: list) -> float:
    """跨模块展开度：distinct 顶层目录数（3+ 记满分）。"""
    top_dirs = {f.filename.split("/", 1)[0] for f in files if "/" in f.filename}
    return min(len(top_dirs) / _CROSS_MODULE_FULL, 1.0)


def novelty_score(tokens: set[str], existing_topics: set[str]) -> float:
    """1 − 与既有 EvidenceCard.topics 的最大 Jaccard；无历史记 1.0。"""
    if not tokens or not existing_topics:
        return 1.0
    best = 0.0
    for topic in existing_topics:
        topic_tokens = set(topic.lower().split())
        inter = len(tokens & topic_tokens)
        if inter:
            best = max(best, inter / len(tokens | topic_tokens))
    return 1.0 - best


def age_bonus(discovered_at: datetime, now: datetime, max_days: int) -> float:
    age_days = (now - discovered_at).total_seconds() / 86400.0
    return min(max(age_days, 0.0) / max_days, 1.0)


def _group_signals(
    group: list[CommitDetail], existing_topics: set[str]
) -> tuple[float, float, float, float, float]:
    files = [f for c in group for f in c.files]
    core = core_source_ratio(files)
    churn = churn_score(files)
    keyword = 1.0 if any(keyword_score(c.message) for c in group) else 0.0
    cross = cross_module_score(files)
    tokens: set[str] = set()
    for c in group:
        tokens |= _significant_words(c.message)
        tokens |= _path_tokens(c.files)
    novelty = novelty_score(tokens, existing_topics)
    return core, churn, keyword, cross, novelty


def _score(core, churn, keyword, cross, novelty, age, weights) -> float:
    return (
        weights.core_source * core
        + weights.churn * churn
        + weights.keyword * keyword
        + weights.cross_module * cross
        + weights.novelty * novelty
        + weights.age_bonus * age
    )


def select_groups(
    groups: list[list[CommitDetail]],
    existing_topics: set[str],
    settings: DailyBudget,
    discovered_at: dict[str, datetime],
    now: datetime,
) -> list[list[CommitDetail]]:
    """确定性选出本轮提取预算内的 group（score 降序，受 group 数与估算字节双约束）。"""
    scored: list[tuple[float, int, list[CommitDetail]]] = []
    for i, group in enumerate(groups):
        core, churn, keyword, cross, novelty = _group_signals(group, existing_topics)
        oldest = min((discovered_at.get(c.sha, now) for c in group), default=now)
        age = age_bonus(oldest, now, settings.age_bonus_max_days)
        s = _score(core, churn, keyword, cross, novelty, age, settings.sort_weights)
        scored.append((s, i, group))
    scored.sort(key=lambda t: (-t[0], t[1]))

    selected: list[list[CommitDetail]] = []
    est_bytes = 0
    for _s, _i, group in scored:
        if len(selected) >= settings.max_change_groups:
            break
        group_bytes = len(_render_commits(group).encode("utf-8"))
        if est_bytes + group_bytes > settings.max_estimated_prompt_bytes:
            continue
        selected.append(group)
        est_bytes += group_bytes
    return selected


def rank_pending(
    items: list[tuple[CommitSummary, datetime]],
    existing_topics: set[str],
    settings: DailyBudget,
    now: datetime,
) -> list[CommitSummary]:
    """段 1 预排序：用 summary 级信号（无文件级）给 pending 排序，供 detail-fetch 选择。"""
    scored: list[tuple[float, int, CommitSummary]] = []
    for i, (summary, discovered) in enumerate(items):
        kw = keyword_score(summary.message)
        novelty = novelty_score(_significant_words(summary.message), existing_topics)
        age = age_bonus(discovered, now, settings.age_bonus_max_days)
        w = settings.sort_weights
        s = w.keyword * kw + w.novelty * novelty + w.age_bonus * age
        scored.append((s, i, summary))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [summary for _s, _i, summary in scored]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_budget.py -v`
Expected: PASS (all 9 tests). If `test_select_groups_prefers_source_over_docs` fails because the `docs`/`src` groups tie on novelty (empty history) and age (both `now`), verify that `core_source_ratio` alone breaks the tie — `src` group has `core_source=1.0`, `docs` has `0.0`, so `src` scores higher. Correct.

- [ ] **Step 5: Commit**

```bash
git add src/finch/evidence/budget.py tests/unit/test_budget.py
git commit -m "feat(evidence): deterministic budget scoring (select_groups, rank_pending)"
```

---

### Task 4: `select_planning_evidence` pure function

**Files:**
- Modify: `src/finch/content/jobs.py`
- Test: `tests/unit/test_jobs.py`

**Interfaces:**
- Produces: `select_planning_evidence(cards: list[EvidenceCard], match_results: list[MatchResult], settings: DailyBudget) -> list[EvidenceCard]`
- Consumes: `EvidenceCard`/`MatchResult` (`finch.evidence.models`), `DailyBudget` (`finch.settings`).

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_jobs.py`:

```python
from finch.content.jobs import select_planning_evidence
from finch.evidence.models import ClaimConfidence, EvidenceCard, MatchResult, JudgeScores
from finch.settings import DailyBudget


def _card(cid, event_id, confidence=ClaimConfidence.VERIFIED, publishable=True):
    return EvidenceCard(
        id=cid, event_id=event_id, claim=f"claim {cid}", sources=[],
        confidence=confidence, publishable=publishable, topics=["agent"],
    )


def test_select_planning_evidence_caps_events_and_cards():
    cards = []
    for e in range(20):
        for label in ("problem", "decision", "result"):
            cards.append(_card(f"ev_{e}_{label}", f"evt_{e}"))
    budget = DailyBudget(max_planning_events=3, max_evidence_cards_for_planning=36)
    out = select_planning_evidence(cards, [], budget)
    # 每 event 3 卡，3 events → 9 卡
    assert len(out) == 9
    assert {c.event_id for c in out} == {"evt_0", "evt_1", "evt_2"}


def test_select_planning_evidence_prefers_matched_and_publishable():
    matched = _card("ev_a_problem", "evt_a")
    unmatched = _card("ev_b_problem", "evt_b")
    mr = MatchResult(candidate_id="c1", card_ids=["ev_a_problem"],
                     scores=JudgeScores(relevance=0.9, evidence_strength=0.9,
                                        incremental_value=0.9, discussability=0.9),
                     score=0.9)
    budget = DailyBudget(max_planning_events=1, max_evidence_cards_for_planning=10)
    out = select_planning_evidence([unmatched, matched], [mr], budget)
    assert out[0].event_id == "evt_a"


def test_select_planning_evidence_empty():
    assert select_planning_evidence([], [], DailyBudget()) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_jobs.py::test_select_planning_evidence_caps_events_and_cards -v`
Expected: FAIL — `ImportError: cannot import name 'select_planning_evidence'`

- [ ] **Step 3: Write minimal implementation**

Add the import at the top of `src/finch/content/jobs.py`:

```python
from finch.settings import DailyBudget
```

Add the function after `select_primary_job`:

```python
def select_planning_evidence(
    cards: list[EvidenceCard],
    match_results: list[MatchResult],
    settings: DailyBudget,
) -> list[EvidenceCard]:
    """在 plan_topics 前裁剪证据卡：按 event 聚合、确定性排序、选 Top N event。

    完整证据卡仍入库，本函数只限制本次内容规划的输入（裁剪集 ⊆ 完整集）。
    """
    if not cards:
        return []

    cards_by_event: dict[str, list[EvidenceCard]] = {}
    for card in cards:
        cards_by_event.setdefault(card.event_id, []).append(card)

    matched_card_ids = {cid for mr in match_results for cid in mr.card_ids}
    _CONF_ORDER = {
        "VERIFIED": 4, "SUPPORTED": 3, "USER_CONFIRMED": 2, "INFERRED": 1, "UNKNOWN": 0,
    }

    def event_key(group: list[EvidenceCard]) -> tuple[int, int, int]:
        publishable = 1 if all(c.publishable for c in group) else 0
        matched = 1 if any(c.id in matched_card_ids for c in group) else 0
        best_conf = max(_CONF_ORDER[c.confidence.value] for c in group)
        return (publishable, matched, best_conf)

    ranked = sorted(cards_by_event.items(), key=lambda kv: event_key(kv[1]), reverse=True)

    selected: list[EvidenceCard] = []
    for _event_id, group in ranked[: settings.max_planning_events]:
        for card in group[:3]:  # 每 event 保留 ≤3 卡
            if len(selected) >= settings.max_evidence_cards_for_planning:
                return selected
            selected.append(card)
    return selected
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_jobs.py -v`
Expected: PASS (existing jobs tests + 3 new ones).

- [ ] **Step 5: Commit**

```bash
git add src/finch/content/jobs.py tests/unit/test_jobs.py
git commit -m "feat(content): select_planning_evidence to trim cards before plan_topics"
```

---

### Task 5: `Extractor.extract_grouped` refactor

**Files:**
- Modify: `src/finch/evidence/extractor.py`
- Test: `tests/unit/test_extractor.py`

**Interfaces:**
- Produces: `Extractor.extract_grouped(groups: list[list[CommitDetail]], repo: str) -> list[EngineeringEvent]` (extracts pre-grouped groups; cache key remains content fingerprint in Phase A). `Extractor.extract(commits, repo)` becomes a thin wrapper: `return self.extract_grouped(list(group_commits(commits)), repo)`.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_extractor.py`:

```python
from finch.evidence.extractor import Extractor
from finch.github.models import CommitDetail, CommitFile


def _detail(sha, msg, filename="src/a.py"):
    return CommitDetail(
        sha=sha, message=msg, author_date="2026-09-01T00:00:00Z",
        html_url="u", parents=[],
        files=[CommitFile(filename=filename, status="modified", additions=1, deletions=0)],
        stats={},
    )


class _FakeRunner:
    def __init__(self, events_by_index):
        self.events_by_index = events_by_index
        self.calls = 0

    def run(self, prompt, model, timeout=None):
        from finch.evidence.models import Claim, ClaimConfidence, EngineeringEvent
        self.calls += 1
        items = []
        for i, event in self.events_by_index.items():
            items.append({"group_id": f"g_{i}", "event": event.model_dump(mode="json")})
        from finch.evidence.extractor import BatchExtractionOutput
        return BatchExtractionOutput(items=items)


def test_extract_grouped_accepts_pregrouped(tmp_path):
    event = EngineeringEvent(
        id="evt", repository="r", commits=["a" * 40],
        problem=Claim(statement="p", confidence=ClaimConfidence.SUPPORTED),
        decision=Claim(statement="d", confidence=ClaimConfidence.INFERRED),
        result=Claim(statement="r", confidence=ClaimConfidence.SUPPORTED),
    )
    runner = _FakeRunner({0: event})
    extractor = Extractor(runner, cache_path=tmp_path / "cache.json")
    groups = [[_detail("a" * 40, "feat: x")]]
    out = extractor.extract_grouped(groups, "r")
    assert [e.id for e in out] == ["evt"]
    assert runner.calls == 1
```

(You must first import `EngineeringEvent` and `Claim`/`ClaimConfidence` in this test file; if they are not already imported, add:

```python
from finch.evidence.models import Claim, ClaimConfidence, EngineeringEvent
```

)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_extractor.py::test_extract_grouped_accepts_pregrouped -v`
Expected: FAIL — `AttributeError: 'Extractor' object has no attribute 'extract_grouped'`

- [ ] **Step 3: Write minimal implementation**

In `src/finch/evidence/extractor.py`, replace the `extract` method body. Find:

```python
    def extract(self, commits: list[CommitDetail], repo: str) -> list[EngineeringEvent]:
        """批量提取所有 commit 组的事件，缺失组最多局部补偿一次。
        ...
        """
        groups = list(group_commits(commits))
        if not groups:
            return []
        template = _BATCH_PROMPT_PATH.read_text()
        ...
```

Replace with:

```python
    def extract(self, commits: list[CommitDetail], repo: str) -> list[EngineeringEvent]:
        """薄封装：先分组再委托 extract_grouped（保住 ``github reflect`` CLI）。"""
        return self.extract_grouped(list(group_commits(commits)), repo)

    def extract_grouped(
        self, groups: list[list[CommitDetail]], repo: str
    ) -> list[EngineeringEvent]:
        """对预分组 group 批量提取事件，缺失组最多局部补偿一次（阶段 A：缓存键为内容指纹）。

        事件顺序 = 输入 group 顺序。
        """
        if not groups:
            return []
        template = _BATCH_PROMPT_PATH.read_text()

        # 指纹缓存查找：命中直接复用，未命中进入提取列表。
        events: dict[int, EngineeringEvent] = {}
        miss_indices: list[int] = []
        for i, group in enumerate(groups):
            cached = (
                self.cache.get(group_fingerprint(repo, group, _CACHE_VERSION))
                if self.cache is not None
                else None
            )
            if cached is not None:
                events[i] = cached
            else:
                miss_indices.append(i)

        if miss_indices:
            miss_groups = [groups[i] for i in miss_indices]
            fresh = self._extract_groups(miss_groups, repo, template)
            for local_i, global_i in enumerate(miss_indices):
                events[global_i] = fresh[local_i]
                if self.cache is not None:
                    self.cache.put(
                        group_fingerprint(repo, miss_groups[local_i], _CACHE_VERSION),
                        fresh[local_i],
                    )
            if self.cache is not None:
                self.cache.save()

        return [events[i] for i in range(len(groups))]
```

The rest of the method body (cache lookup + `_extract_groups` call) is unchanged; ensure the `groups = list(group_commits(commits))` and the `if not groups: return []` lines moved into `extract`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_extractor.py -v`
Expected: PASS (existing extractor tests + the new `extract_grouped` test).

- [ ] **Step 5: Commit**

```bash
git add src/finch/evidence/extractor.py tests/unit/test_extractor.py
git commit -m "feat(evidence): expose extract_grouped for pre-grouped ingestion"
```

---

### Task 6: `GhClient.list_commits_newest_first` + `Ingestor`

**Files:**
- Modify: `src/finch/github/gh_client.py`
- Create: `src/finch/github/ingestion.py`
- Test: `tests/unit/test_ingestion.py` (new)

**Interfaces:**
- Produces:
  - `GhClient.list_commits_newest_first(repo, *, per_page=100, max_commits=200) -> list[CommitSummary]` (page-bounded, newest-first).
  - `Ingestor(gh, settings, ingestion, cursor)` with `.ingest(repos: list[str], existing_topics: set[str]) -> dict[str, list[list[CommitDetail]]]`.
- Consumes: `CommitIngestionRepository`/`RepoCursorRepository` (Task 2), `rank_pending`/`select_groups` (Task 3), `group_commits`/`is_noise`/`find_local_clone`/`LocalRepoClient`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_ingestion.py`:

```python
from datetime import UTC, datetime, timedelta

from finch.github.ingestion import Ingestor
from finch.github.models import CommitDetail, CommitFile, CommitSummary
from finch.settings import DailyBudget, Paths, Settings
from finch.storage.database import Store
from finch.storage.repositories import CommitIngestionRepository, RepoCursorRepository

REPO = "flingjie/FDE-Gym"
NOW = datetime(2026, 9, 5, tzinfo=UTC)


def _detail(sha, files, message="feat: x"):
    return CommitDetail(
        sha=sha, message=message, author_date=NOW - timedelta(hours=1),
        html_url="u", parents=[], files=files, stats={},
    )


class _FakeGh:
    def __init__(self, summaries, details_by_sha):
        self.summaries = summaries
        self.details_by_sha = details_by_sha

    def list_commits(self, repo, since=None, per_page=100):
        return self.summaries

    def commit_detail(self, repo, sha):
        return self.details_by_sha[sha]


def _settings(tmp_path):
    return Settings(
        repositories=[REPO],
        paths=Paths(db_path=tmp_path / "db.sqlite"),
        daily_budget=DailyBudget(max_detail_fetches=2, max_change_groups=1),
    )


def test_ingest_fetches_budget_and_defers_rest(tmp_path, monkeypatch):
    monkeypatch.setattr("finch.github.ingestion.find_local_clone", lambda *a: None)
    shas = [f"{i:040d}" for i in range(5)]
    summaries = [
        CommitSummary(sha=s, message="fix: thing", author_date=NOW - timedelta(hours=1),
                      html_url="u", parents=[])
        for s in shas
    ]
    gh = _FakeGh(summaries, {
        s: _detail(s, [CommitFile(filename="src/a.py", status="modified",
                                   additions=1, deletions=0)])
        for s in shas
    })
    store = Store(tmp_path / "db.sqlite")
    store.init()
    ing = Ingestor(gh, _settings(tmp_path), CommitIngestionRepository(store),
                   RepoCursorRepository(store))
    groups = ing.ingest([REPO], existing_topics=set())

    # 全部 5 个 commit 都进入 ledger（发现），但只有 max_detail_fetches=2 被 fetch 成 grouped
    repo = CommitIngestionRepository(store)
    assert len(repo.known_shas(REPO)) == 5
    assert len(repo.list_pending(REPO)) == 3
    # 仅 1 个 group 进入本轮提取预算
    assert sum(len(g) for g in groups[REPO]) == 1


def test_ingest_skips_noise(tmp_path, monkeypatch):
    monkeypatch.setattr("finch.github.ingestion.find_local_clone", lambda *a: None)
    gh = _FakeGh(
        [CommitSummary(sha="n" * 40, message="chore: format",
                       author_date=NOW - timedelta(hours=1), html_url="u", parents=[])],
        {"n" * 40: _detail("n" * 40,
                           [CommitFile(filename="package-lock.json", status="modified",
                                       additions=1, deletions=1)],
                           message="chore: format")},
    )
    store = Store(tmp_path / "db.sqlite")
    store.init()
    ing = Ingestor(gh, _settings(tmp_path), CommitIngestionRepository(store),
                   RepoCursorRepository(store))
    ing.ingest([REPO], existing_topics=set())
    assert CommitIngestionRepository(store).list_pending(REPO) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_ingestion.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'finch.github.ingestion'`

- [ ] **Step 3: Write minimal implementation**

First, add the page-bounded method to `src/finch/github/gh_client.py` (after `list_commits`):

```python
    def list_commits_newest_first(
        self, repo: str, *, per_page: int = 100, max_commits: int = 200
    ) -> list[CommitSummary]:
        """从 main 分支 HEAD 起、按新到旧分页拉取，最多 ``max_commits`` 条（有界）。"""
        out: list[CommitSummary] = []
        page = 1
        while len(out) < max_commits:
            url = f"repos/{repo}/commits?sha=main&per_page={per_page}&page={page}"
            data = self._gh_json(
                ["gh", "api", "-H", "Accept: application/vnd.github+json", url],
                timeout=60.0,
            )
            assert isinstance(data, list)
            if not data:
                break
            for item in data:
                out.append(parse_commit_summary(item))
            if len(data) < per_page:
                break
            page += 1
        return out[:max_commits]
```

Then create `src/finch/github/ingestion.py`:

```python
"""摄取阶段（阶段 A）：per-SHA 增量发现 + 两段有界预算 + 预分组 group。"""

from datetime import UTC, datetime, timedelta

from ..evidence.budget import rank_pending, select_groups
from ..settings import Settings
from ..storage.repositories import CommitIngestionRepository, RepoCursorRepository
from .change_grouper import group_commits
from .commit_reader import is_noise
from .gh_client import GhClient
from .local_repo import LocalRepoClient, find_local_clone
from .models import CommitDetail, CommitSummary


class Ingestor:
    """把新 commit 增量落 ledger，并产出本轮预算内的预分组 group。"""

    def __init__(
        self,
        gh: GhClient,
        settings: Settings,
        ingestion: CommitIngestionRepository,
        cursor: RepoCursorRepository,
    ) -> None:
        self.gh = gh
        self.settings = settings
        self.ingestion = ingestion
        self.cursor = cursor

    def ingest(
        self, repos: list[str], existing_topics: set[str]
    ) -> dict[str, list[list[CommitDetail]]]:
        now = datetime.now(UTC)
        budget = self.settings.daily_budget
        return {
            repo: self._ingest_repo(repo, existing_topics, now, budget) for repo in repos
        }

    def _ingest_repo(
        self, repo: str, existing_topics: set[str], now: datetime, budget
    ) -> list[list[CommitDetail]]:
        # 1) 发现新 commit summary（per-SHA 增量）
        known = self.ingestion.known_shas(repo)
        summaries = self._discover(repo, known)
        if summaries:
            self.ingestion.upsert_pending(repo, summaries)
            self.cursor.advance(repo, summaries[0].sha)  # newest-first，仅成功后推进

        # 2) 段 1：从 pending 池选出 detail-fetch 候选
        pending = self.ingestion.list_pending(repo)
        items = [
            (CommitSummary.model_validate_json(r.payload_json), r.discovered_at)
            for r in pending
        ]
        for summary in rank_pending(items, existing_topics, budget, now)[: budget.max_detail_fetches]:
            detail = self._fetch_detail(repo, summary.sha)
            if is_noise(detail):
                self.ingestion.mark_skipped(repo, summary.sha)
            else:
                self.ingestion.store_detail(repo, detail)

        # 3) 段 2：分组 + 选出本轮提取预算
        grouped = self.ingestion.list_grouped(repo)
        details = [CommitDetail.model_validate_json(r.payload_json) for r in grouped]
        discovered = {r.sha: r.discovered_at for r in grouped}
        groups = list(group_commits(details))
        return select_groups(groups, existing_topics, budget, discovered, now)

    def _discover(self, repo: str, known: set[str]) -> list[CommitSummary]:
        local = find_local_clone(repo, self.settings.paths.local_repos_dirs)
        if not known:
            # 首次：有界 backfill，只取最近 lookback_hours 内的 commit。
            since = (
                datetime.now(UTC)
                - timedelta(hours=self.settings.repository_discovery.lookback_hours)
            ).isoformat()
            if local is not None:
                return LocalRepoClient(repo, local).list_commits(repo, since=since)
            return self.gh.list_commits(repo, since=since)
        if local is not None:
            all_summaries = LocalRepoClient(repo, local).list_commits(repo, since=None)
        else:
            all_summaries = self.gh.list_commits_newest_first(repo)
        new: list[CommitSummary] = []
        for s in all_summaries:
            if s.sha in known:
                break
            new.append(s)
        return new

    def _fetch_detail(self, repo: str, sha: str) -> CommitDetail:
        local = find_local_clone(repo, self.settings.paths.local_repos_dirs)
        if local is not None:
            return LocalRepoClient(repo, local).commit_detail(repo, sha)
        return self.gh.commit_detail(repo, sha)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_ingestion.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/finch/github/gh_client.py src/finch/github/ingestion.py tests/unit/test_ingestion.py
git commit -m "feat(github): add bounded SHA-incremental ingestor"
```

---

### Task 7: Wire graph + CLI to pre-grouped ingestion

**Files:**
- Modify: `src/finch/graph/pipeline.py` (`make_extract_node`)
- Modify: `src/finch/graph/daily.py` (`daily_nodes`)
- Modify: `src/finch/cli.py` (`run_daily`, `run_resume`, imports)
- Test: `tests/unit/test_cli_run.py`

**Interfaces:**
- Produces:
  - `make_extract_node(*, extractor, groups_by_repo: dict[str, list[list[CommitDetail]]], repo_is_private, known_commit_urls, cards_repo, ingestion_repo, max_extract_retries: int) -> Node`
  - `daily_nodes(*, settings, store, gh, opencli, extractor, runner, groups_by_repo, known_commit_urls, repo_is_private, voice_profile=None, inference_runners=None) -> list[Node]` (drops `commits_by_repo`; drops the `sync_fn`/`make_sync_node` path).
- Consumes: `Ingestor` (Task 6), `extract_grouped` (Task 5), `CommitIngestionRepository` (Task 2).

- [ ] **Step 1: Write the failing test**

Update `tests/unit/test_cli_run.py`. Replace `test_run_daily_loads_recent_commits_only` and `test_run_daily_enabled_echoes_engagement_summary` with the new ingest-based versions:

```python
def test_run_daily_uses_ingestor(monkeypatch, tmp_path):
    settings = Settings(
        repositories=["flingjie/FDE-Gym"],
        paths=Paths(db_path=tmp_path / "finch.db"),
        engagement=EngagementSettings(enabled=False),
    )
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    captured = {}

    class FakeIngestor:
        def __init__(self, gh, settings, ingestion, cursor):
            captured["constructed"] = True

        def ingest(self, repos, existing_topics=None):
            captured["repos"] = repos
            return {}

    class FakeGh:
        def repo_view(self, repo):
            from finch.github.models import RepoInfo

            return RepoInfo(
                name_with_owner=repo,
                default_branch="main",
                url="https://github.com/" + repo,
                is_private=False,
            )

    monkeypatch.setattr(cli, "GhClient", lambda: FakeGh())
    monkeypatch.setattr(cli, "Ingestor", FakeIngestor)
    monkeypatch.setattr(cli, "daily_nodes", lambda **kwargs: [])
    monkeypatch.setattr(cli, "load_voice_profile", lambda path: None)

    r = CliRunner().invoke(app, ["run", "daily"])
    assert r.exit_code == 0, r.output
    assert captured["repos"] == ["flingjie/FDE-Gym"]


def test_run_daily_enabled_echoes_engagement_summary(monkeypatch, tmp_path):
    from finch.engagement.flow import EngagementRunResult

    settings = Settings(
        repositories=["flingjie/FDE-Gym"],
        paths=Paths(db_path=tmp_path / "finch.db"),
        engagement=EngagementSettings(enabled=True, platforms=["x"]),
    )
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    class FakeIngestor:
        def __init__(self, gh, settings, ingestion, cursor):
            pass

        def ingest(self, repos, existing_topics=None):
            return {}

    class FakeGh:
        def repo_view(self, repo):
            from finch.github.models import RepoInfo

            return RepoInfo(
                name_with_owner=repo,
                default_branch="main",
                url="https://github.com/" + repo,
                is_private=False,
            )

    monkeypatch.setattr(cli, "GhClient", lambda: FakeGh())
    monkeypatch.setattr(cli, "Ingestor", FakeIngestor)
    monkeypatch.setattr(cli, "daily_nodes", lambda **kwargs: [])
    monkeypatch.setattr(cli, "load_voice_profile", lambda path: None)

    def fake_engagement_flow(
        settings, opencli, runner, *, reddit_opencli=None, run_id, skip_ids=None
    ):
        return EngagementRunResult(
            run_id=run_id, posts_found=0, candidates=[], failures=[],
            status="empty", summary="engagement: no posts found",
        )

    monkeypatch.setattr(cli, "run_discovery_engagement_flow", fake_engagement_flow)

    r = CliRunner().invoke(app, ["run", "daily"])
    assert r.exit_code == 0, r.output
    assert "engagement: no posts found" in r.output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_cli_run.py::test_run_daily_uses_ingestor -v`
Expected: FAIL — `AttributeError: module 'finch.cli' has no attribute 'Ingestor'`

- [ ] **Step 3: Write minimal implementation**

**3a.** In `src/finch/graph/pipeline.py`, replace `make_extract_node`:

```python
def make_extract_node(
    *,
    extractor: Extractor,
    groups_by_repo: dict[str, list[list[CommitDetail]]],
    repo_is_private: dict[str, bool],
    known_commit_urls: set[str],
    cards_repo: EvidenceRepository,
    ingestion_repo: CommitIngestionRepository,
    max_extract_retries: int,
) -> Node:
    class ExtractNode(Node):
        def run(self, ctx: dict) -> NodeResult:
            cards: list[EvidenceCard] = []
            all_shas: dict[str, list[str]] = {}
            for repo, groups in groups_by_repo.items():
                shas = [c.sha for g in groups for c in g]
                all_shas[repo] = shas
                try:
                    events = extractor.extract_grouped(groups, repo)
                except Exception:  # noqa: BLE001
                    for r, s in all_shas.items():
                        ingestion_repo.mark_failed(r, s, max_extract_retries)
                    raise
                repo_cards = build_cards(events)
                if repo_is_private.get(repo, False):
                    repo_cards = [c.model_copy(update={"publishable": False}) for c in repo_cards]
                cards.extend(repo_cards)

            report = scan_cards(
                cards,
                repo_is_private=repo_is_private,
                known_commit_urls=known_commit_urls,
            )
            if report.hard_fail:
                for r, s in all_shas.items():
                    ingestion_repo.mark_failed(r, s, max_extract_retries)
                return NodeResult(
                    status="failed", error_code=report.hits[0].code, retryable=False
                )
            cards_repo.upsert_cards(cards)
            for r, s in all_shas.items():
                ingestion_repo.mark_extracted(r, s)
            return NodeResult(
                status="succeeded", output=items_payload(cast(list[BaseModel], cards))
            )

    return ExtractNode(
        name="extract_events", reads=[], writes="evidence_cards", succeeds_to="EVENTS_EXTRACTED"
    )
```

Add the import and remove the now-unused `make_sync_node` (see 3c). Add to the imports:

```python
from ..storage.repositories import CommitIngestionRepository, EvidenceRepository
```

**3b.** In `src/finch/graph/daily.py`, update `daily_nodes` signature and body:

```python
def daily_nodes(
    *,
    settings: Settings,
    store: Store,
    gh: GhClient,
    opencli: OpenCliClient,
    extractor: Extractor,
    runner: CodexRunner,
    groups_by_repo: dict[str, list[list[CommitDetail]]],
    known_commit_urls: set[str],
    repo_is_private: dict[str, bool],
    voice_profile: VoiceProfile | None = None,
    inference_runners: dict[str, StructuredInferenceRunner | None] | None = None,
) -> list[Node]:
    """组装每日 Graph：preflight → extract → collect → recall → match → draft →
    critique → brief。提取节点对 groups_by_repo 中的预分组 group 提取事件并合并 Evidence Cards。
    """
    def collect_fn() -> list[DiscussionCandidate]:
        builder = QueryBuilder(
            settings.twitter.queries, per_query_limit=settings.twitter.per_query_limit
        )
        candidates: list[DiscussionCandidate] = []
        for cfg in builder:
            if len(candidates) >= settings.twitter.daily_limit:
                break
            tweets = opencli.search(
                cfg.text, product=cfg.filter, limit=settings.twitter.per_query_limit
            )
            for tweet in normalize_tweets(tweets):
                if len(candidates) >= settings.twitter.daily_limit:
                    break
                candidates.append(to_candidate(tweet, query_id=cfg.id))
        return candidates

    jobs_repo = ContentJobRepository(store)

    def _resolve(node_name: str) -> StructuredInferenceRunner:
        if inference_runners is None:
            return runner
        return inference_runners.get(node_name) or runner

    return [
        make_preflight_node(gh, opencli),
        make_extract_node(
            extractor=extractor,
            groups_by_repo=groups_by_repo,
            repo_is_private=repo_is_private,
            known_commit_urls=known_commit_urls,
            cards_repo=EvidenceRepository(store),
            ingestion_repo=CommitIngestionRepository(store),
            max_extract_retries=settings.daily_budget.max_extract_retries,
        ),
        make_collect_node(collect_fn),
        make_recall_node(settings.quality_gates),
        make_match_node(_resolve("match_evidence"), settings.quality_gates, settings.twitter),
        make_define_jobs_node(
            _resolve("plan_topics"),
            _resolve("expand_job"),
            expand_concurrency=settings.llm.for_node("expand_job").max_concurrency,
            jobs_repo=jobs_repo,
        ),
        make_position_gate_node(jobs_repo=jobs_repo),
        make_draft_node(runner, write_reply, write_original, settings.quality_gates),
        make_critique_node(
            runner, rewrite, settings.quality_gates,
            checkers=default_checker_suite(_resolve("critique"), voice_profile),
            voice_profile=voice_profile,
        ),
        make_brief_node(settings.quality_gates, jobs_repo=jobs_repo),
    ]
```

Remove `CommitReader` import and the `sync_fn` closure; add `CommitIngestionRepository` import:

```python
from ..storage.repositories import (
    CommitIngestionRepository,
    ContentJobRepository,
    EvidenceRepository,
)
```

Also drop `make_sync_node` from the `.pipeline` import:

```python
from .pipeline import (
    make_collect_node,
    make_extract_node,
    make_preflight_node,
)
```

**3c.** In `src/finch/graph/pipeline.py`, delete `make_sync_node` and its now-unused `Callable` import (keep `Callable` — `make_collect_node` still uses it):

```python
from collections.abc import Callable
```

Delete only the `make_sync_node` function definition.

**3d.** In `src/finch/cli.py`:

Add imports:

```python
from .github.ingestion import Ingestor
from .storage.repositories import CommitIngestionRepository, RepoCursorRepository
```

Replace the body of `run_daily` (from `repos = resolve_repositories(...)` through the `nodes = daily_nodes(...)` construction) with:

```python
    repos = resolve_repositories(settings, gh)

    ingestion_repo = CommitIngestionRepository(store)
    cursor_repo = RepoCursorRepository(store)
    existing_topics = {
        topic for card in EvidenceRepository(store).list_cards() for topic in card.topics
    }
    groups_by_repo = Ingestor(gh, settings, ingestion_repo, cursor_repo).ingest(
        repos, existing_topics=existing_topics
    )

    repo_is_private: dict[str, bool] = {}
    known_commit_urls: set[str] = set()
    for repo in repos:
        repo_is_private[repo] = gh.repo_view(repo).is_private
    for repo, groups in groups_by_repo.items():
        for group in groups:
            for commit in group:
                known_commit_urls.add(f"https://github.com/{repo}/commit/{commit.sha}")

    nodes = daily_nodes(
        settings=settings,
        store=store,
        gh=gh,
        opencli=opencli,
        extractor=Extractor(
            create_runner(settings.llm) or CodexRunner(),
            settings=settings.extraction,
            cache_path=settings.paths.cache_dir / "extraction_cache.json",
        ),
        runner=CodexRunner(),
        groups_by_repo=groups_by_repo,
        known_commit_urls=known_commit_urls,
        repo_is_private=repo_is_private,
        voice_profile=load_voice_profile(settings.paths.voice_profile_path),
        inference_runners={
            "match_evidence": create_runner(settings.llm, "match_evidence"),
            "plan_topics": create_runner(settings.llm, "plan_topics"),
            "expand_job": create_runner(settings.llm, "expand_job"),
            "critique": create_runner(settings.llm, "critique"),
        },
    )
```

Remove the now-unused imports in `cli.py`: `load_commit_details` and `CommitReader` are still used by `github_reflect` — keep them. Remove `EvidenceCard` import only if now unused (it is still used in `_evidence_cards_for`); keep.

**3e.** In `src/finch/cli.py`, update `run_resume`'s `daily_nodes` call. Replace:

```python
    commits_by_repo: dict[str, list[CommitDetail]] = {
        repo: [] for repo in settings.repositories
    }
    known_commit_urls: set[str] = set()
    repo_is_private = {repo: False for repo in settings.repositories}
```

with:

```python
    groups_by_repo: dict[str, list[list[CommitDetail]]] = {
        repo: [] for repo in settings.repositories
    }
    known_commit_urls: set[str] = set()
    repo_is_private = {repo: False for repo in settings.repositories}
```

and change `commits_by_repo=commits_by_repo` → `groups_by_repo=groups_by_repo` in the `run_resume` `daily_nodes(...)` call.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_cli_run.py tests/unit/test_extractor.py -v`
Expected: PASS. Also run `uv run pytest tests/graph -v` if graph tests exist.

- [ ] **Step 5: Commit**

```bash
git add src/finch/graph/pipeline.py src/finch/graph/daily.py src/finch/cli.py tests/unit/test_cli_run.py
git commit -m "feat(graph): wire extract node to pre-grouped ingestion and mark ledger status"
```

---

### Task 8: `make_define_jobs_node` trims cards via `select_planning_evidence`

**Files:**
- Modify: `src/finch/graph/content_nodes.py`
- Modify: `src/finch/graph/daily.py`
- Test: `tests/unit/test_nodes.py`

**Interfaces:**
- Produces: `make_define_jobs_node(plan_runner, expand_runner, expand_concurrency=4, jobs_repo=None, budget: DailyBudget | None = None) -> Node`. When `budget` is provided, `plan_content_topics` receives the trimmed card set; `cards_by_id` still maps the full set. `daily_nodes` passes `budget=settings.daily_budget`.
- Consumes: `select_planning_evidence` (Task 4), `DailyBudget` (Task 1).

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_nodes.py`:

```python
from finch.graph.content_nodes import make_define_jobs_node
from finch.settings import DailyBudget


class _EchoPlanRunner:
    def run(self, prompt, model, timeout=None):
        # 记录最近一次传入的卡片 id 数（用于断言裁剪生效）
        import json as _json
        from finch.content.jobs import PlanTopicsOutput
        self.last_prompt = prompt
        return PlanTopicsOutput(items=[])


def test_define_jobs_trims_planning_cards_when_budget_set():
    from finch.evidence.models import ClaimConfidence, EvidenceCard
    from finch.graph.context import items_payload

    cards = [
        EvidenceCard(id=f"ev_{i}", event_id=f"evt_{i}", claim="c", sources=[],
                     confidence=ClaimConfidence.VERIFIED, publishable=True, topics=[])
        for i in range(30)
    ]
    runner = _EchoPlanRunner()
    node = make_define_jobs_node(runner, runner, jobs_repo=None,
                                 budget=DailyBudget(max_planning_events=5))
    # 直接调用 run，传入仅有 evidence_cards 的 context（其余读键用空 payload）
    result = node.run({
        "evidence_cards": items_payload(cards),
        "match_results": items_payload([]),
        "candidates": items_payload([]),
    })
    assert result.status == "succeeded"
    assert '"ev_4"' in runner.last_prompt  # 裁剪后保留前 5 个 event（每个 event 1 卡）
    assert '"ev_5"' not in runner.last_prompt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_nodes.py::test_define_jobs_trims_planning_cards_when_budget_set -v`
Expected: FAIL — `TypeError: make_define_jobs_node() got an unexpected keyword argument 'budget'`

- [ ] **Step 3: Write minimal implementation**

In `src/finch/graph/content_nodes.py`, add imports:

```python
from ..settings import DailyBudget, QualityGates
```

Change the `make_define_jobs_node` signature and body:

```python
def make_define_jobs_node(
    plan_runner: StructuredInferenceRunner,
    expand_runner: StructuredInferenceRunner,
    expand_concurrency: int = 4,
    jobs_repo: ContentJobRepository | None = None,
    budget: DailyBudget | None = None,
) -> Node:
```

Inside `DefineJobsNode.run`, after building `cards_by_id` and before the `plan_content_topics` call, insert:

```python
            planning_cards = (
                select_planning_evidence(cards, match_results, budget)
                if budget is not None
                else cards
            )
```

and change:

```python
            topics = plan_content_topics(plan_runner, cards, match_results, candidates)
```

to:

```python
            topics = plan_content_topics(plan_runner, planning_cards, match_results, candidates)
```

Add `select_planning_evidence` to the `..content.jobs` import:

```python
from ..content.jobs import (
    ContentJob,
    ContentJobStatus,
    TopicProposal,
    expand_content_job,
    plan_content_topics,
    select_planning_evidence,
    select_primary_job,
)
```

- [ ] **Step 3b: Wire `budget` into `daily_nodes`**

In `src/finch/graph/daily.py`, pass `budget=settings.daily_budget` to `make_define_jobs_node` (the call currently reads `jobs_repo=jobs_repo,`):

```python
        make_define_jobs_node(
            _resolve("plan_topics"),
            _resolve("expand_job"),
            expand_concurrency=settings.llm.for_node("expand_job").max_concurrency,
            jobs_repo=jobs_repo,
            budget=settings.daily_budget,
        ),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_nodes.py tests/unit/test_jobs.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/finch/graph/content_nodes.py tests/unit/test_nodes.py
git commit -m "feat(content): trim planning evidence before plan_topics"
```

---

### Task 9: Remove `CommitReader.sync` / shared cursor / `make_sync_node` / `github sync`

**Files:**
- Modify: `src/finch/github/commit_reader.py`
- Modify: `src/finch/cli.py` (remove `github_sync` command)
- Modify: `tests/unit/test_commit_reader.py`
- Delete: `tests/unit/test_cli_github.py`

**Interfaces:**
- Removes: `CommitReader.sync`, `_cursor_path`, `_load_cursor`, `_save_cursor`, `github_sync` CLI command, `make_sync_node` (already removed in Task 7).

- [ ] **Step 1: Write the failing test**

This task is removal — the "failing test" is the suite asserting the removed symbols are gone. Update `tests/unit/test_commit_reader.py` by deleting `test_sync_uses_cursor_and_advances` and the now-unused `Path` import usage. Delete `tests/unit/test_cli_github.py`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_commit_reader.py tests/unit/test_cli_github.py -v`
Expected: FAIL — `test_cli_github.py` references `finch.cli.GhClient` for `github sync` which will error after removal (or the file is already deleted → collection error).

- [ ] **Step 3: Write minimal implementation**

**3a.** In `src/finch/github/commit_reader.py`, delete `sync`, `_cursor_path`, `_load_cursor`, `_save_cursor`, and remove the `json` import. Keep `is_noise`, `filter_noise`, `load_commit_details`. The file becomes:

```python
"""Commit 噪声过滤与详情加载（spec 12.1「Commit 游标推进」已由 ingestion ledger 取代）。"""

from pathlib import Path

from .gh_client import GhClient
from .local_repo import LocalRepoClient, find_local_clone
from .models import CommitDetail, CommitSummary

_LOCKFILE_MARKERS = ("package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
                     "Cargo.lock", "Gemfile.lock", "go.sum", "uv.lock")
_FORMAT_MARKERS = ("format", "formatting", "prettier", "lint", "style", "black", "ruff")


def is_noise(commit: CommitDetail) -> bool:
    if not commit.files:
        return True
    paths = [f.filename.lower() for f in commit.files]
    if all(any(m in p for m in _LOCKFILE_MARKERS) for p in paths):
        return True
    if all(f.status == "renamed" for f in commit.files):
        return True
    if any(m in commit.message.lower() for m in _FORMAT_MARKERS) and not any(
        f.additions > 0 and f.status == "added" for f in commit.files
    ):
        return True
    return False


class CommitReader:
    def __init__(self, gh: GhClient, repo: str):
        self.gh = gh
        self.repo = repo

    def filter_noise(self, commits: list[CommitDetail]) -> list[CommitDetail]:
        return [c for c in commits if not is_noise(c)]


def load_commit_details(
    repo: str,
    gh: GhClient,
    *,
    local_dirs: list[Path],
    since: str | None = None,
    workers: int = 6,
) -> list[CommitDetail]:
    """Load commit details from a local checkout when available, else gh."""
    local = find_local_clone(repo, local_dirs)
    if local is not None:
        client = LocalRepoClient(repo, local)
        return client.list_commit_details(repo, since=since)
    summaries = gh.list_commits(repo, since=since)
    return gh.list_commit_details(repo, [s.sha for s in summaries], workers=workers)
```

**3b.** In `src/finch/cli.py`, delete the `github_sync` command:

```python
@github_app.command("sync")
def github_sync(repo: str = typer.Option("flingjie/FDE-Gym"), since: str | None = None) -> None:
    """增量读取仓库 Commit 并推进游标。"""
    reader = CommitReader(GhClient(), repo=repo)
    commits = reader.sync(since=_since_iso(since))
    typer.echo(f"synced {len(commits)} commits for {repo}")
```

`_since_iso` remains (still used by `github_reflect`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_commit_reader.py -v && uv run ruff check src/finch/github/commit_reader.py`
Expected: PASS + no lint errors (unused `json`/`datetime` imports removed).

- [ ] **Step 5: Commit**

```bash
git add src/finch/github/commit_reader.py src/finch/cli.py tests/unit/test_commit_reader.py
git rm tests/unit/test_cli_github.py
git commit -m "refactor(github): remove shared cursor sync path (replaced by ingestion ledger)"
```

---

## Self-Review

**Spec coverage (Phase A):**
- §3 data model (ledger + cursor) → Task 2.
- §4 ingest (two-stage, bounded, SHA-incremental, cursor-after-persist) → Task 6 (+ Task 3 scoring).
- §4 `select_groups` unified ranking + age_bonus → Task 3.
- §5 extract wiring (mark extracted/failed, pre-grouped) → Tasks 5 + 7.
- §6 `select_planning_evidence` → Tasks 4 + 8.
- §11 config → Task 1.
- §4 "remove make_sync_node / CommitReader.sync / shared cursor" → Tasks 7c + 9.

**Placeholder scan:** none — every step carries concrete code and commands.

**Type consistency:**
- `select_groups(groups, existing_topics, settings, discovered_at, now)` matches its definition and the `Ingestor._ingest_repo` call site.
- `rank_pending(items, existing_topics, settings, now)` — `items` is `list[tuple[CommitSummary, datetime]]`; `Ingestor` builds it via `CommitSummary.model_validate_json(r.payload_json)`.
- `make_extract_node(... groups_by_repo, ingestion_repo, max_extract_retries)` matches `daily_nodes` and `pipeline.py`; `run_daily`/`run_resume` pass `groups_by_repo`.
- `CommitIngestionRepository.mark_failed(repo, shas, max_retries)` matches the `make_extract_node` call.
- `make_define_jobs_node(..., budget=...)` matches Task 8 (signature change + `daily_nodes` wiring in the same task, so no transient break).

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-09-05-daily-work-budget-phase-a.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
