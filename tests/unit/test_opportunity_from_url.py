"""Tests for connect assess --url (入口 2)."""

from finch.opportunities.assess import OpportunityDraft
from finch.opportunities.from_url import assess_from_url
from finch.opportunities.models import ContributionForm, EntryKind
from finch.opportunities.repository import OpportunityRepository
from finch.opportunities.service import OpportunityService
from finch.storage.workspace import Workspace
from finch.webfetch.fetcher import WebSourceUnavailable


class FakeFetcher:
    def __init__(self, body: str = "作者说重跑仍不一致", exc: Exception | None = None):
        self.body = body
        self.exc = exc
        self.calls = 0

    def fetch(self, url: str) -> str:
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return self.body


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
        why_me="用户指定的讨论",
        why_continue="可补充边界",
        contribution="做一张方法卡",
        form=ContributionForm.METHOD_CARD,
        expected_output="一张卡",
        scope="一例",
        recommend=True,
    )
    data.update(kw)
    return OpportunityDraft(**data)


def test_assess_from_url_creates_opportunity(tmp_path):
    ws = Workspace(tmp_path)
    service = OpportunityService(OpportunityRepository(ws))
    runner = FakeRunner(_draft())
    result = assess_from_url(
        url="https://example.com/post/1",
        runner=runner,
        service=service,
        fetcher=FakeFetcher(),
        user_context="回归测试",
    )
    assert result.outcome == "recommended"
    assert result.opportunity is not None
    assert result.opportunity.thread_ref == "https://example.com/post/1"
    assert any("身份未知" in q for q in result.opportunity.open_questions)
    assert "重跑仍不一致" in (runner.last_prompt or "")


def test_assess_from_url_fetch_failed(tmp_path):
    ws = Workspace(tmp_path)
    service = OpportunityService(OpportunityRepository(ws))
    result = assess_from_url(
        url="https://example.com/post/1",
        runner=FakeRunner(_draft()),
        service=service,
        fetcher=FakeFetcher(exc=WebSourceUnavailable("blocked")),
    )
    assert result.outcome == "fetch_failed"
    assert result.opportunity is None


def test_assess_from_url_idempotent(tmp_path):
    ws = Workspace(tmp_path)
    service = OpportunityService(OpportunityRepository(ws))
    fetcher = FakeFetcher()
    runner = FakeRunner(_draft())
    first = assess_from_url(
        url="https://example.com/post/1",
        runner=runner,
        service=service,
        fetcher=fetcher,
    )
    second = assess_from_url(
        url="https://example.com/post/1",
        runner=runner,
        service=service,
        fetcher=fetcher,
    )
    assert first.opportunity is not None
    assert second.opportunity is not None
    assert first.opportunity.id == second.opportunity.id
    assert runner.calls == 1


def test_assess_from_url_passes_user_practices_to_prompt(tmp_path):
    service = OpportunityService(OpportunityRepository(Workspace(tmp_path)))
    runner = FakeRunner(_draft())
    assess_from_url(
        url="https://example.com/post",
        runner=runner,
        service=service,
        fetcher=FakeFetcher("body text"),
        user_practices="- [agent-100-days] (sourced) agent engineering: 100 天路径",
    )
    assert "[agent-100-days]" in (runner.last_prompt or "")
