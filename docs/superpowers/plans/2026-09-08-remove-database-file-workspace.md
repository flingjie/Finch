# Remove Database → File Workspace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace SQLite/SQLModel/Alembic with a file workspace (`Workspace`), preserving every existing command/service/repository-method behavior, then add agent-first `finch context` + projections.

**Architecture:** A thin `Workspace` (filesystem root + atomic write + YAML/frontmatter/JSONL helpers) replaces `Store`. All ~20 repository classes keep their exact public method signatures, only swapping `Session.merge/commit` for file read/write and changing the constructor arg `Store` → `Workspace`. Domain models stay Pydantic; filenames are the existing domain IDs.

**Tech Stack:** Python 3.12, Pydantic 2, PyYAML (already a dep), Typer. Removes: sqlmodel, sqlalchemy, alembic.

**Task boundaries note:** the repository constructor change (`Store` → `Workspace`) is a hard coupling point — `cli.py` and ~20 test files all construct `XxxRepository(store)` directly. There is no way to split "storage swap" from "CLI swap" into two green tasks: changing the constructor breaks every caller at once. So Task 2 is one atomic "core swap" (repositories + cli.py + all their tests together), leaving the full suite green at its end.

## Global Constraints

- Python 3.12+; Pydantic 2 models (`StrEnum`/`Literal`/`Field`); SQLModel records removed entirely.
- Ruff selects `E,F,I,B,UP`; line-length 100. `alembic/versions` lint-exclude removed.
- Bilingual (Chinese/English) docstrings match surrounding files.
- Domain services stay deterministic and single-threaded; no `asyncio.gather`.
- Behavior-preserving: every existing command and repository method keeps its current input/output contract. `run_id` (InteractionRepository) and `execution_outcome`/`execution_detail` (record_execution) were write-only DB columns never read back — they become accepted-but-ignored params.
- Deterministic IDs already live on the domain models (`peer_id_for`, `generation_key`, `ContentJob.id`, etc.); filenames reuse them — no new hashing layer.
- All writes go through `Workspace.atomic_write` (temp file + `os.replace`). No file locking.
- Fresh start: `var/finch.db` is deleted, no migration, no archive.

---

## Task 1: `Workspace` class + primitives

✅ **DONE** (commit `89164a5`). `src/finch/storage/workspace.py` provides `Workspace(root)`, `.ensure()`, `.dir(name)`, `.safe_filename(name)`, `.atomic_write(path, text)`, `.write_yaml(path, model)`, `.read_yaml(path, Model)`, `.write_frontmatter(path, meta, body)`, `.read_frontmatter(path) -> (meta, body)`, `.append_jsonl(path, obj)`, `.read_jsonl(path)`.

---

## Task 2: Core swap — repositories + cli.py + all tests

**Files:**
- Rewrite: `src/finch/storage/repositories.py`
- Modify: `src/finch/cli.py`
- Create: `tests/unit/test_file_repositories.py`
- Convert (repository tests, 6): `tests/unit/test_peer_repositories.py`, `tests/unit/test_author_repositories.py`, `tests/unit/test_feedback_repository.py`, `tests/unit/test_interaction_repository.py`, `tests/unit/test_interaction_record_repository.py`, `tests/unit/test_repositories.py`
- Convert (service tests, 4): `tests/unit/test_conversation_service.py`, `tests/unit/test_jobs.py`, `tests/unit/test_practice_service.py`, `tests/unit/test_weekly.py`
- Convert (CLI tests, 10): `tests/unit/test_cli_run.py`, `tests/unit/test_cli_review_engagement.py`, `tests/unit/test_cli_connect.py`, `tests/unit/test_cli_ideas.py`, `tests/unit/test_cli_drafts.py`, `tests/unit/test_cli_learn.py`, `tests/unit/test_cli_practice.py`, `tests/unit/test_cli_style.py`, `tests/unit/test_cli_voice.py`, `tests/unit/test_connection_loop_e2e.py`
- Delete (DB tests, 3): `tests/unit/test_database.py`, `tests/unit/test_storage.py`, `tests/unit/test_alembic.py`

**Interfaces:**
- Consumes: `Workspace` (Task 1).
- Produces: the same 16 repository classes with identical method signatures (code below); `cli.py` wired to `Workspace`.

### Step 1: Rewrite `repositories.py` (full replacement)

