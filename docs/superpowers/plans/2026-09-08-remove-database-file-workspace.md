# Remove Database → File Workspace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace SQLite/SQLModel/Alembic with a file workspace (`Workspace`), preserving every existing command/service/repository-method behavior, then add agent-first `finch context` + projections.

**Architecture:** A thin `Workspace` (filesystem root + atomic write + YAML/frontmatter/JSONL helpers) replaces `Store`. All ~20 repository classes keep their exact public method signatures, only swapping `Session.merge/commit` for file read/write and changing the constructor arg `Store` → `Workspace`. Domain models stay Pydantic; filenames are the existing domain IDs.

**Tech Stack:** Python 3.12, Pydantic 2, PyYAML (already a dep), Typer. Removes: sqlmodel, sqlalchemy, alembic.

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

**Files:**
- Create: `src/finch/storage/workspace.py`
- Test: `tests/unit/test_workspace.py`

**Interfaces:**
- Produces: `Workspace(root)`, `.ensure()`, `.dir(name)`, `.safe_filename(name)`, `.atomic_write(path, text)`, `.write_yaml(path, model)`, `.read_yaml(path, Model)`, `.write_frontmatter(path, meta, body)`, `.read_frontmatter(path) -> (meta, body)`, `.append_jsonl(path, obj)`, `.read_jsonl(path)`.

- [ ] **Step 1: Write the failing test**

`tests/unit/test_workspace.py`:

```python
"""Workspace 原子写 / YAML / frontmatter / JSONL 原语测试。"""

from datetime import UTC, datetime

import pytest

from finch.peers.models import PeerProfile, PlatformIdentity
from finch.storage.workspace import Workspace


def _profile() -> PeerProfile:
    return PeerProfile(
        id="p1",
        platform_identities=[PlatformIdentity(platform="x", author_id="alice")],
        last_meaningful_interaction_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
    )


def test_yaml_roundtrip(tmp_path):
    ws = Workspace(tmp_path)
    model = _profile()
    p = tmp_path / "p.yaml"
    ws.write_yaml(p, model)
    assert ws.read_yaml(p, PeerProfile) == model


def test_yaml_write_idempotent_bytes(tmp_path):
    ws = Workspace(tmp_path)
    p = tmp_path / "p.yaml"
    ws.write_yaml(p, _profile())
    first = p.read_text()
    ws.write_yaml(p, _profile())
    assert p.read_text() == first


def test_read_yaml_missing_returns_none(tmp_path):
    ws = Workspace(tmp_path)
    assert ws.read_yaml(tmp_path / "nope.yaml", PeerProfile) is None


def test_atomic_write_leaves_no_tmp(tmp_path):
    ws = Workspace(tmp_path)
    p = tmp_path / "x.txt"
    ws.atomic_write(p, "hello")
    assert p.read_text() == "hello"
    assert not (tmp_path / "x.txt.tmp").exists()


def test_frontmatter_roundtrip_preserves_body_horizontal_rule(tmp_path):
    ws = Workspace(tmp_path)
    meta = {"id": "d1", "kind": "original", "candidate_id": None}
    body = "# Title\n\nSome text.\n\n---\n\nMore."
    p = tmp_path / "draft.md"
    ws.write_frontmatter(p, meta, body)
    m2, b2 = ws.read_frontmatter(p)
    assert m2 == meta
    assert b2 == body


def test_safe_filename_rejects_path_traversal(tmp_path):
    ws = Workspace(tmp_path)
    with pytest.raises(ValueError):
        ws.safe_filename("../evil")


def test_safe_filename_maps_colon(tmp_path):
    ws = Workspace(tmp_path)
    assert ws.safe_filename("intent:abc") == "intent_abc"


def test_jsonl_append_and_read(tmp_path):
    ws = Workspace(tmp_path)
    p = tmp_path / "critic.jsonl"
    ws.append_jsonl(p, {"round": 0, "outcome": "pass"})
    ws.append_jsonl(p, {"round": 1, "outcome": "rewrite"})
    assert ws.read_jsonl(p) == [
        {"round": 0, "outcome": "pass"},
        {"round": 1, "outcome": "rewrite"},
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_workspace.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'finch.storage.workspace'`

