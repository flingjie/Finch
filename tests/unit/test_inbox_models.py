"""Unit tests for inbox projection models."""

from finch.inbox.models import InboxItem, InboxTrack


def test_inbox_item_round_trips():
    item = InboxItem(
        id="job_1",
        track=InboxTrack.ORIGINAL,
        content_type="original",
        provenance="personal",
        source_refs=["https://github.com/o/r/commit/abc"],
        why_now="replay 是本周热点",
        score=0.72,
        draft_id="draft_1",
        draft="正文",
        position={"claim": "c", "decision": "d", "tradeoff": "t"},
        must_ask=False,
        ask_reasons=[],
        risks=[],
    )
    back = InboxItem.model_validate(item.model_dump(mode="json"))
    assert back == item
    assert back.track == InboxTrack.ORIGINAL
    assert back.content_type == "original"


def test_inbox_item_defaults():
    item = InboxItem(
        id="cand_1",
        track=InboxTrack.ENGAGEMENT,
        content_type="reply",
        provenance="external",
        source_refs=[],
        why_now="",
        score=0.0,
        draft_id=None,
        draft="",
    )
    assert item.position is None
    assert item.must_ask is False
    assert item.ask_reasons == []
    assert item.risks == []