```python
"""文件工作区仓储：领域模型 → YAML / frontmatter-Markdown / JSONL。

保留原公有方法签名，只把 ``Session.merge/commit`` 换成文件原子写；构造入参由
``Store`` 改为 ``Workspace``。二级查找（find_by_generation_key / list_by_* /
list_pending）由「同类独立目录 + glob + Python 过滤」实现。
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar

import yaml
from pydantic import BaseModel, ValidationError

from finch.author.models import PublicationIntent
from finch.content.checkers.base import CheckResult
from finch.content.jobs import ContentJob
from finch.content.models import Draft
from finch.conversations.models import ConversationThread
from finch.engagement.models import (
    ConversationEvidence,
    EngagementRunStats,
    FeedbackSnapshot,
    InteractionProposal,
    InteractionRecord,
    InteractionStatus,
)
from finch.evidence.models import EvidenceCard
from finch.inbox.models import DecisionRecord
from finch.learn.models import Feedback
from finch.peers.models import PeerProfile
from finch.practice.models import PracticeSession
from finch.storage.workspace import Workspace

T = TypeVar("T", bound=BaseModel)


def _write(ws: Workspace, name: str, key: str, model: BaseModel) -> None:
    ws.write_yaml(ws.dir(name) / f"{ws.safe_filename(key)}.yaml", model)


def _read(ws: Workspace, name: str, key: str, model_cls: type[T]) -> T | None:
    return ws.read_yaml(ws.dir(name) / f"{ws.safe_filename(key)}.yaml", model_cls)


def _list_all(ws: Workspace, name: str, model_cls: type[T]) -> list[T]:
    out: list[T] = []
    for path in sorted(ws.dir(name).glob("*.yaml")):
        obj = ws.read_yaml(path, model_cls)
        if obj is not None:
            out.append(obj)
    return out


class EvidenceRepository:
    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def upsert_card(self, card: EvidenceCard) -> None:
        _write(self.ws, "evidence", card.id, card)

    def upsert_cards(self, cards: list[EvidenceCard]) -> None:
        for card in cards:
            self.upsert_card(card)

    def get_card(self, card_id: str) -> EvidenceCard | None:
        return _read(self.ws, "evidence", card_id, EvidenceCard)

    def list_cards(self) -> list[EvidenceCard]:
        return _list_all(self.ws, "evidence", EvidenceCard)


class DraftRepository:
    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def _path(self, draft_id: str) -> Path:
        return self.ws.dir("drafts") / self.ws.safe_filename(draft_id) / "draft.md"

    def upsert_draft(self, draft: Draft) -> None:
        meta = draft.model_dump(mode="json", exclude={"body"})
        self.ws.write_frontmatter(self._path(draft.id), meta, draft.body)

    def get_draft(self, draft_id: str) -> Draft | None:
        path = self._path(draft_id)
        if not path.exists():
            return None
        meta, body = self.ws.read_frontmatter(path)
        return Draft.model_validate({**meta, "body": body})

    def list_drafts(self) -> list[Draft]:
        out: list[Draft] = []
        for path in sorted(self.ws.dir("drafts").glob("*/draft.md")):
            meta, body = self.ws.read_frontmatter(path)
            out.append(Draft.model_validate({**meta, "body": body}))
        return out

    def list_by_job(self, job_id: str) -> list[Draft]:
        return [d for d in self.list_drafts() if d.content_job_id == job_id]


class DecisionRecordRepository:
    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def save(self, record: DecisionRecord) -> None:
        _write(self.ws, "decisions", record.id, record)

    def get(self, job_id: str) -> DecisionRecord | None:
        return _read(self.ws, "decisions", f"dec_{job_id}", DecisionRecord)

    def list(self) -> list[DecisionRecord]:
        return _list_all(self.ws, "decisions", DecisionRecord)


class FeedbackRepository:
    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def save_feedback(self, feedback: Feedback) -> None:
        _write(self.ws, "feedback", feedback.draft_id, feedback)

    def get_feedback(self, draft_id: str) -> Feedback | None:
        return _read(self.ws, "feedback", draft_id, Feedback)

    def list_feedbacks(self) -> list[Feedback]:
        return _list_all(self.ws, "feedback", Feedback)


class ContentJobRepository:
    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def upsert_job(self, job: ContentJob) -> None:
        _write(self.ws, "ideas", job.id, job)

    def upsert_jobs(self, jobs: list[ContentJob]) -> None:
        for job in jobs:
            self.upsert_job(job)

    def get_job(self, job_id: str) -> ContentJob | None:
        return _read(self.ws, "ideas", job_id, ContentJob)

    def find_by_generation_key(self, generation_key: str) -> ContentJob | None:
        for job in self.list_jobs():
            if job.generation_key == generation_key:
                return job
        return None

    def list_jobs(self) -> list[ContentJob]:
        jobs, _ = self._list_jobs_and_failures()
        return jobs

    def list_job_parse_failures(self) -> list[str]:
        _, failures = self._list_jobs_and_failures()
        return failures

    def _list_jobs_and_failures(self) -> tuple[list[ContentJob], list[str]]:
        jobs: list[ContentJob] = []
        failures: list[str] = []
        for path in sorted(self.ws.dir("ideas").glob("*.yaml")):
            try:
                job = self.ws.read_yaml(path, ContentJob)
            except (ValidationError, yaml.YAMLError):
                failures.append(path.stem)
            else:
                if job is not None:
                    jobs.append(job)
        return jobs, failures


class DraftVersionRepository:
    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def upsert_version(self, draft_id: str, round: int, draft: Draft) -> None:
        meta = draft.model_dump(mode="json", exclude={"body"})
        path = (
            self.ws.dir("drafts") / self.ws.safe_filename(draft_id) / "versions" / f"{round}.md"
        )
        self.ws.write_frontmatter(path, meta, draft.body)

    def list_versions(self, draft_id: str) -> list[Draft]:
        d = self.ws.dir("drafts") / self.ws.safe_filename(draft_id) / "versions"
        out: list[Draft] = []
        for path in sorted(d.glob("*.md"), key=lambda p: int(p.stem)):
            meta, body = self.ws.read_frontmatter(path)
            out.append(Draft.model_validate({**meta, "body": body}))
        return out


class CriticReportRepository:
    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def _path(self, draft_id: str) -> Path:
        return self.ws.dir("drafts") / self.ws.safe_filename(draft_id) / "critic.jsonl"

    def upsert_report(
        self, draft_id: str, round: int, checks: list[CheckResult], outcome: str
    ) -> None:
        payload = {
            "draft_id": draft_id,
            "round": round,
            "checks": [c.model_dump(mode="json") for c in checks],
            "outcome": outcome,
            "ts": datetime.now(UTC).isoformat(),
        }
        self.ws.append_jsonl(self._path(draft_id), payload)

    def list_reports(self, draft_id: str) -> list[dict]:
        rows = sorted(self.ws.read_jsonl(self._path(draft_id)), key=lambda r: r["round"])
        return [{"checks": r["checks"], "outcome": r["outcome"]} for r in rows]

    def list_all_reports(self, since: datetime | None = None) -> dict[str, list[dict]]:
        grouped: dict[str, list[dict]] = {}
        for path in sorted(self.ws.dir("drafts").glob("*/critic.jsonl")):
            for r in self.ws.read_jsonl(path):
                if since is not None and datetime.fromisoformat(r["ts"]) < since:
                    continue
                grouped.setdefault(r["draft_id"], []).append(r)
        for draft_id, rows in grouped.items():
            rows.sort(key=lambda r: r["round"])
            grouped[draft_id] = [
                {"checks": r["checks"], "outcome": r["outcome"]} for r in rows
            ]
        return grouped


class InteractionRepository:
    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def upsert(self, candidate: InteractionProposal, run_id: str) -> None:
        # run_id 保留签名兼容；文件形式不单独持久化（原列仅用于回填，从未被读取）。
        _write(self.ws, "interactions/proposals", candidate.id, candidate)

    def get(self, candidate_id: str) -> InteractionProposal | None:
        return _read(self.ws, "interactions/proposals", candidate_id, InteractionProposal)

    def find_by_generation_key(self, generation_key: str) -> InteractionProposal | None:
        for c in self.list_all():
            if c.generation_key == generation_key:
                return c
        return None

    def list_pending(self) -> list[InteractionProposal]:
        return [c for c in self.list_all() if c.status == InteractionStatus.PROPOSED]

    def list_all(self) -> list[InteractionProposal]:
        return _list_all(self.ws, "interactions/proposals", InteractionProposal)

    def list_executed(self) -> list[InteractionProposal]:
        return [c for c in self.list_all() if c.status == InteractionStatus.EXECUTED]

    def _update(self, candidate_id: str, **updates: object) -> None:
        candidate = self.get(candidate_id)
        if candidate is None:
            raise KeyError(candidate_id)
        self.upsert(candidate.model_copy(update=updates), run_id="")

    def approve(self, candidate_id: str) -> None:
        self._update(candidate_id, status=InteractionStatus.APPROVED, reject_reason=None)

    def reject(self, candidate_id: str, reason: str) -> None:
        self._update(candidate_id, status=InteractionStatus.REJECTED, reject_reason=reason)

    def edit(self, candidate_id: str, revised_draft: str) -> None:
        self._update(candidate_id, revised_draft=revised_draft)

    def record_execution(self, candidate_id: str, outcome: str, detail: str) -> None:
        candidate = self.get(candidate_id)
        if candidate is None:
            raise KeyError(candidate_id)
        self.upsert(
            candidate.model_copy(update={"status": InteractionStatus.EXECUTED}), run_id=""
        )


class FeedbackSnapshotRepository:
    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def upsert(self, snapshot: FeedbackSnapshot) -> None:
        _write(self.ws, "interactions/snapshots", snapshot.id, snapshot)

    def get(self, interaction_id: str) -> FeedbackSnapshot | None:
        matches = [s for s in self.list_all() if s.interaction_id == interaction_id]
        return matches[0] if matches else None

    def list_all(self) -> list[FeedbackSnapshot]:
        return _list_all(self.ws, "interactions/snapshots", FeedbackSnapshot)


class ConversationEvidenceRepository:
    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def upsert(self, evidence: ConversationEvidence) -> None:
        _write(self.ws, "interactions/evidence", evidence.id, evidence)

    def get(self, evidence_id: str) -> ConversationEvidence | None:
        return _read(self.ws, "interactions/evidence", evidence_id, ConversationEvidence)

    def list_unverified(self) -> list[ConversationEvidence]:
        return [e for e in self.list_all() if not e.verified]

    def list_all(self) -> list[ConversationEvidence]:
        return _list_all(self.ws, "interactions/evidence", ConversationEvidence)

    def list_verified(self) -> list[ConversationEvidence]:
        return [e for e in self.list_all() if e.verified]

    def mark_verified(self, evidence_id: str) -> None:
        evidence = self.get(evidence_id)
        if evidence is None:
            raise KeyError(evidence_id)
        self.upsert(evidence.model_copy(update={"verified": True}))

    def list_by_interaction(self, interaction_id: str) -> list[ConversationEvidence]:
        return [e for e in self.list_all() if e.interaction_id == interaction_id]


class EngagementRunStatsRepository:
    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def upsert(self, stats: EngagementRunStats) -> None:
        _write(self.ws, "interactions/run-stats", stats.run_id, stats)

    def list_all(self) -> list[EngagementRunStats]:
        return _list_all(self.ws, "interactions/run-stats", EngagementRunStats)


class PublicationIntentRepository:
    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def save(self, intent: PublicationIntent) -> None:
        _write(self.ws, "publication-intents", f"intent_{intent.source_id}", intent)

    def get(self, source_id: str) -> PublicationIntent | None:
        return _read(self.ws, "publication-intents", f"intent_{source_id}", PublicationIntent)

    def list(self) -> list[PublicationIntent]:
        return _list_all(self.ws, "publication-intents", PublicationIntent)


class PracticeSessionRepository:
    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def upsert(self, session: PracticeSession) -> None:
        _write(self.ws, "practice", session.id, session)

    def get(self, session_id: str) -> PracticeSession | None:
        return _read(self.ws, "practice", session_id, PracticeSession)


class PeerRepository:
    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def upsert(self, profile: PeerProfile) -> None:
        _write(self.ws, "peers", profile.id, profile)

    def get(self, peer_id: str) -> PeerProfile | None:
        return _read(self.ws, "peers", peer_id, PeerProfile)

    def list_all(self) -> list[PeerProfile]:
        return _list_all(self.ws, "peers", PeerProfile)


class InteractionRecordRepository:
    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def upsert(self, record: InteractionRecord) -> None:
        _write(self.ws, "interactions/records", record.id, record)

    def get(self, record_id: str) -> InteractionRecord | None:
        return _read(self.ws, "interactions/records", record_id, InteractionRecord)

    def list_by_proposal(self, proposal_id: str) -> list[InteractionRecord]:
        return [r for r in self.list_all() if r.proposal_id == proposal_id]

    def list_by_peer(self, peer_id: str) -> list[InteractionRecord]:
        return [r for r in self.list_all() if r.peer_id == peer_id]

    def list_all(self) -> list[InteractionRecord]:
        return _list_all(self.ws, "interactions/records", InteractionRecord)


class ConversationThreadRepository:
    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def upsert(self, thread: ConversationThread) -> None:
        _write(self.ws, "conversations", thread.id, thread)

    def get(self, thread_id: str) -> ConversationThread | None:
        return _read(self.ws, "conversations", thread_id, ConversationThread)

    def list_by_peer(self, peer_id: str) -> list[ConversationThread]:
        return [t for t in self.list_all() if t.peer_id == peer_id]

    def list_all(self) -> list[ConversationThread]:
        return _list_all(self.ws, "conversations", ConversationThread)
```