- [ ] **Step 3: Write the implementation**

`src/finch/storage/workspace.py`:

```python
"""文件工作区：确定性原子读写 + YAML / frontmatter-Markdown / JSONL 序列化。

替换 ``Store``（SQLite）。单用户本地 CLI，原子写（临时文件 + ``os.replace``）是
唯一一致性机制；不设文件锁。领域模型仍用 Pydantic 校验读写。
"""

import json
import os
from pathlib import Path
from typing import TypeVar

import yaml
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

_FRONTMATTER = "---"


class Workspace:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def ensure(self) -> None:
        """幂等创建根目录。"""
        self.root.mkdir(parents=True, exist_ok=True)

    def dir(self, name: str) -> Path:
        """创建并返回工作区子目录（支持 ``a/b`` 嵌套路径）。"""
        d = self.root / name
        d.mkdir(parents=True, exist_ok=True)
        return d

    @staticmethod
    def safe_filename(name: str) -> str:
        """文件名消毒：拒绝路径穿越，``:`` 映射为 ``_``（复合 id 兜底）。"""
        if ".." in name or "/" in name or "\x00" in name:
            raise ValueError(f"unsafe filename: {name!r}")
        return name.replace(":", "_")

    def atomic_write(self, path: Path, text: str) -> None:
        """写临时文件后 ``os.replace`` 原子替换（同目录保证原子性）。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)

    def write_yaml(self, path: Path, model: BaseModel) -> None:
        """领域模型 → YAML（``mode="json"`` 保证 datetime/StrEnum/None 稳定）。"""
        self.atomic_write(
            path,
            yaml.safe_dump(model.model_dump(mode="json"), sort_keys=False, allow_unicode=True),
        )

    def read_yaml(self, path: Path, model_cls: type[T]) -> T | None:
        """YAML → 领域模型；缺文件返回 None；损坏抛 ValidationError / YAMLError。"""
        if not path.exists():
            return None
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return model_cls.model_validate(data)

    def write_frontmatter(self, path: Path, meta: dict, body: str) -> None:
        """写 frontmatter Markdown：``---`` 元数据 ``---`` 正文（单文件单真相单原子写）。"""
        meta_yaml = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True)
        self.atomic_write(path, f"{_FRONTMATTER}\n{meta_yaml}{_FRONTMATTER}\n{body}")

    def read_frontmatter(self, path: Path) -> tuple[dict, str]:
        """读 frontmatter Markdown → (元数据 dict, 正文 str)。

        只按前两个 ``---`` 行切分，正文中的 ``---``（如 Markdown 分隔线）不受影响；
        正文尾部换行在读时规范化（不保留）。
        """
        lines = path.read_text(encoding="utf-8").split("\n")
        close = next(i for i in range(1, len(lines)) if lines[i] == _FRONTMATTER)
        meta = yaml.safe_load("\n".join(lines[1:close])) or {}
        return meta, "\n".join(lines[close + 1 :])

    def append_jsonl(self, path: Path, obj: dict) -> None:
        """追加一行 JSONL（保序）。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")

    def read_jsonl(self, path: Path) -> list[dict]:
        """读全部 JSONL 行；缺文件返回空列表。"""
        if not path.exists():
            return []
        out: list[dict] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))
        return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_workspace.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add src/finch/storage/workspace.py tests/unit/test_workspace.py
git commit -m "feat(storage): add Workspace (atomic write + YAML/frontmatter/JSONL)"
```

---

## Task 2: Rewrite `repositories.py` to file-based (keep interfaces)

**Files:**
- Rewrite: `src/finch/storage/repositories.py`
- Test: `tests/unit/test_file_repositories.py`

