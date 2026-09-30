"""Tests for write_contribution and prepare_contribution (选定机会后按需制作贡献)."""

import pytest

from finch.opportunities.models import (
    ArtifactKind,
    ContributionForm,
    EntryKind,
    EvidenceRef,
    EvidenceTier,
    MaterialOrigin,
    Opportunity,
    OpportunityStatus,
    Proposal,
)
from finch.opportunities.prepare import (
    ContributionBodyOutput,
    PreparedContribution,
    artifact_kind_for,
    prepare_contribution,
    render_evidence_refs,
    write_contribution,
)
from finch.opportunities.repository import ArtifactRepository, OpportunityRepository
from finch.opportunities.service import OpportunityService
from finch.storage.workspace import Workspace


class FakeRunner:
    def __init__(self, body: str):
        self.body = body
        self.calls = 0
        self.last_prompt: str | None = None

    def run(self, prompt, output_model, **kw):
        self.calls += 1
        self.last_prompt = prompt
        return ContributionBodyOutput(body=self.body)


def _opportunity() -> Opportunity:
    return Opportunity(
        id="opp_1",
        person_ref="person_1",
        topic="失败回放",
        entry_kind=EntryKind.DIFFICULTY,
        why_me="与回归测试探索直接相关",
        why_continue="作者已保存 trace",
        proposal=Proposal(
            contribution="做一张 trace→最小回放方法卡",
            form=ContributionForm.METHOD_CARD,
            expected_output="含输入/步骤/输出/限制的方法卡",
            scope="一个失败案例",
        ),
    )


def test_write_contribution_returns_body():
    runner = FakeRunner("适用处境：…")
    body = write_contribution(runner, _opportunity())
    assert body == "适用处境：…"
    assert runner.calls == 1


def test_write_contribution_renders_opportunity_context():
    runner = FakeRunner("x")
    write_contribution(runner, _opportunity())
    p = runner.last_prompt or ""
    assert "失败回放" in p
    assert "做一张 trace→最小回放方法卡" in p
    assert "method_card" in p
    assert "含输入/步骤/输出/限制的方法卡" in p


def test_artifact_kind_for_maps_forms():
    assert artifact_kind_for(ContributionForm.METHOD_CARD) == ArtifactKind.METHOD_CARD
    assert artifact_kind_for(ContributionForm.DEMO) == ArtifactKind.DEMO
    assert artifact_kind_for(ContributionForm.CASE) == ArtifactKind.CASE
    assert artifact_kind_for(ContributionForm.REPLY_DRAFT) == ArtifactKind.REPLY_DRAFT
    assert artifact_kind_for(ContributionForm.CLARIFYING_QUESTION) == ArtifactKind.REPLY_DRAFT


def _service(tmp_path) -> tuple[OpportunityService, OpportunityRepository, ArtifactRepository]:
    ws = Workspace(tmp_path)
    repo = OpportunityRepository(ws)
    art_repo = ArtifactRepository(ws)
    return OpportunityService(repo, artifacts=art_repo), repo, art_repo


def test_prepare_contribution_marks_ready_and_records_artifact(tmp_path):
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

    body = "适用处境：…\n输入：…\n步骤：…\n输出：…\n限制：…"
    prepared = prepare_contribution(
        opportunity=service.get("opp_1"), runner=FakeRunner(body), service=service
    )

    assert isinstance(prepared, PreparedContribution)
    opp = prepared.opportunity
    assert opp.status == OpportunityStatus.READY
    assert "art_opp_1_method_card" in opp.artifact_refs
    assert art_repo.read_content("opp_1", "art_opp_1_method_card") == body
    assert [e.event_type for e in repo.list_events("opp_1")] == [
        "proposed",
        "selected",
        "artifact_added",
        "ready",
    ]
    saved = art_repo.get("opp_1", "art_opp_1_method_card")
    assert saved is not None
    assert saved.material_origin == MaterialOrigin.SYNTHETIC
    assert prepared.artifacts[0].body == body
    assert prepared.artifacts[0].execution_status.value == "not_run"


def test_prepare_contribution_rejects_unselected_before_calling_llm(tmp_path):
    service, _repo, _art_repo = _service(tmp_path)
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
    runner = FakeRunner("x")
    with pytest.raises(ValueError, match="illegal state"):
        prepare_contribution(
            opportunity=service.get("opp_1"), runner=runner, service=service
        )
    assert runner.calls == 0


def test_write_contribution_includes_evidence_in_prompt():
    opp = _opportunity().model_copy(
        update={
            "evidence_refs": [
                EvidenceRef(
                    source_ref="https://x.com/alice/status/1",
                    quote="同一任务重跑结果不同",
                    claim="失败可复现",
                    tier=EvidenceTier.EXPLICIT,
                )
            ]
        }
    )
    runner = FakeRunner("x")
    write_contribution(runner, opp)
    p = runner.last_prompt or ""
    assert "https://x.com/alice/status/1" in p
    assert "同一任务重跑结果不同" in p


def test_render_evidence_refs_is_json():
    out = render_evidence_refs(
        [EvidenceRef(source_ref="s", quote="q", claim="c", tier=EvidenceTier.EXPLICIT)]
    )
    assert '"source_ref": "s"' in out
    assert '"quote": "q"' in out
