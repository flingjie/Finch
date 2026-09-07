"""Tests for DraftService (Skill 架构 Step 4：idea-to-draft 完整流程)。"""

import pytest

from finch.content.checkers.decision import _DecisionOutput
from finch.content.checkers.portability import _PortabilityOutput
from finch.content.checkers.safety import _SafetyOutput
from finch.content.jobs import AuthorPosition, ContentJob, ContentJobStatus
from finch.content.models import Draft, DraftKind
from finch.drafts.service import (
    DraftCreateResult,
    DraftService,
    _adjustments_summary,
    _idea_fingerprint,
    draft_generation_key,
)


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
    """In-memory double for CriticReportRepository (记录 upsert_report 调用)。"""

    def __init__(self) -> None:
        self.reports: list[tuple[str, int, object, str]] = []

    def upsert_report(self, draft_id: str, round: int, checks: object, outcome: str) -> None:
        self.reports.append((draft_id, round, checks, outcome))

    def list_reports(self, draft_id: str) -> list[tuple[str, int, object, str]]:
        return [r for r in self.reports if r[0] == draft_id]


class FakeContentJobRepository:
    """In-memory double for ContentJobRepository（get_job / upsert_job）。"""

    def __init__(self, jobs: list[ContentJob] | None = None) -> None:
        self._by_id: dict[str, ContentJob] = {j.id: j for j in (jobs or [])}

    def get_job(self, job_id: str) -> ContentJob | None:
        return self._by_id.get(job_id)

    def upsert_job(self, job: ContentJob) -> None:
        self._by_id[job.id] = job


class FakeRunner:
    """Counting double for CodexRunner：首稿返回 Draft，各检查器返回通过结果。

    ``safety=(invented, unsupported)`` 可注入 SafetyChecker 的失败 flag 以触发
    ``needs_input`` 丢弃路径。
    """

    def __init__(
        self,
        body: str = "把编排器改成确定性图后，失败可以重放，代价是要持久化状态。",
        safety: tuple[bool, bool] = (False, False),
    ) -> None:
        self.calls = 0
        self.body = body
        self.safety = safety

    def run(self, prompt: str, output_model: type, *, timeout: float | None = None) -> object:
        self.calls += 1
        if output_model is Draft:
            return Draft(
                id="tmp",
                kind=DraftKind.ORIGINAL,
                language="zh",
                body=self.body,
                claims=[],
            )
        if output_model is _DecisionOutput:
            return _DecisionOutput(
                expresses_decision=True, expresses_tradeoff=True, missing=[]
            )
        if output_model is _PortabilityOutput:
            return _PortabilityOutput(findings=[])
        if output_model is _SafetyOutput:
            return _SafetyOutput(
                invented_personal_experience=self.safety[0],
                unsupported_metric=self.safety[1],
            )
        raise AssertionError(f"unexpected output_model: {output_model!r}")


def _idea(**overrides: object) -> ContentJob:
    data: dict[str, object] = dict(
        id="idea_abc123",
        source_card_ids=[],
        candidate_id=None,
        reader_problem="很多人只把 Graph 当可视化",
        author_position=AuthorPosition(
            claim="Graph 主要价值是恢复与重放",
            decision="用可恢复性评价 Graph",
            tradeoff="需要持久化状态",
            change_mind_if=None,
        ),
        recommended_format=DraftKind.ORIGINAL,
        status=ContentJobStatus.CONFIRMED,
        core_message="Graph 的价值是恢复与重放",
        content_fingerprint="fp_abc123",
    )
    data.update(overrides)
    return ContentJob(**data)  # type: ignore[arg-type]


def _service(
    job: ContentJob | None = None,
    runner: FakeRunner | None = None,
    *,
    max_rewrite_rounds: int = 1,
) -> tuple[DraftService, FakeDraftRepository, FakeCriticReportRepository]:
    drafts = FakeDraftRepository()
    reports = FakeCriticReportRepository()
    jobs = FakeContentJobRepository([job] if job is not None else [])
    svc = DraftService(
        drafts,
        reports,
        jobs,
        runner or FakeRunner(),  # type: ignore[arg-type]
        max_rewrite_rounds=max_rewrite_rounds,
    )
    return svc, drafts, reports


def test_adjustments_summary_lists_checkers_fixed_between_rounds():
    reports = [
        {"outcome": "rewrite", "checks": [
            {"checker": "portability", "passed": False},
            {"checker": "voice", "passed": True},
        ]},
        {"outcome": "pass", "checks": [
            {"checker": "portability", "passed": True},
            {"checker": "voice", "passed": True},
        ]},
    ]
    assert _adjustments_summary(reports) == ["收紧了观点的适用边界"]


def test_adjustments_summary_empty_when_no_rounds_or_no_fixups():
    assert _adjustments_summary([]) == []
    reports = [{"outcome": "pass", "checks": [
        {"checker": "portability", "passed": True},
    ]}]
    assert _adjustments_summary(reports) == []