### Step 2: Create `test_file_repositories.py` (behavioral contract)

```python
"""文件仓储行为契约：upsert/get 往返、幂等、二级查找、状态转换。"""

from datetime import UTC, datetime

from finch.author.models import PublicationIntent
from finch.conversations.models import ConversationThread
from finch.content.jobs import ContentJob, ContentJobStatus
from finch.content.models import Draft, DraftKind
from finch.engagement.models import (
    ConversationScore,
    ExternalPost,
    InteractionAction,
    InteractionProposal,
    InteractionRecord,
    InteractionStatus,
)
from finch.inbox.models import DecisionAction, DecisionRecord
from finch.peers.models import PeerProfile, PlatformIdentity
from finch.storage.repositories import (
    ContentJobRepository,
    ConversationThreadRepository,
    DecisionRecordRepository,
    DraftRepository,
    InteractionRecordRepository,
    InteractionRepository,
    PeerRepository,
    PublicationIntentRepository,
)
from finch.storage.workspace import Workspace


def test_peer_upsert_get_idempotent(tmp_path):
    repo = PeerRepository(Workspace(tmp_path))
    p = PeerProfile(id="p1", platform_identities=[PlatformIdentity(platform="x", author_id="a")])
    repo.upsert(p)
    repo.upsert(p.model_copy(update={"display_name": "Alice"}))
    assert len(repo.list_all()) == 1
    assert repo.get("p1").display_name == "Alice"


def test_thread_list_by_peer(tmp_path):
    repo = ConversationThreadRepository(Workspace(tmp_path))
    repo.upsert(ConversationThread(id="t1", peer_id="p1", topic="x"))
    repo.upsert(ConversationThread(id="t2", peer_id="p2", topic="y"))
    assert [t.id for t in repo.list_by_peer("p1")] == ["t1"]


def test_content_job_find_by_generation_key(tmp_path):
    repo = ContentJobRepository(Workspace(tmp_path))
    job = ContentJob(
        id="idea_abc", source_card_ids=[], reader_problem="r", recommended_format="short_post",
        status=ContentJobStatus.PROPOSED, generation_key="gk1",
    )
    repo.upsert_job(job)
    assert repo.find_by_generation_key("gk1").id == "idea_abc"
    assert repo.find_by_generation_key("nope") is None


def test_interaction_approve_reject(tmp_path):
    repo = InteractionRepository(Workspace(tmp_path))
    post = ExternalPost(
        id="post1", platform="x", url="https://x.com/u/1", author_id="a", author_name="A",
        content="hello", published_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    score = ConversationScore(
        relevance=0.5, novelty=0.5, discussability=0.5, practical_evidence=0.5,
        relationship_value=0.5, total=0.5, reasons=[],
    )
    cand = InteractionProposal(
        id="c1", post=post, score=score, action=InteractionAction.DRAFT_REPLY,
        approval_required=True, status=InteractionStatus.PROPOSED, generation_key="g1",
    )
    repo.upsert(cand, run_id="run1")
    assert len(repo.list_pending()) == 1
    repo.approve("c1")
    assert repo.get("c1").status == InteractionStatus.APPROVED
    assert repo.list_pending() == []
    repo.reject("c1", "reason")
    assert repo.get("c1").status == InteractionStatus.REJECTED


def test_interaction_record_list_by_peer(tmp_path):
    repo = InteractionRecordRepository(Workspace(tmp_path))
    repo.upsert(InteractionRecord(
        id="rec_c1", proposal_id="c1", peer_id="p1", platform="x",
        source_url="u", published_body="b", occurred_at=datetime.now(UTC), outcome="published",
    ))
    assert [r.id for r in repo.list_by_peer("p1")] == ["rec_c1"]


def test_draft_frontmatter_roundtrip(tmp_path):
    repo = DraftRepository(Workspace(tmp_path))
    draft = Draft(
        id="draft_abc", kind=DraftKind.ORIGINAL, language="en",
        body="# Hi\n\n---\n\nBody.", content_job_id="idea_abc",
    )
    repo.upsert_draft(draft)
    got = repo.get_draft("draft_abc")
    assert got == draft
    assert [d.id for d in repo.list_by_job("idea_abc")] == ["draft_abc"]


def test_decision_get_by_job_id(tmp_path):
    repo = DecisionRecordRepository(Workspace(tmp_path))
    repo.save(DecisionRecord(
        id="dec_idea_abc", job_id="idea_abc", draft_id="draft_abc",
        action=DecisionAction.ACCEPT, approved_content_hash="h", decided_at=datetime.now(UTC),
    ))
    assert repo.get("idea_abc").id == "dec_idea_abc"


def test_publication_intent_get(tmp_path):
    repo = PublicationIntentRepository(Workspace(tmp_path))
    repo.save(PublicationIntent(
        source_type="draft", source_id="d1", approved_body="b", content_hash="h",
        approved_at=datetime.now(UTC), expected_kind="original",
    ))
    assert repo.get("d1").source_id == "d1"
```

