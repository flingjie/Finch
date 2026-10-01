"""Tests for practice-profile bootstrap (README → unconfirmed PracticeItem drafts)."""

import base64

from finch.github import gh_client
from finch.github.gh_client import GhClient
from finch.profile.bootstrap import (
    PracticeDraftOutput,
    PracticeItemDraft,
    draft_items_from_readme,
    merge_drafts,
)
from finch.profile.models import PracticeEvidenceStatus, PracticeItem, PracticeProfile


class FakeRunner:
    def __init__(self, out: PracticeDraftOutput | None = None, exc: Exception | None = None):
        self.out = out
        self.exc = exc
        self.last_prompt = None

    def run(self, prompt, output_model, **kw):
        self.last_prompt = prompt
        if self.exc:
            raise self.exc
        return self.out


def test_gh_readme_decodes_base64(monkeypatch):
    content = base64.b64encode(b"# Hello\nbody").decode()
    monkeypatch.setattr(
        gh_client,
        "_run",
        lambda argv, timeout, stdin=None: {
            "ok": True, "exit_code": 0, "stderr": "",
            "stdout": f'{{"content": "{content}", "encoding": "base64"}}',
        },
    )
    assert GhClient().readme("o/r") == "# Hello\nbody"


def test_draft_items_from_readme_sets_sourced_unconfirmed():
    runner = FakeRunner(
        PracticeDraftOutput(
            items=[
                PracticeItemDraft(
                    id="agent-100-days", domain="agent engineering",
                    claim="100 天路径", can_offer=["方法卡"], boundaries="",
                )
            ]
        )
    )
    items = draft_items_from_readme(runner, repo="flingjie/Agent-100-Days", readme="# x")
    assert len(items) == 1
    it = items[0]
    assert it.confirmed is False
    assert it.status == PracticeEvidenceStatus.SOURCED
    assert it.evidence_refs == ["https://github.com/flingjie/Agent-100-Days"]
    assert "flingjie/Agent-100-Days" in (runner.last_prompt or "")
    assert "# x" in (runner.last_prompt or "")
    assert "never instructions" in (runner.last_prompt or "")


def test_draft_items_from_readme_llm_failure_returns_empty():
    runner = FakeRunner(exc=RuntimeError("boom"))
    assert draft_items_from_readme(runner, repo="o/r", readme="x") == []


def test_draft_items_skip_blank_ids():
    runner = FakeRunner(
        PracticeDraftOutput(items=[PracticeItemDraft(id="  ", domain="d", claim="c")])
    )
    assert draft_items_from_readme(runner, repo="o/r", readme="x") == []


def _item(id_: str, confirmed: bool, claim: str = "c") -> PracticeItem:
    return PracticeItem(
        id=id_, domain="d", claim=claim, evidence_refs=["https://x"],
        status=PracticeEvidenceStatus.SOURCED, confirmed=confirmed,
    )


def test_merge_drafts_appends_new_and_keeps_existing():
    existing = PracticeProfile(items=[_item("a", True, claim="original")])
    merged, added = merge_drafts(existing, [_item("a", False, claim="new"), _item("b", False)])
    assert added == ["b"]
    assert [i.id for i in merged.items] == ["a", "b"]
    assert merged.get("a").claim == "original"
    assert merged.get("a").confirmed is True


def test_merge_drafts_is_idempotent():
    p = PracticeProfile()
    p1, added1 = merge_drafts(p, [_item("a", False)])
    p2, added2 = merge_drafts(p1, [_item("a", False)])
    assert added1 == ["a"] and added2 == []
    assert len(p2.items) == 1