**Interfaces:**
- Consumes: `Workspace` from Task 1.
- Produces: the same 16 repository classes with identical method signatures (see code). Filename = domain id; lookups are glob + filter.

- [ ] **Step 1: Write the failing test**

`tests/unit/test_file_repositories.py`:

```python
"""文件仓储行为契约：upsert/get 往返、幂等、二级查找、状态转换。"""

from datetime import UTC, datetime

import pytest

from finch.author.models import PublicationIntent
from finch.conversations.models import ConversationThread
from finch.content.jobs import ContentJob, ContentJobStatus
from finch.content.models import Draft, DraftKind, RecommendedFormat
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

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_file_repositories.py -v`
Expected: FAIL — `ImportError` (repositories.py still imports `Store`) or `TypeError` (repos expect `Store`, not `Workspace`). If `InteractionProposal(...)` raises a Pydantic validation error, add the required fields (open `src/finch/engagement/models.py` and supply them).

- [ ] **Step 3: Write the implementation**

Rewrite `src/finch/storage/repositories.py` (full replacement):

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
    """EvidenceCard 仓储（C5）。"""

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
    """Draft 仓储（frontmatter Markdown，单文件单真相）。"""

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
    """DecisionRecord 仓储。"""

    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def save(self, record: DecisionRecord) -> None:
        _write(self.ws, "decisions", record.id, record)

    def get(self, job_id: str) -> DecisionRecord | None:
        return _read(self.ws, "decisions", f"dec_{job_id}", DecisionRecord)

    def list(self) -> list[DecisionRecord]:
        return _list_all(self.ws, "decisions", DecisionRecord)


class FeedbackRepository:
    """Feedback 仓储（按 draft_id 幂等）。"""

    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def save_feedback(self, feedback: Feedback) -> None:
        _write(self.ws, "feedback", feedback.draft_id, feedback)

    def get_feedback(self, draft_id: str) -> Feedback | None:
        return _read(self.ws, "feedback", draft_id, Feedback)

    def list_feedbacks(self) -> list[Feedback]:
        return _list_all(self.ws, "feedback", Feedback)


class ContentJobRepository:
    """ContentJob 仓储（idea 状态机；跳过不可解析行并暴露失败 id）。"""

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
    """草稿版本仓储（frontmatter Markdown，按 round 排序）。"""

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
    """Critic 报告仓储（JSONL，每轮一行；ts 供时间窗过滤）。"""

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
    """互动候选审批队列仓储（状态机：PROPOSED→APPROVED/REJECTED/EXECUTED）。"""

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
    """互动反馈快照仓储。"""

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
    """conversation 证据仓储。"""

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
    """互动轨道运行级计数仓储。"""

    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def upsert(self, stats: EngagementRunStats) -> None:
        _write(self.ws, "interactions/run-stats", stats.run_id, stats)

    def list_all(self) -> list[EngagementRunStats]:
        return _list_all(self.ws, "interactions/run-stats", EngagementRunStats)


class PublicationIntentRepository:
    """发布意图仓储。"""

    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def save(self, intent: PublicationIntent) -> None:
        _write(self.ws, "publication-intents", f"intent_{intent.source_id}", intent)

    def get(self, source_id: str) -> PublicationIntent | None:
        return _read(self.ws, "publication-intents", f"intent_{source_id}", PublicationIntent)

    def list(self) -> list[PublicationIntent]:
        return _list_all(self.ws, "publication-intents", PublicationIntent)


class PracticeSessionRepository:
    """表达练习会话仓储。"""

    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def upsert(self, session: PracticeSession) -> None:
        _write(self.ws, "practice", session.id, session)

    def get(self, session_id: str) -> PracticeSession | None:
        return _read(self.ws, "practice", session_id, PracticeSession)