### Step 3: Run the new test file

Run: `uv run pytest tests/unit/test_file_repositories.py -v`
Expected: PASS (8 tests). Fix any Pydantic construction mismatch (e.g. a required field on `DecisionRecord`/`PublicationIntent`) by supplying the missing field.

### Step 4: Rewire `cli.py`

1. Swap the import: `from .storage.database import Store` → `from .storage.workspace import Workspace`.
2. In each of the 35 command functions, replace the two lines:
   ```
       store = Store(settings.paths.db_path)
       store.init()
   ```
   with:
   ```
       ws = Workspace(settings.paths.var_dir)
       ws.ensure()
   ```
   then rename every remaining `store` → `ws` in that function. Affected: `ideas_commit`, `ideas_create`, `ideas_list`, `ideas_show`, `ideas_confirm`, `ideas_revise_position`, `ideas_skip`, `drafts_create`, `drafts_show`, `drafts_revise`, `learn`, `run_weekly`, `voice_approve_example` (8-space indent inside `else:`), `voice_reject_example`, `review_list`, `review_show`, `review_approve`, `review_revise`, `review_skip`, `connect_daily`, `connect_prepare`, `connect_approve`, `connect_reject`, `connect_edit`, `connect_record`, `peers_list`, `peers_show`, `conversations_list`, `conversations_show`, `conversations_follow_up`, `practice_start`, `practice_diagnose`, `practice_save`, `practice_finish`, `practice_show`.
