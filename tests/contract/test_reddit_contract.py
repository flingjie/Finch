"""Contract test: real opencli reddit JSON output validates against RedditPost model."""
import json
from pathlib import Path

import pytest

from finch.reddit.models import RedditPost

FIXTURE = Path("tests/fixtures/opencli/reddit-search.json")


@pytest.mark.skipif(not FIXTURE.exists(), reason="fixture not captured")
def test_fixture_parses_as_reddit_posts():
    data = json.loads(FIXTURE.read_text())
    assert isinstance(data, list)
    assert len(data) > 0

    posts = [RedditPost.model_validate(item) for item in data]
    assert len(posts) == len(data)

    for p in posts:
        assert p.id
        assert p.title
        assert p.author
        assert p.url
        assert p.url.startswith("https://")


@pytest.mark.skipif(not FIXTURE.exists(), reason="fixture not captured")
def test_fixture_published_at_parses():
    data = json.loads(FIXTURE.read_text())
    for item in data:
        p = RedditPost.model_validate(item)
        if p.created_utc is not None:
            assert p.published_at() is not None, f"failed to parse: {p.created_utc}"
