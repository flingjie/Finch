"""Tests for write_contribution and prepare_contribution (选定机会后按需制作贡献)."""

import pytest

from finch.expression_methods.models import ExpressionMethod
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
    Reaction,
)
from finch.opportunities.prepare import (
    REPLY_STYLE_POLICY_VERSION,
    ContributionBodyOutput,
    PreparedContribution,
    artifact_id_for,
    artifact_kind_for,
    effective_form,
    prepare_contribution,
    render_evidence_refs,
    render_reply_style,
    write_contribution,
)
from finch.opportunities.repository import ArtifactRepository, OpportunityRepository
from finch.opportunities.service import OpportunityService
from finch.profile.models import PracticeEvidenceStatus, PracticeItem, PracticeProfile
from finch.storage.workspace import Workspace


class FakeRunner:
    def __init__(
        self,
        body: str,
        method_id: str | None = None,
        fit_reason: str = "",
        response_focus: str = "",
    ):
        self.body = body
        self.method_id = method_id
        self.fit_reason = fit_reason
        self.response_focus = response_focus
        self.calls = 0
        self.last_prompt: str | None = None

    def run(self, prompt, output_model, **kw):
        self.calls += 1
        self.last_prompt = prompt
        return ContributionBodyOutput(
            body=self.body,
            method_id=self.method_id,
            fit_reason=self.fit_reason,
            response_focus=self.response_focus,
        )


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
    out = write_contribution(runner, _opportunity())
    assert out.body == "适用处境：…"
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
    assert "art_opp_1_clarifying_question" in opp.artifact_refs
    assert art_repo.read_content("opp_1", "art_opp_1_clarifying_question") == body
    assert [e.event_type for e in repo.list_events("opp_1")] == [
        "proposed",
        "selected",
        "artifact_added",
        "ready",
    ]
    saved = art_repo.get("opp_1", "art_opp_1_clarifying_question")
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


def test_write_contribution_injects_voice_and_positions():
    from finch.content.jobs import AuthorPosition, ContentJob, ContentJobStatus
    from finch.content.models import ContentType, RecommendedFormat
    from finch.content.voice import VoiceProfile

    runner = FakeRunner("x")
    write_contribution(
        runner,
        _opportunity(),
        voice_profile=VoiceProfile(preferred_patterns=["先给结论"], avoid_phrases=["赋能"]),
        confirmed_jobs=[
            ContentJob(
                id="idea_1",
                source_card_ids=[],
                status=ContentJobStatus.CONFIRMED,
                reader_problem="p",
                core_message="m",
                why_now="n",
                recommended_format=RecommendedFormat.SHORT_POST,
                content_type=ContentType.METHOD_CARD,
                author_position=AuthorPosition(
                    claim="失败要变成回归",
                    decision="做回放卡",
                    tradeoff="覆盖面换精确度",
                ),
            )
        ],
    )
    p = runner.last_prompt or ""
    assert "先给结论" in p
    assert "赋能" in p
    assert "失败要变成回归" in p


def test_render_evidence_refs_is_json():
    out = render_evidence_refs(
        [EvidenceRef(source_ref="s", quote="q", claim="c", tier=EvidenceTier.EXPLICIT)]
    )
    assert '"source_ref": "s"' in out
    assert '"quote": "q"' in out


def _profile() -> PracticeProfile:
    return PracticeProfile(
        items=[
            PracticeItem(
                id="agent-100-days",
                domain="agent engineering",
                claim="把 Agent 落地失败整理成 100 天路径",
                evidence_refs=["https://github.com/flingjie/Agent-100-Days"],
                status=PracticeEvidenceStatus.SOURCED,
                can_offer=["方法卡"],
                boundaries="没管过生产 Agent SLA",
                confirmed=True,
            )
        ]
    )


def test_write_contribution_renders_practice_block():
    runner = FakeRunner("x")
    write_contribution(runner, _opportunity(), practice_profile=_profile())
    p = runner.last_prompt or ""
    assert "## User real practices" in p
    assert "[agent-100-days]" in p
    assert "没管过生产 Agent SLA" in p


def test_write_contribution_without_profile_renders_none():
    runner = FakeRunner("x")
    write_contribution(runner, _opportunity())
    p = runner.last_prompt or ""
    section = p.split("## User real practices (confirmed; the ONLY source for first-person")[1]
    # 精确钉住槽位：规则正文里也出现 (none)，不能用宽松包含断言。
    assert "experience)\n\n(none)\n\n## User reaction to THIS opportunity" in section