class PeerRepository:
    """PeerProfile 仓储：按 id 幂等 upsert。"""

    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def upsert(self, profile: PeerProfile) -> None:
        _write(self.ws, "peers", profile.id, profile)

    def get(self, peer_id: str) -> PeerProfile | None:
        return _read(self.ws, "peers", peer_id, PeerProfile)

    def list_all(self) -> list[PeerProfile]:
        return _list_all(self.ws, "peers", PeerProfile)


class InteractionRecordRepository:
    """已发生互动仓储（独立事实）。"""

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
    """对话线索仓储。"""

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

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_file_repositories.py -v`
Expected: PASS. Fix any `InteractionProposal` required-field additions discovered in Step 2.

- [ ] **Step 5: Commit**

```bash
git add src/finch/storage/repositories.py tests/unit/test_file_repositories.py
git commit -m "refactor(storage): rewrite repositories to file-based (keep interfaces)"
```

---

## Task 3: Convert existing repository unit tests

**Files:**
- Modify: `tests/unit/test_peer_repositories.py`, `tests/unit/test_author_repositories.py`, `tests/unit/test_feedback_repository.py`, `tests/unit/test_interaction_repository.py`, `tests/unit/test_interaction_record_repository.py`, `tests/unit/test_repositories.py`

**Interfaces:**
- Consumes: `Workspace`, file-based repositories (Task 2).

These tests fall into two kinds:
1. **Behavioral** (upsert/get roundtrip, idempotency, list filters) — only the fixture changes.
2. **DB-internal** (assert row counts via `Session`/`select` on `*Record`, or read `platform`/`author_id` columns) — rewrite to assert on the repository public API.

- [ ] **Step 1: Apply the mechanical fixture swap to every file**

For each of the six files, apply these three edits:

```
- from finch.storage.database import Store
+ from finch.storage.workspace import Workspace
```
```
- store = Store(tmp_path / "<anything>")
- store.init()
+ ws = Workspace(tmp_path)
```
```
- <Repo>(store)
+ <Repo>(ws)
```

And remove now-deleted imports: `from sqlmodel import Session, select` and any `from finch.storage.repositories import <X>Record` (the `*Record` classes are gone).

- [ ] **Step 2: Rewrite DB-internal assertions to public-API assertions**

For each test that inspects the DB directly, replace with a public-API assertion. Examples:

In `test_peer_repositories.py`, a test named `test_same_author_across_posts_keeps_one_row` currently counts rows via `Session(store.engine)` and `select(PeerRecord)`. Replace the row-count block with:

```python
assert len(repo.list_all()) == 1
assert repo.list_all()[0].display_name == "Alice Updated"
```

Any test asserting `record.platform == "x"` / `record.author_id == "alice"` (denormalized columns) should instead assert on the returned `PeerProfile.platform_identities[0]`.

- [ ] **Step 3: Run the converted tests**

Run: `uv run pytest tests/unit/test_peer_repositories.py tests/unit/test_author_repositories.py tests/unit/test_feedback_repository.py tests/unit/test_interaction_repository.py tests/unit/test_interaction_record_repository.py tests/unit/test_repositories.py -v`
Expected: PASS (all converted tests green). Fix any remaining `Store`/`*Record`/`Session` references the grep in Step 4 reveals.

- [ ] **Step 4: Confirm no residual DB references**

Run: `grep -rn "Store\|Session\|select\|Record" tests/unit/test_peer_repositories.py tests/unit/test_author_repositories.py tests/unit/test_feedback_repository.py tests/unit/test_interaction_repository.py tests/unit/test_interaction_record_repository.py tests/unit/test_repositories.py`
Expected: no matches.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_peer_repositories.py tests/unit/test_author_repositories.py tests/unit/test_feedback_repository.py tests/unit/test_interaction_repository.py tests/unit/test_interaction_record_repository.py tests/unit/test_repositories.py
git commit -m "test(storage): convert repository tests to file workspace"
```

---

## Task 4: Rewire `cli.py` (Store → Workspace)

