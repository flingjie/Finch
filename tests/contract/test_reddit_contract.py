"""Contract test: real opencli reddit JSON output validates against RedditPost model."""
import json
from pathlib import Path

import pytest

from finch.reddit.models import RedditPost
from finch.reddit.opencli_client import _post_from_read_rows

FIXTURE = Path("tests/fixtures/opencli/reddit-search.json")
READ_FIXTURE = Path("tests/fixtures/opencli/reddit-read.json")


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


@pytest.mark.skipif(not READ_FIXTURE.exists(), reason="fixture not captured")
def test_read_fixture_maps_to_post():
    """``reddit read`` 行没有 id/title/url/selftext；客户端必须自己映射。"""
    rows = json.loads(READ_FIXTURE.read_text())
    url = "https://www.reddit.com/r/AgentContext_dev/comments/1wxbic4/slug/"
    post = _post_from_read_rows(rows, url)
    assert post is not None
    assert post.id == "1wxbic4"
    assert post.url == url
    assert post.title.startswith("From Hand-Fed Prompts to Automated Systems")
    assert "Loop engineering belongs to the second group." in post.selftext
    assert post.author == "javaeeeee"
    assert post.comment_rows == []
    assert post.truncated is False
