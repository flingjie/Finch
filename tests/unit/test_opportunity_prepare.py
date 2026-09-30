"""Tests for write_contribution (选定机会后按需制作贡献：LLM 产出正文)."""

from finch.opportunities.models import ContributionForm, EntryKind, Opportunity, Proposal
from finch.opportunities.prepare import ContributionBodyOutput, write_contribution


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