**Files:**
- Modify: `src/finch/cli.py`

**Interfaces:**
- Consumes: `Workspace` (Task 1), file repositories (Task 2).

- [ ] **Step 1: Swap the top-level import**

```
- from .storage.database import Store
+ from .storage.workspace import Workspace
```

- [ ] **Step 2: Apply the mechanical pattern to every command body**

There are 35 command functions that contain exactly:

```python
    store = Store(settings.paths.db_path)
    store.init()
```

Replace with:

```python
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
```

Then, within each such function, rename every remaining `store` → `ws` (repository constructors and helper calls). The affected functions are, by line: `ideas_commit` (299), `ideas_create` (347), `ideas_list` (378), `ideas_show` (406), `ideas_confirm` (425), `ideas_revise_position` (450), `ideas_skip` (481), `drafts_create` (549), `drafts_show` (590), `drafts_revise` (610), `learn` (697), `run_weekly` (741), `voice_approve_example` (825, 8-space indent inside `else:`), `voice_reject_example` (901), `review_list` (948), `review_show` (987), `review_approve` (1025), `review_revise` (1052), `review_skip` (1086), `connect_daily` (1164), `connect_prepare` (1204), `connect_approve` (1226), `connect_reject` (1243), `connect_edit` (1260), `connect_record` (1283), `peers_list` (1313), `peers_show` (1337), `conversations_list` (1371), `conversations_show` (1397), `conversations_follow_up` (1424), `practice_start` (1457), `practice_diagnose` (1478), `practice_save` (1507), `practice_finish` (1535), `practice_show` (1562).

Verify coverage afterward with: `grep -n "Store\|store\b" src/finch/cli.py` (expected: zero remaining, after Step 3–5 below).

- [ ] **Step 3: Rewrite `init` (drops prune)**

Replace the whole `init` command (lines 223–244) with:

```python
@app.command()
def init() -> None:
    """初始化工作区目录树（幂等）。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    typer.echo(f"initialized: {settings.paths.var_dir}")
```

- [ ] **Step 4: Remove the dead `Store` line in `style_analyze`**

In `style_analyze` (line ~1595), delete this line (it was a no-op schema init; style analysis persists nothing):

```python
    Store(settings.paths.db_path).init()
```

- [ ] **Step 5: Update the two Store-typed helper signatures**

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

(`_draft_job_id` already takes `DraftRepository` and needs no change.)

- [ ] **Step 6: Run CLI smoke test (import + help)**