3. Replace the whole `init` command with:
   ```python
   @app.command()
   def init() -> None:
       """初始化工作区目录树（幂等）。"""
       settings = load_settings()
       ws = Workspace(settings.paths.var_dir)
       ws.ensure()
       typer.echo(f"initialized: {settings.paths.var_dir}")
   ```
4. Delete the dead line `Store(settings.paths.db_path).init()` in `style_analyze`.
5. Update the two Store-typed helpers:
   ```python
   def _decision_service(ws: Workspace) -> InboxDecisionService:
       return InboxDecisionService(
           jobs=ContentJobRepository(ws),
           drafts=DraftRepository(ws),
           decisions=DecisionRecordRepository(ws),
           publication_intents=PublicationIntentRepository(ws),
           interactions=InteractionRepository(ws),
       )
   ```
   ```python
   def _persist_discovery(ws: Workspace, result: EngagementRunResult) -> None:
       peers = PeerRepository(ws)
       interactions = InteractionRepository(ws)
       for ranked in result.peers:
           peers.upsert(ranked.profile)
       for candidate in result.candidates:
           interactions.upsert(candidate, run_id=result.run_id)
   ```

### Step 5: Convert all test files

For every test file in the "Convert" list above, apply:

1. `from finch.storage.database import Store` → `from finch.storage.workspace import Workspace`.
2. `Store(tmp_path / "<anything>")` (or `Store(settings.paths.db_path)`) → `Workspace(tmp_path)` (or `Workspace(settings.paths.var_dir)`); drop any immediately following `.init()` (Workspace lazily creates dirs via `.dir()`; keep `.ensure()` only where the test explicitly needs the root created).
3. `<Repo>(store)` → `<Repo>(ws)` (rename the variable `store` → `ws` in those files).
4. In CLI test files, change the settings fixture: `Paths(db_path=tmp_path / "finch.db")` → `Paths(var_dir=tmp_path)`.
5. Remove now-deleted imports: `from sqlmodel import Session, select`, `from finch.storage.repositories import <X>Record`, and any `from finch.storage.database import Store`.
6. Rewrite DB-internal assertions (row counts via `Session(store.engine)`/`select`, or denormalized `record.platform`/`record.author_id` column reads) to assertions on the repository's public API (`len(repo.list_all())`, `repo.get(...).<field>`, `profile.platform_identities[0].platform`).

