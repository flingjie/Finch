"""P0 baseline expectations for problem-led connection fixtures."""

from discovery_samples import (
    ALL_SAMPLE_POSTS,
    BASELINE_DAILY_SHAPE,
    CROSS_DOMAIN_METHOD,
    DUPLICATE_OF_FAMILIAR,
    FAMILIAR_VIEW,
    LEARN_ONLY,
    MISSING_SOURCE,
    NEW_AUTHOR,
    OLD_AUTHOR_NEW_EXPERIMENT,
    PLATFORM_FAILURE,
    PRACTICE_RELEASE,
)


def test_baseline_daily_shape_documented():
    assert BASELINE_DAILY_SHAPE["show_peers_cap"] == 3
    assert BASELINE_DAILY_SHAPE["pool_max_peers_per_run"] == 5
    assert BASELINE_DAILY_SHAPE["generates_drafts_on_daily"] is True


def test_representative_samples_cover_required_scenarios():
    by_id = {p.id: p for p in ALL_SAMPLE_POSTS}
    assert "fam_1" in by_id
    assert "new_1" in by_id
    assert "old_exp_2" in by_id
    assert "cross_1" in by_id
    assert "learn_1" in by_id
    assert "missing_1" in by_id
    assert MISSING_SOURCE.url == ""
    assert DUPLICATE_OF_FAMILIAR.content == FAMILIAR_VIEW.content
    assert "发布" in PRACTICE_RELEASE.content
    assert "复盘" in PRACTICE_RELEASE.content or "实验" in PRACTICE_RELEASE.content
    assert PLATFORM_FAILURE.reason
    assert LEARN_ONLY.author_id != NEW_AUTHOR.author_id
    assert CROSS_DOMAIN_METHOD.matched_topics
    assert OLD_AUTHOR_NEW_EXPERIMENT.author_id == FAMILIAR_VIEW.author_id
