"""conversation → personal 证据升级单元测试（Phase 6，无网络）。"""

import pytest

from finch.engagement.evidence_upgrade import (
    EvidenceBatchOutput,
    ExtractedEvidenceItem,
    extract_conversation_evidence,
    promote_to_personal,
)
from finch.engagement.models import ConversationEvidence, Verification
from finch.evidence.models import ClaimConfidence


def _evidence(**overrides) -> ConversationEvidence:
    data = dict(
        id="ce_x:p1:draft_reply_0",
        interaction_id="x:p1:draft_reply",
        post_id="p1",
        kind="hypothesis",
        statement="Recording failure replays makes agent failures reproducible.",
        verified=False,
    )
    data.update(overrides)
    return ConversationEvidence(**data)


def _verification(
    kind: str = "experiment", detail: str = "replayed a recorded failure locally"
) -> Verification:
    return Verification(kind=kind, detail=detail)


class _FakeRunner:
    """记录调用次数并返回固定 EvidenceBatchOutput。"""

    def __init__(self, items: list[ExtractedEvidenceItem]) -> None:
        self.items = items
        self.calls = 0

    def run(self, prompt, output_model, **kw):
        self.calls += 1
        return EvidenceBatchOutput(items=self.items)


@pytest.mark.parametrize("kind", ["case", "code", "experiment", "multi_source"])
def test_promote_verified_with_valid_kind(kind):
    evidence = _evidence(verified=True)
    card = promote_to_personal(evidence, _verification(kind=kind))
    assert card is not None
    assert card.id == "ev_ce_x:p1:draft_reply_0"
    assert card.event_id == evidence.interaction_id
    assert card.claim == evidence.statement
    assert card.confidence is ClaimConfidence.SUPPORTED
    assert card.sources == []
    assert card.topics == []


def test_promote_returns_none_when_unverified():
    assert promote_to_personal(_evidence(verified=False), _verification()) is None


def test_promote_returns_none_when_wrong_verification_kind():
    # 绕过 Literal 校验，模拟非法验证来源（防御分支）。
    bad = Verification.model_construct(kind="unknown", detail="nope")
    assert promote_to_personal(_evidence(verified=True), bad) is None


def test_extract_conversation_evidence_returns_items():
    runner = _FakeRunner(
        [
            ExtractedEvidenceItem(kind="question", statement="How do you replay failures?"),
            ExtractedEvidenceItem(kind="hypothesis", statement="Replay makes failures testable."),
        ]
    )
    out = extract_conversation_evidence(runner, "x:p1:draft_reply", "p1", "some discussion")
    assert runner.calls == 1
    assert [e.kind for e in out] == ["question", "hypothesis"]
    assert all(e.interaction_id == "x:p1:draft_reply" for e in out)
    assert all(e.post_id == "p1" for e in out)
    assert all(e.origin == "conversation" for e in out)
    assert all(e.verified is False for e in out)
    assert [e.id for e in out] == ["ce_x:p1:draft_reply_0", "ce_x:p1:draft_reply_1"]


def test_extract_empty_discussion_short_circuits_no_llm_call():
    runner = _FakeRunner([])
    out = extract_conversation_evidence(runner, "x:p1:draft_reply", "p1", "   \n ")
    assert out == []
    assert runner.calls == 0
