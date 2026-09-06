from finch.gate import interactive
from finch.gate.models import InputAction, InputRequest, ProposedPosition


class _FakeTyper:
    def __init__(self, prompts):
        self._prompts = list(prompts)
        self.echoed = []

    def prompt(self, text, default=""):
        if not self._prompts:
            raise AssertionError("no more prompts")
        return self._prompts.pop(0)

    def echo(self, text=""):
        self.echoed.append(text)


def _request():
    return InputRequest(
        run_id="r1", job_id="j1", topic="t",
        proposed_position=ProposedPosition(claim="c", decision="d", tradeoff="t"),
    )


def test_select_action_enter_confirms(monkeypatch):
    fake = _FakeTyper([""])
    monkeypatch.setattr(interactive, "typer", fake)
    assert interactive.select_action(_request(), []) is InputAction.CONFIRM


def test_select_action_keys(monkeypatch):
    for key, expected in [("e", InputAction.EDIT), ("s", InputAction.SKIP)]:
        fake = _FakeTyper([key])
        monkeypatch.setattr(interactive, "typer", fake)
        assert interactive.select_action(_request(), []) is expected


def test_select_action_q_returns_none(monkeypatch):
    fake = _FakeTyper(["q"])
    monkeypatch.setattr(interactive, "typer", fake)
    assert interactive.select_action(_request(), []) is None


def test_select_action_d_shows_evidence_then_confirms(monkeypatch):
    fake = _FakeTyper(["d", ""])
    monkeypatch.setattr(interactive, "typer", fake)
    assert interactive.select_action(_request(), []) is InputAction.CONFIRM
    assert any("（无证据）" in e for e in fake.echoed)


def test_select_action_invalid_retries(monkeypatch):
    fake = _FakeTyper(["x", ""])
    monkeypatch.setattr(interactive, "typer", fake)
    assert interactive.select_action(_request(), []) is InputAction.CONFIRM
    assert any("无效选择" in e for e in fake.echoed)


def test_edit_position_inline_keeps_fields_on_empty(monkeypatch):
    fake = _FakeTyper(["", "方案2", "", ""])
    monkeypatch.setattr(interactive, "typer", fake)
    out = interactive.edit_position_inline(
        ProposedPosition(claim="c", decision="d", tradeoff="t", change_mind_if="cm")
    )
    assert out.claim == "c"
    assert out.decision == "方案2"
    assert out.tradeoff == "t"
    assert out.change_mind_if == "cm"
