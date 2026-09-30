# tests/unit/test_engagement_models.py
from datetime import datetime

from finch.engagement.models import InteractionRecord
from finch.settings import Settings, load_settings


def test_settings_defaults_for_engagement_and_interests():
    s = Settings()
    assert s.engagement.enabled is True
    assert s.engagement.schedule == "every_run"
    assert s.engagement.platforms == ["x", "reddit"]
    assert s.engagement.max_posts_scanned == 30
    assert s.engagement.min_candidate_score == 0.72
    assert s.engagement.max_bookmarks == 5
    assert s.engagement.max_reply_drafts == 10
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


def test_interaction_record_follow_up_at_defaults_none():
    rec = InteractionRecord(
        id="rec_1", proposal_id="p1", peer_id="peer_abc", platform="x",
        source_url="https://x.com/a/1", occurred_at=datetime(2026, 9, 1),
    )
    assert rec.follow_up_at is None


def test_interaction_record_links_to_opportunity():
    rec = InteractionRecord(
        id="rec_1", peer_id="peer_abc", platform="x",
        source_url="https://x.com/a/1", occurred_at=datetime(2026, 9, 1),
    )
    assert rec.opportunity_id is None
    linked = rec.model_copy(update={"opportunity_id": "opp_person_1"})
    assert linked.opportunity_id == "opp_person_1"