### Step 6: Delete the three dead DB tests

```bash
git rm tests/unit/test_database.py tests/unit/test_storage.py tests/unit/test_alembic.py
```

### Step 7: Green the full suite

Run: `uv run pytest`
Expected: PASS. Iterate on any residual failures — grep `grep -rn "Store\|db_path\|storage.database\|sqlmodel\|Session\|select\|<X>Record" src/ tests/` and fix every hit.

### Step 8: Lint + type-check

Run: `uv run ruff check . && uv run mypy src`
Expected: clean (mypy may flag the `_list_all`/`_read` generic bounds — a `# type: ignore[arg-type]` at the exact line is acceptable).

### Step 9: Commit

```bash
git add -A
git commit -m "refactor(storage): swap SQLite for file workspace (repositories + cli + tests)"
```

---

## Task 3: Delete database infrastructure

**Files:**
- Delete: `src/finch/storage/database.py`, `alembic/` (entire dir), `alembic.ini`
- Modify: `src/finch/settings.py`, `pyproject.toml`

- [ ] **Step 1:** `git rm src/finch/storage/database.py alembic.ini && git rm -r alembic/`
- [ ] **Step 2:** In `settings.py`, delete the `db_path` field and remove `self.db_path.parent` from `ensure()` (leaving `dirs = (self.var_dir, self.outputs_dir, self.inbox_dir, self.cache_dir)`).
- [ ] **Step 3:** In `pyproject.toml`, remove `"sqlmodel>=0.0.16"` and `"alembic>=1.13"` from deps, and remove the `extend-exclude = ["alembic/versions"]` ruff line (and its comment).
- [ ] **Step 4:** `uv sync`, then `grep -rn "sqlmodel\|sqlalchemy\|alembic\|storage.database\|db_path" src/ tests/` → no matches.
- [ ] **Step 5:** `uv run pytest && uv run ruff check . && uv run mypy src` → all clean.
- [ ] **Step 6:** Commit: `git add -A && git commit -m "chore: remove SQLite/SQLModel/Alembic infrastructure"`.

