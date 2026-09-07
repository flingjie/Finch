"""Tests for DraftService (Skill 架构 Step 1 领域核心：draft 幂等键 + 骨架)。"""

from finch.content.jobs import AuthorPosition, ContentJob, ContentJobStatus, IntendedEffect
from finch.content.models import Draft, DraftKind
from finch.drafts.service import DraftService, draft_generation_key


class FakeDraftRepository:
    """In-memory double for DraftRepository (upsert_draft / get_draft / list_by_job)."""

    def __init__(self) -> None:
        self._by_id: dict[str, Draft] = {}

    def upsert_draft(self, draft: Draft) -> None:
        self._by_id[draft.id] = draft

    def get_draft(self, draft_id: str) -> Draft | None:
        return self._by_id.get(draft_id)

    def list_drafts(self) -> list[Draft]:
        return list(self._by_id.values())

    def list_by_job(self, job_id: str) -> list[Draft]:
        return [d for d in self._by_id.values() if d.content_job_id == job_id]


class FakeCriticReportRepository:
    """Minimal double for CriticReportRepository (DraftService 本任务不调用它)。"""

    def upsert_report(self, *args: object, **kwargs: object) -> None:
        return None

    def list_reports(self, *args: object, **kwargs: object) -> list[dict]:
        return []

    def list_all_reports(self, *args: object, **kwargs: object) -> dict[str, list[dict]]:
        return {}


def _idea(**overrides: object) -> ContentJob:
    data: dict[str, object] = dict(
        id="idea_abc123",
        source_card_ids=[],
        candidate_id=None,
        reader_problem="很多人只把 Graph 当可视化",
        audience="",
        intended_effect=IntendedEffect(understand="Graph 的价值是恢复与重放"),
        author_position=AuthorPosition(
            claim="Graph 主要价值是恢复与重放",
            decision="用可恢复性评价 Graph",
            tradeoff="需要持久化状态",
            change_mind_if=None,
        ),
        success_criteria=[],
        recommended_format=DraftKind.ORIGINAL,
        status=ContentJobStatus.CONFIRMED,
        core_message="Graph 的价值是恢复与重放",
        content_fingerprint="fp_abc123",
    )
    data.update(overrides)
    return ContentJob(**data)  # type: ignore[arg-type]


def _service() -> tuple[DraftService, FakeDraftRepository]:
    repo = FakeDraftRepository()
    return DraftService(repo, FakeCriticReportRepository()), repo


# ---- draft_generation_key ----


def test_draft_generation_key_is_deterministic():
    a = draft_generation_key("fp", "0.1.0", "original", "v1")
    b = draft_generation_key("fp", "0.1.0", "original", "v1")
    assert a == b
    assert len(a) == 64  # sha256 hex


def test_draft_generation_key_sensitive_to_each_field():
    base = ("fp", "0.1.0", "original", "v1")
    variants = [
        ("other-fp", "0.1.0", "original", "v1"),
        ("fp", "0.2.0", "original", "v1"),
        ("fp", "0.1.0", "reply", "v1"),
        ("fp", "0.1.0", "original", "v2"),
    ]
    base_key = draft_generation_key(*base)
    assert all(draft_generation_key(*v) != base_key for v in variants)


# ---- create ----


def test_create_builds_skeleton_draft():
    svc, repo = _service()
    draft = svc.create(_idea(), version="0.1.0", format="original", voice_version="v1")
    assert draft.id == f"draft_{draft_generation_key('fp_abc123', '0.1.0', 'original', 'v1')[:16]}"
    assert draft.kind == DraftKind.ORIGINAL
    assert draft.candidate_id is None
    assert draft.language == "zh"
    assert draft.body == ""
    assert draft.claims == []
    assert draft.content_job_id == "idea_abc123"
    assert draft.position_statement == "用可恢复性评价 Graph"
    assert draft.run_id == "idea"
    assert repo.get_draft(draft.id) is not None


def test_create_is_idempotent_by_generation_key():
    svc, repo = _service()
    first = svc.create(_idea(), version="0.1.0", format="original", voice_version="v1")
    second = svc.create(_idea(), version="0.1.0", format="original", voice_version="v1")
    assert first.id == second.id
    assert first == second
    assert len(repo._by_id) == 1


def test_create_reply_kind_from_recommended_format():
    svc, _ = _service()
    draft = svc.create(
        _idea(recommended_format=DraftKind.REPLY),
        version="0.1.0",
        format="reply",
        voice_version="v1",
    )
    assert draft.kind == DraftKind.REPLY


def test_create_distinguishes_format_in_key():
    svc, _ = _service()
    a = svc.create(_idea(), version="0.1.0", format="original", voice_version="v1")
    b = svc.create(_idea(), version="0.1.0", format="reply", voice_version="v1")
    assert a.id != b.id


def test_create_distinguishes_version_in_key():
    svc, _ = _service()
    a = svc.create(_idea(), version="0.1.0", format="original", voice_version="v1")
    b = svc.create(_idea(), version="0.2.0", format="original", voice_version="v1")
    assert a.id != b.id


def test_create_falls_back_to_core_message_fingerprint():
    import hashlib

    svc, _ = _service()
    idea = _idea(content_fingerprint=None)
    fingerprint = hashlib.sha256(idea.core_message.encode("utf-8")).hexdigest()
    expected_id = f"draft_{draft_generation_key(fingerprint, '0.1.0', 'original', 'v1')[:16]}"
    draft = svc.create(idea, version="0.1.0", format="original", voice_version="v1")
    assert draft.id == expected_id


def test_create_position_statement_empty_without_author_position():
    svc, _ = _service()
    draft = svc.create(
        _idea(author_position=None),
        version="0.1.0",
        format="original",
        voice_version="v1",
    )
    assert draft.position_statement == ""