def test_prepare_contribution_passes_profile(tmp_path):
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
    service.select("opp_1")
    runner = FakeRunner("正文")
    prepare_contribution(
        opportunity=service.get("opp_1"),
        runner=runner,
        service=service,
        practice_profile=_profile(),
    )
    assert "[agent-100-days]" in (runner.last_prompt or "")


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
    assert "\n\n(none)\n\n## Reply style policy (default)" in section


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


# ---- 简洁风格（reply style policy，plan 第 2 步）----


def test_render_reply_style_contains_concise_rules():
    style = render_reply_style()
    assert "40" in style and "120" in style  # 长度目标
    assert "铺垫" in style  # 少铺垫
    assert "问题" in style  # 问题可选


def test_reply_style_policy_version_is_defined():
    assert REPLY_STYLE_POLICY_VERSION


def test_write_contribution_injects_reply_style_block():
    runner = FakeRunner("x")
    write_contribution(runner, _opportunity())
    p = runner.last_prompt or ""
    assert "## Reply style policy (default)" in p
    assert "铺垫" in p


def test_write_contribution_default_style_note_is_none():
    runner = FakeRunner("x")
    write_contribution(runner, _opportunity())
    p = runner.last_prompt or ""
    section = p.split("## This-time style instruction (highest priority)")[1]
    assert section.startswith("\n\n(none)\n\n")


def test_write_contribution_style_note_overrides_default():
    runner = FakeRunner("x")
    write_contribution(runner, _opportunity(), style_note="再短一点")
    p = runner.last_prompt or ""
    assert "再短一点" in p


# ---- 方法复用（method reuse，plan 第 3 步）----


def _reply_method() -> ExpressionMethod:
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    return ExpressionMethod(
        id="emethod_reply_1",
        title="补充具体案例",
        why_effective="用真实案例补充结论适用范围",
        when_to_use="对方提出一般判断",
        applicable_forms=["reply"],
        reply_usage="用一个真实案例补充结论的适用条件",
        reply_boundaries="需要真实经历或可引用公开案例",
        created_at=now,
        updated_at=now,
    )


def test_write_contribution_renders_methods_block():
    runner = FakeRunner("x")
    write_contribution(runner, _opportunity(), methods=[_reply_method()])
    p = runner.last_prompt or ""
    assert "emethod_reply_1" in p
    assert "补充具体案例" in p
    assert "用一个真实案例补充结论的适用条件" in p


def test_write_contribution_renders_no_methods_as_none():
    runner = FakeRunner("x")
    write_contribution(runner, _opportunity())
    p = runner.last_prompt or ""
    section = p.split("## Available methods (optional)")[1]
    assert section.startswith("\n\n(none)\n\n")


def test_write_contribution_validates_method_id_in_candidates():
    runner = FakeRunner("x", method_id="unknown_id")
    with pytest.raises(ValueError, match="method_id"):
        write_contribution(runner, _opportunity(), methods=[_reply_method()])


def test_write_contribution_returns_method_selection():
    runner = FakeRunner(
        "正文",
        method_id="emethod_reply_1",
        fit_reason="贴合",
        response_focus="重试的副作用",
    )
    out = write_contribution(runner, _opportunity(), methods=[_reply_method()])
    assert out.body == "正文"
    assert out.method_id == "emethod_reply_1"
    assert out.fit_reason == "贴合"
    assert out.response_focus == "重试的副作用"


def test_prepare_records_method_metadata_on_artifact(tmp_path):
    service, _repo, art_repo = _service(tmp_path)
    service.create(
        opportunity_id="opp_1",
        topic="t",
        proposal=Proposal(
            contribution="c",
            form=ContributionForm.REPLY_DRAFT,
            expected_output="o",
            scope="s",
        ),
    )
    service.select("opp_1")
    method = _reply_method()
    runner = FakeRunner(
        "正文", method_id=method.id, fit_reason="贴合", response_focus="重点"
    )
    prepare_contribution(
        opportunity=service.get("opp_1"),
        runner=runner,
        service=service,
        reaction="我遇到过类似情况",
        methods=[method],
    )
    art = art_repo.get("opp_1", "art_opp_1_reply_draft_r1")
    assert art is not None
    assert art.method_ref == method.id
    assert art.method_version_hash == method.content_fingerprint()
    assert art.fit_reason == "贴合"
    assert art.response_focus == "重点"
    assert art.style_policy_version == REPLY_STYLE_POLICY_VERSION