Run: `uv run finch --help`
Expected: prints help without `ImportError`. Then `uv run mypy src/finch/cli.py`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add src/finch/cli.py
git commit -m "refactor(cli): wire Workspace instead of Store"
```

---

## Task 5: Convert CLI tests + delete dead DB tests

**Files:**
- Modify: `tests/unit/test_cli_run.py`, `tests/unit/test_cli_review_engagement.py`, `tests/unit/test_cli_connect.py`, `tests/unit/test_cli_ideas.py`, `tests/unit/test_cli_drafts.py`, `tests/unit/test_cli_learn.py`, `tests/unit/test_cli_practice.py`, `tests/unit/test_cli_style.py`, `tests/unit/test_cli_voice.py`, `tests/unit/test_connection_loop_e2e.py`
- Delete: `tests/unit/test_database.py`, `tests/unit/test_storage.py`, `tests/unit/test_alembic.py`

- [ ] **Step 1: Apply the fixture swap to every CLI test file**

Same three edits as Task 3 Step 1:
- `from finch.storage.database import Store` → `from finch.storage.workspace import Workspace`
- `Store(settings.paths.db_path)` → `Workspace(settings.paths.var_dir)` and `Store(tmp_path / "...")` → `Workspace(tmp_path)`
- `.init()` → `.ensure()`

For files that construct `Store(tmp_path / "db.sqlite")` in a helper (e.g. `test_connection_loop_e2e.py`), change the helper body to `return Workspace(tmp_path)`.

- [ ] **Step 2: Delete dead DB-specific test files**

```bash
git rm tests/unit/test_database.py tests/unit/test_storage.py tests/unit/test_alembic.py
```

(`test_storage.py` asserts the WAL pragma, `test_database.py` asserts schema-drift pruning, `test_alembic.py` asserts migrations — all three concepts disappear with SQLite.)

- [ ] **Step 3: Run the converted CLI tests**

Run: `uv run pytest tests/unit/test_cli_run.py tests/unit/test_cli_review_engagement.py tests/unit/test_cli_connect.py tests/unit/test_cli_ideas.py tests/unit/test_cli_drafts.py tests/unit/test_cli_learn.py tests/unit/test_cli_practice.py tests/unit/test_cli_style.py tests/unit/test_cli_voice.py tests/unit/test_connection_loop_e2e.py -v`
Expected: PASS. Fix any remaining `Store`/`db_path` references surfaced by:
`grep -rn "Store\|db_path\|\.init()" tests/unit/` (expected: no matches).

- [ ] **Step 4: Commit**

```bash
git add tests/unit/
git commit -m "test(cli): convert CLI tests to file workspace; drop DB tests"
```

---

## Task 6: Delete database infrastructure

**Files:**
- Delete: `src/finch/storage/database.py`, `alembic/` (entire dir), `alembic.ini`
- Modify: `src/finch/settings.py`, `pyproject.toml`

- [ ] **Step 1: Delete the DB files**

```bash
git rm src/finch/storage/database.py alembic.ini
git rm -r alembic/
```

- [ ] **Step 2: Remove `db_path` from `settings.py`**

In `src/finch/settings.py`, delete the field:

```python
    db_path: Path = Field(default_factory=lambda: Path("var/finch.db"))
```

and remove `self.db_path.parent` from the `ensure()` dirs tuple:

```python
    def ensure(self) -> "Paths":
        dirs = (self.var_dir, self.outputs_dir, self.inbox_dir, self.cache_dir)
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)
        return self
```

- [ ] **Step 3: Drop the dependencies and the alembic lint exclude**

In `pyproject.toml`, remove these dependency lines:

```toml
    "sqlmodel>=0.0.16",
    "alembic>=1.13",
```

and remove the `extend-exclude = ["alembic/versions"]` line (and its comment) from the ruff config.

- [ ] **Step 4: Re-sync deps and verify nothing imports the removed modules**

Run: `uv sync`
Run: `grep -rn "sqlmodel\|sqlalchemy\|alembic\|storage.database\|from .storage import Store\|db_path" src/ tests/`
Expected: no matches (except any legitimate unrelated mention in docs).

- [ ] **Step 5: Run lint + type-check**

Run: `uv run ruff check . && uv run mypy src`
Expected: clean (mypy may flag `Workspace` generic-typing edges — fix with a `# type: ignore[arg-type]` if `_list_all`/`_read` generic bounds complain; keep it minimal).

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "chore: remove SQLite/SQLModel/Alembic infrastructure"
```

---

## Task 7: Projections + `finch context`

**Files:**
- Create: `src/finch/projections.py`
- Modify: `src/finch/cli.py`
- Test: `tests/unit/test_projections.py`

**Interfaces:**
- Produces: `build_daily_context(ws: Workspace) -> dict`, `build_pending_actions(ws: Workspace) -> dict`; CLI command `finch context`.

- [ ] **Step 1: Write the failing test**

`tests/unit/test_projections.py`:

```python
"""投影生成：确定性只读聚合 → daily-context / pending-actions。"""

from finch.content.jobs import ContentJob, ContentJobStatus
from finch.peers.models import PeerProfile, PlatformIdentity
from finch.projections import build_daily_context, build_pending_actions
from finch.storage.repositories import ContentJobRepository, PeerRepository
from finch.storage.workspace import Workspace


