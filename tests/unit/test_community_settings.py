from finch.settings import CommunityScoutSettings, Settings


def test_community_scout_defaults():
    s = CommunityScoutSettings()
    assert s.max_candidates == 20
    assert s.inspect_batch == 6
    assert s.max_cards == 3
    assert s.max_reinspect_rounds == 1
    assert s.suppress_window_weeks == 4
    assert s.search_urls == []


def test_settings_carries_community_scout():
    settings = Settings()
    assert settings.community_scout.max_cards == 3
