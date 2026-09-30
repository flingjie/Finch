"""Tests for discover_preferred_opportunity (把候选转成首选机会并落库)."""

from datetime import UTC, datetime

from finch.opportunities.assess import OpportunityDraft
from finch.opportunities.discover import (
    discover_preferred_opportunity,
    render_artifacts_json,
)
from finch.opportunities.models import ContributionForm, EntryKind
from finch.opportunities.repository import OpportunityRepository
from finch.opportunities.service import OpportunityService
from finch.sources.models import AuthorIdentity, RawArtifact, Source
from finch.storage.workspace import Workspace


class FakeRunner:
    def __init__(self, draft: OpportunityDraft):
        self.draft = draft
        self.calls = 0
        self.last_prompt: str | None = None

    def run(self, prompt, output_model, **kw):
        self.calls += 1
        self.last_prompt = prompt
        return self.draft


def _draft(**kw) -> OpportunityDraft:
    data = dict(
        topic="失败回放",
        entry_kind=EntryKind.DIFFICULTY,
        why_me="与当前回归测试探索直接相关",
        why_continue="作者已保存 trace，可补充边界",
        contribution="做一张 trace→最小回放方法卡",
        form=ContributionForm.METHOD_CARD,
        expected_output="含输入/步骤/输出/限制的方法卡",
        scope="第一版只覆盖一个失败案例",
        recommend=True,
    )
    data.update(kw)
    return OpportunityDraft(**data)


def _artifact(
    artifact_id: str = "a1", title: str = "failure replay", text: str = "同一任务重跑结果不同"
) -> RawArtifact:
    return RawArtifact(
        artifact_id=artifact_id,
        source=Source.TWITTER,
        source_type="post",
        source_id="s1",
        author_identity=AuthorIdentity(platform="x", external_id="u1", handle="alice"),
        title=title,
        text=text,
        retrieved_at=datetime.now(UTC),
    )


def _service(tmp_path) -> OpportunityService:
    return OpportunityService(OpportunityRepository(Workspace(tmp_path)))


def _kwargs(tmp_path) -> dict:
    return dict(
        runner=FakeRunner(_draft()),
        peer_id="peer_1",
        display_name="alice",
        platform="x",
        current_work="failure replay",
        why_relevant="regression tests",
        person_ref="person_1",
        artifacts=[_artifact()],
        service=_service(tmp_path),
    )


def test_discovers_and_creates_preferred_opportunity(tmp_path):
    opp = discover_preferred_opportunity(**_kwargs(tmp_path))
    assert opp is not None
    assert opp.id == "opp_person_1"
    assert opp.person_ref == "person_1"
    assert opp.entry_kind == EntryKind.DIFFICULTY
    assert opp.proposal is not None
    assert opp.proposal.contribution == "做一张 trace→最小回放方法卡"
    assert _service(tmp_path).get("opp_person_1") is not None


def test_idempotent_returns_existing_without_reassessing(tmp_path):
    kwargs = _kwargs(tmp_path)
    first = discover_preferred_opportunity(**kwargs)
    calls = kwargs["runner"].calls
    second = discover_preferred_opportunity(**kwargs)
    assert second == first
    assert kwargs["runner"].calls == calls  # 命中已有，未重新调 LLM


def test_returns_none_when_not_recommended(tmp_path):
    kwargs = _kwargs(tmp_path)
    kwargs["runner"] = FakeRunner(_draft(recommend=False, skip_reason="已解决"))
    assert discover_preferred_opportunity(**kwargs) is None
    assert _service(tmp_path).get("opp_person_1") is None


def test_renders_artifact_text_in_prompt(tmp_path):
    kwargs = _kwargs(tmp_path)
    kwargs["artifacts"] = [_artifact(text="重跑结果不同")]
    discover_preferred_opportunity(**kwargs)
    assert "重跑结果不同" in kwargs["runner"].last_prompt


def test_render_artifacts_json_truncates_long_text():
    out = render_artifacts_json([_artifact(text="x" * 1000)])
    assert '"title": "failure replay"' in out
    assert "…" in out