---

## Task 4: Projections + `finch context`

**Files:** Create `src/finch/projections.py`; Modify `src/finch/cli.py`; Create `tests/unit/test_projections.py`.

**Interfaces:** `build_daily_context(ws) -> dict`, `build_pending_actions(ws) -> dict`; CLI `finch context`.

- [ ] **Step 1:** Write `tests/unit/test_projections.py` (asserts `build_daily_context` returns `peers`/`ideas_awaiting_confirmation`/`pending_proposals`, and `build_pending_actions` returns the three keys `approved_unexecuted_proposals`/`ideas_awaiting_confirmation`/`drafts_awaiting_review`).
- [ ] **Step 2:** Run → FAIL (`No module named 'finch.projections'`).
- [ ] **Step 3:** Write `src/finch/projections.py`:

```python
"""确定性投影：从工作区只读聚合生成 projections/*.json（非事实源，可重建可删）。"""

from datetime import UTC, datetime

from finch.content.jobs import ContentJobStatus
from finch.conversations.service import ConversationService
from finch.engagement.models import InteractionStatus
from finch.inbox.service import list_items
from finch.storage.repositories import (
    ContentJobRepository,
    ConversationThreadRepository,
    DecisionRecordRepository,
    DraftRepository,
    EvidenceRepository,
    InteractionRepository,
    PeerRepository,
)
from finch.storage.workspace import Workspace


def build_daily_context(ws: Workspace) -> dict:
    now = datetime.now(UTC)
    threads = ConversationThreadRepository(ws).list_all()
    jobs = ContentJobRepository(ws).list_jobs()
    return {
        "peers": [p.model_dump(mode="json") for p in PeerRepository(ws).list_all()],
        "conversations_needing_follow_up": [
            t.model_dump(mode="json")
            for t in threads
            if ConversationService().needs_follow_up(t, now=now)
        ],
        "ideas_awaiting_confirmation": [
            j.model_dump(mode="json")
            for j in jobs
            if j.status == ContentJobStatus.PROPOSED
        ],
        "pending_proposals": [
            c.model_dump(mode="json") for c in InteractionRepository(ws).list_pending()
        ],
    }


def build_pending_actions(ws: Workspace) -> dict:
    jobs_repo = ContentJobRepository(ws)
    interactions = InteractionRepository(ws)
    return {
        "approved_unexecuted_proposals": [
            c.model_dump(mode="json")
            for c in interactions.list_all()
            if c.status == InteractionStatus.APPROVED
        ],
        "ideas_awaiting_confirmation": [
            j.model_dump(mode="json")
            for j in jobs_repo.list_jobs()
            if j.status == ContentJobStatus.PROPOSED
        ],
        "drafts_awaiting_review": [
            i.model_dump(mode="json")
            for i in list_items(
                jobs=jobs_repo,
                drafts=DraftRepository(ws),
                decisions=DecisionRecordRepository(ws),
                interactions=interactions,
                cards=EvidenceRepository(ws),
            )
        ],
    }
```

