# tests/unit/test_engagement_models.py
from datetime import datetime

import pytest
from pydantic import ValidationError

from finch.engagement.models import (
    ConversationScore,
    ExternalPost,
    InteractionAction,
    InteractionCandidate,
    InteractionStatus,
)
from finch.settings import Settings, load_settings

_SCORE_FIELDS = [
    "relevance",
    "novelty",
    "discussability",
    "practical_evidence",
    "relationship_value",
    "total",
]


def _make_post(**overrides) -> ExternalPost:
    data = dict(
        id="post_1",
        platform="x",
        url="https://x.com/alice/status/1",
        author_id="alice",
        author_name="Alice",
        content="How do you test agent reliability?",
        published_at=datetime(2026, 9, 1, 12, 0, 0),
    )
    data.update(overrides)
    return ExternalPost(**data)


def _score_kwargs(**overrides) -> dict:
    data = {field: 0.5 for field in _SCORE_FIELDS}
    data["reasons"] = ["relevant"]
    data.update(overrides)
    return data


def test_external_post_defaults():
    post = _make_post()
    assert post.metrics == {}
    assert post.matched_topics == []
    assert post.platform == "x"


def test_external_post_rejects_unknown_platform():
    with pytest.raises(ValidationError):
        _make_post(platform="linkedin")


def test_external_post_metrics_and_topics():
    post = _make_post(metrics={"likes": 3, "views": 10.5}, matched_topics=["agent evals"])
    assert post.metrics["likes"] == 3
    assert post.metrics["views"] == 10.5
    assert post.matched_topics == ["agent evals"]


def test_conversation_score_valid():
    score = ConversationScore(
        **_score_kwargs(relevance=0.8, novelty=0.7, total=0.72)
    )
    assert score.total == 0.72
    assert score.reasons == ["relevant"]


def test_conversation_score_total_is_required_with_no_default():
    with pytest.raises(ValidationError):
        ConversationScore(
            relevance=0.5, novelty=0.5, discussability=0.5,
            practical_evidence=0.5, relationship_value=0.5, reasons=[],
        )


@pytest.mark.parametrize("field", _SCORE_FIELDS)
def test_conversation_score_upper_bound(field):
    with pytest.raises(ValidationError):
        ConversationScore(**_score_kwargs(**{field: 1.1}))


@pytest.mark.parametrize("field", _SCORE_FIELDS)
def test_conversation_score_lower_bound(field):
    with pytest.raises(ValidationError):
        ConversationScore(**_score_kwargs(**{field: -0.1}))


def test_interaction_action_values_lowercase():
    assert InteractionAction.IGNORE.value == "ignore"
    assert InteractionAction.BOOKMARK.value == "bookmark"
    assert InteractionAction.OBSERVE_AUTHOR.value == "observe_author"
    assert InteractionAction.DRAFT_REPLY.value == "draft_reply"
    assert InteractionAction.DRAFT_QUOTE.value == "draft_quote"
    assert InteractionAction.DRAFT_DM.value == "draft_dm"


def test_interaction_status_values_lowercase():
    assert InteractionStatus.PROPOSED.value == "proposed"
    assert InteractionStatus.APPROVED.value == "approved"
    assert InteractionStatus.REJECTED.value == "rejected"
    assert InteractionStatus.EXECUTED.value == "executed"
    assert InteractionStatus.EXPIRED.value == "expired"


def test_interaction_candidate_default_status():
    post = _make_post()
    score = ConversationScore(
        **_score_kwargs(relevance=0.8, novelty=0.7, total=0.72)
    )
    candidate = InteractionCandidate(
        post=post, score=score, action=InteractionAction.BOOKMARK, approval_required=False,
    )
    assert candidate.status is InteractionStatus.PROPOSED
    assert candidate.draft is None
    assert candidate.approval_required is False


def test_settings_defaults_for_engagement_and_interests():
    s = Settings()
    assert s.engagement.enabled is True
    assert s.engagement.schedule == "every_run"
    assert s.engagement.platforms == ["x", "reddit"]
    assert s.engagement.max_posts_scanned == 30
    assert s.engagement.min_candidate_score == 0.72
    assert s.engagement.max_bookmarks == 5
    assert s.engagement.max_reply_drafts == 3
    assert s.engagement.max_public_replies == 2
    assert s.engagement.per_author_daily_limit == 1
    assert s.engagement.public_expression_requires_approval is True
    assert s.engagement.weights.relevance == 0.25
    assert s.engagement.weights.novelty == 0.25
    assert s.engagement.weights.discussability == 0.20
    assert s.engagement.weights.practical_evidence == 0.20
    assert s.engagement.weights.relationship_value == 0.10
    assert s.interests.stable == []
    assert s.interests.exploring == []
    assert s.interests.excluded == []


def test_settings_loads_engagement_and_interests_from_yaml(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    yaml_path = tmp_path / "finch.yaml"
    yaml_path.write_text(
        "engagement:\n"
        "  enabled: false\n"
        "  min_candidate_score: 0.8\n"
        "interests:\n"
        "  stable:\n"
        "    - agent reliability\n"
        "  excluded:\n"
        "    - AI 新闻搬运\n",
        encoding="utf-8",
    )
    s = load_settings(yaml_path)
    assert s.engagement.enabled is False
    assert s.engagement.min_candidate_score == 0.8
    assert s.interests.stable == ["agent reliability"]
    assert s.interests.excluded == ["AI 新闻搬运"]