def test_build_daily_context(tmp_path):
    ws = Workspace(tmp_path)
    PeerRepository(ws).upsert(
        PeerProfile(id="p1", platform_identities=[PlatformIdentity(platform="x", author_id="a")])
    )
    ContentJobRepository(ws).upsert_job(
        ContentJob(
            id="idea_abc", source_card_ids=[], reader_problem="r",
            recommended_format="short_post", status=ContentJobStatus.PROPOSED,
        )
    )
    daily = build_daily_context(ws)
    assert [p["id"] for p in daily["peers"]] == ["p1"]
    assert [j["id"] for j in daily["ideas_awaiting_confirmation"]] == ["idea_abc"]
    assert "pending_proposals" in daily


def test_build_pending_actions(tmp_path):
    ws = Workspace(tmp_path)
    pending = build_pending_actions(ws)
    assert set(pending) == {
        "approved_unexecuted_proposals",
        "ideas_awaiting_confirmation",
        "drafts_awaiting_review",
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_projections.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'finch.projections'`

- [ ] **Step 3: Write the implementation**

`src/finch/projections.py`:

```python
"""确定性投影：从工作区只读聚合生成 projections/*.json（非事实源，可重建可删）。

供 Agent 读取「今天相关」的窄上下文，避免扫描全历史。投影由 Python 生成、原子写，
不参与领域状态判定。
"""

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
    """当日上下文：同行 + 需跟进对话 + 待确认 idea + 待处理提案。"""
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
    """待办：已批准未执行提案 + 待确认 idea + 待审草稿。"""
    jobs_repo = ContentJobRepository(ws)
    interactions = InteractionRepository(ws)
    approved_unexecuted = [
        c.model_dump(mode="json")
        for c in interactions.list_all()
        if c.status == InteractionStatus.APPROVED
    ]
    ideas = [
        j.model_dump(mode="json")
        for j in jobs_repo.list_jobs()
        if j.status == ContentJobStatus.PROPOSED
    ]
    items = list_items(
        jobs=jobs_repo,
        drafts=DraftRepository(ws),
        decisions=DecisionRecordRepository(ws),
        interactions=interactions,
        cards=EvidenceRepository(ws),
    )
    return {
        "approved_unexecuted_proposals": approved_unexecuted,
        "ideas_awaiting_confirmation": ideas,
        "drafts_awaiting_review": [i.model_dump(mode="json") for i in items],
    }
```

- [ ] **Step 4: Add the `finch context` command**

In `src/finch/cli.py`, add the import `from .projections import build_daily_context, build_pending_actions` and the command:

```python
@app.command()
def context(as_json: bool = typer.Option(False, "--json", help="输出 JSON")) -> None:
    """生成当日上下文投影并写入 projections/（可重建，非事实源）。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    daily = build_daily_context(ws)
    pending = build_pending_actions(ws)
    proj = ws.dir("projections")
    ws.atomic_write(proj / "daily-context.json", json.dumps(daily, ensure_ascii=False, indent=2))
    ws.atomic_write(
        proj / "pending-actions.json", json.dumps(pending, ensure_ascii=False, indent=2)
    )
    if as_json:
        typer.echo(json.dumps({"daily": daily, "pending": pending}, ensure_ascii=False, indent=2))
    else:
        typer.echo(f"projections written to {proj}")
```

(`finch context daily` from the spec maps to this single `finch context`, which always includes daily-context.)

- [ ] **Step 5: Run tests + smoke test**

Run: `uv run pytest tests/unit/test_projections.py -v`
Expected: PASS.
Run: `uv run finch context`
Expected: prints `projections written to var/projections` and creates the two JSON files.

- [ ] **Step 6: Commit**

```bash
git add src/finch/projections.py src/finch/cli.py tests/unit/test_projections.py
git commit -m "feat(context): add projections + finch context"
```

---

## Task 8: `peers get` / `conversations get` aliases

**Files:**
- Modify: `src/finch/cli.py`
- Test: `tests/unit/test_cli_get_aliases.py`

- [ ] **Step 1: Write the failing test**

`tests/unit/test_cli_get_aliases.py`:

```python
"""Agent 读取别名：peers get / conversations get 默认 JSON。"""

from finch.cli import peers_get, conversations_get


def test_peers_get_missing(tmp_path, capsys):
    # 无 peer 时输出 not found 并退出码 1
    import typer

    from finch.cli import peers_show

    try:
        peers_show("nope", as_json=True)
    except typer.Exit as exc:
        assert exc.exit_code == 1
    out = capsys.readouterr().out
    assert "peer not found" in out
```

Note: these alias commands call the existing `peers_show` / `conversations_show` directly. If `typer` callback signature quirks make a full CLI-run test awkward, assert the alias delegates by calling `peers_get(peer_id="nope")` and expecting a `typer.Exit(1)`.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_cli_get_aliases.py -v`
Expected: FAIL with `ImportError` (no `peers_get`/`conversations_get`).

- [ ] **Step 3: Add the aliases**

In `src/finch/cli.py`, add after `peers_show`:

```python
@peers_app.command("get")
def peers_get(peer_id: str = typer.Argument(..., help="peer id")) -> None:
    """Agent 用确定性读取：等同 peers show --json。"""
    peers_show(peer_id, as_json=True)
```

and after `conversations_show`:

```python
@conversations_app.command("get")
def conversations_get(conversation_id: str = typer.Argument(..., help="conversation id")) -> None:
    """Agent 用确定性读取：等同 conversations show --json。"""
    conversations_show(conversation_id, as_json=True)
```

- [ ] **Step 4: Run test + smoke test**

Run: `uv run pytest tests/unit/test_cli_get_aliases.py -v`
Expected: PASS.
Run: `uv run finch peers get nope`
Expected: `peer not found: nope` with exit code 1.

- [ ] **Step 5: Commit**

```bash
git add src/finch/cli.py tests/unit/test_cli_get_aliases.py
git commit -m "feat(cli): add peers get / conversations get agent aliases"
```

---

## Task 9: Full verification

- [ ] **Step 1: Full test suite**

Run: `uv run pytest`
Expected: PASS (all tests green).

- [ ] **Step 2: Lint + format**

Run: `uv run ruff check .`
Expected: clean.

- [ ] **Step 3: Type-check**

Run: `uv run mypy src`
Expected: clean.

- [ ] **Step 4: Manual end-to-end smoke**

```bash
uv run finch init          # → initialized: var
uv run finch context       # → projections written to var/projections
uv run finch peers list    # → no peers (fresh start)
uv run finch ideas list    # → empty (fresh start)
```

Expected: each prints without error; no `.db` file is created anywhere under `var/`.

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "refactor(storage): remove database, adopt file workspace + agent context"
```

---

## Self-Review Notes (for the implementer)

- **Spec coverage:** D1 (full bundle) = Tasks 1–9; D2 (fresh start) = Task 6 + Task 9 manual smoke; D3 (atomic write) = Task 1; D4/D6 (YAML/mixed + frontmatter draft) = Task 1/2; D5 (`var/` root) = Task 4/7; D7 (keep interfaces) = Task 2; D8 (filename = domain id) = Task 2.
- **Behavior preserved:** `connect record` still only writes `InteractionRecord` (does NOT update `ConversationThread`); conversation `events.jsonl` is intentionally omitted (no current writer — future spec). `weekly-summary.json` is covered by existing `finch weekly --json`, not duplicated.
- **Type consistency:** `Workspace.read_yaml`/`_read`/`_list_all` use `T = TypeVar("T", bound=BaseModel)`; `_list_all` filters `None` (corrupt files raise `ValidationError`/`YAMLError`, matching current behavior for non-ContentJob repos; ContentJob skips-and-reports).