- [ ] **Step 4:** In `cli.py`, add `from .projections import build_daily_context, build_pending_actions` and the `context` command (writes `projections/daily-context.json` + `projections/pending-actions.json` via `ws.atomic_write`, `--json` prints both).
- [ ] **Step 5:** `uv run pytest tests/unit/test_projections.py -v` → PASS; `uv run finch context` → writes projections.
- [ ] **Step 6:** Commit: `git commit -m "feat(context): add projections + finch context"`.

---

## Task 5: `peers get` / `conversations get` aliases

**Files:** Modify `src/finch/cli.py`; Create `tests/unit/test_cli_get_aliases.py`.

- [ ] **Step 1:** Write `tests/unit/test_cli_get_aliases.py` (calls `peers_get("nope")` / `conversations_get("nope")`, asserts `typer.Exit(1)` and "peer not found" / "conversation not found").
- [ ] **Step 2:** Run → FAIL (`ImportError`).
- [ ] **Step 3:** In `cli.py`, add `peers_get` (delegates to `peers_show(peer_id, as_json=True)`) after `peers_show`, and `conversations_get` (delegates to `conversations_show(conversation_id, as_json=True)`) after `conversations_show`.
- [ ] **Step 4:** `uv run pytest tests/unit/test_cli_get_aliases.py -v` → PASS; `uv run finch peers get nope` → exit 1.
- [ ] **Step 5:** Commit: `git commit -m "feat(cli): add peers get / conversations get agent aliases"`.

---

## Task 6: Full verification

- [ ] **Step 1:** `uv run pytest` → PASS.
- [ ] **Step 2:** `uv run ruff check .` → clean.
- [ ] **Step 3:** `uv run mypy src` → clean.
- [ ] **Step 4:** Manual smoke: `uv run finch init`, `uv run finch context`, `uv run finch peers list`, `uv run finch ideas list` — each prints without error; no `.db` file created under `var/`.
- [ ] **Step 5:** Final commit if needed.

---

## Self-Review Notes (for the implementer)

- **Spec coverage:** D1 (full bundle) = Tasks 1–6; D2 (fresh start) = Task 3 + Task 6 smoke; D3 (atomic write) = Task 1; D4/D6 (YAML/mixed + frontmatter draft) = Task 1/2; D5 (`var/` root) = Task 2/4; D7 (keep interfaces) = Task 2; D8 (filename = domain id) = Task 2.
- **Behavior preserved:** `connect record` still only writes `InteractionRecord` (does NOT update `ConversationThread`); conversation `events.jsonl` is intentionally omitted (no current writer — future spec). `weekly-summary.json` is covered by existing `finch weekly --json`, not duplicated.
- **Type consistency:** `Workspace.read_yaml`/`_read`/`_list_all` use `T = TypeVar("T", bound=BaseModel)`; `_list_all` filters `None` (corrupt files raise `ValidationError`/`YAMLError`, matching current behavior for non-ContentJob repos; ContentJob skips-and-reports).