def test_create_result_reports_rounds_outcome_and_adjustments():
    class DictReports:
        def __init__(self) -> None:
            self.reports = [
                {"outcome": "rewrite", "checks": [{"checker": "portability", "passed": False}]},
                {"outcome": "pass", "checks": [{"checker": "portability", "passed": True}]},
            ]

        def upsert_report(self, *args, **kwargs) -> None:
            pass

        def list_reports(self, draft_id: str) -> list[dict]:
            return self.reports

    drafts = FakeDraftRepository()
    reports = DictReports()
    jobs = FakeContentJobRepository([_idea()])
    svc = DraftService(drafts, reports, jobs, FakeRunner(), max_rewrite_rounds=1)
    result = svc.create_result(
        "idea_abc123", version="1.0.0", format="original", voice_version="1.0.0"
    )
    assert isinstance(result, DraftCreateResult)
    assert result.outcome == "pass"
    assert result.critic_rounds == 2
    assert result.adjustments == ["收紧了观点的适用边界"]
    assert result.draft.id.startswith("draft_")


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


# ---- create: 状态门禁 ----

def test_create_raises_when_job_missing():
    svc, _, _ = _service()
    with pytest.raises(KeyError):
        svc.create("idea_missing", version="1.0.0", format="original", voice_version="1.0.0")


def test_create_raises_when_unconfirmed():
    svc, _, _ = _service(_idea(status=ContentJobStatus.PROPOSED))
    with pytest.raises(ValueError, match="needs_confirmation") as excinfo:
        svc.create("idea_abc123", version="1.0.0", format="original", voice_version="1.0.0")
    assert "idea_abc123" in str(excinfo.value)


# ---- create: 幂等 ----

def test_create_is_idempotent_no_second_llm_call():
    runner = FakeRunner()
    svc, drafts, _ = _service(_idea(), runner)
    first = svc.create("idea_abc123", version="1.0.0", format="original", voice_version="1.0.0")
    calls_after_first = runner.calls
    assert calls_after_first > 0

    second = svc.create("idea_abc123", version="1.0.0", format="original", voice_version="1.0.0")
    assert second == first
    assert runner.calls == calls_after_first  # 命中已有 Draft，不再调用 LLM
    assert len(drafts._by_id) == 1


# ---- create: 通过 Critic 落库 Draft + CriticReport ----

def test_create_pass_through_saves_draft_and_report():
    runner = FakeRunner()
    svc, drafts, reports = _service(_idea(), runner)
    draft = svc.create("idea_abc123", version="1.0.0", format="original", voice_version="1.0.0")

    key = draft_generation_key(_idea_fingerprint(_idea()), "1.0.0", "original", "1.0.0")
    expected_id = f"draft_{key[:16]}"
    assert draft.id == expected_id
    assert draft.body == runner.body
    assert draft.claims == []
    assert draft.content_job_id == "idea_abc123"
    assert draft.position_statement == "用可恢复性评价 Graph"
    assert draft.run_id == "idea"

    assert drafts.get_draft(expected_id) == draft
    assert reports.reports
    draft_id, round_no, _checks, outcome = reports.reports[-1]
    assert draft_id == expected_id
    assert round_no == 0
    assert outcome == "pass"


def test_idea_fingerprint_changes_when_tradeoff_changes():
    base = _idea()
    changed = _idea(
        author_position=AuthorPosition(
            claim="Graph 主要价值是恢复与重放",
            decision="用可恢复性评价 Graph",
            tradeoff="稳定后，再把需要确定性和幂等保障的部分代码化",
            change_mind_if=None,
        )
    )
    # core_message 相同、content_fingerprint 相同，但 tradeoff 变了 → 指纹必须变。
    assert base.core_message == changed.core_message
    assert base.content_fingerprint == changed.content_fingerprint
    assert _idea_fingerprint(base) != _idea_fingerprint(changed)


def test_idea_fingerprint_ignores_content_fingerprint():
    a = _idea()
    b = _idea(content_fingerprint="different_fp_but_same_fields")
    # content_fingerprint 只是 idea 自身的幂等字段，不应影响草稿指纹。
    assert _idea_fingerprint(a) == _idea_fingerprint(b)


# ---- create: 硬失败（needs_input）丢弃 ----

def test_create_drops_on_safety_needs_input():
    runner = FakeRunner(safety=(True, False))  # invented_personal_experience
    svc, drafts, reports = _service(_idea(), runner)
    with pytest.raises(ValueError, match="needs_input"):
        svc.create("idea_abc123", version="1.0.0", format="original", voice_version="1.0.0")
    assert drafts.list_drafts() == []
    # Critic 报告已落库（供排查），但 Draft 未保留。
    assert reports.reports
