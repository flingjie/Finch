"""Contract test: real opencli JSON output validates against Tweet model."""
import json
from pathlib import Path

import pytest

from finch.twitter.models import Tweet

FIXTURE = Path("tests/fixtures/opencli/twitter-search.json")


@pytest.mark.skipif(not FIXTURE.exists(), reason="fixture not captured")
def test_fixture_parses_as_tweets():
    data = json.loads(FIXTURE.read_text())
    assert isinstance(data, list)
    assert len(data) > 0

    tweets = [Tweet.model_validate(item) for item in data]
    assert len(tweets) == len(data)

    # Every tweet must have id, author, text, url
    for t in tweets:
        assert t.id
        assert t.author
        assert t.text
        assert t.url
        assert t.url.startswith("https://x.com/")


@pytest.mark.skipif(not FIXTURE.exists(), reason="fixture not captured")
def test_fixture_views_coerced():
    data = json.loads(FIXTURE.read_text())
    for item in data:
        t = Tweet.model_validate(item)
        assert isinstance(t.views, int)


@pytest.mark.skipif(not FIXTURE.exists(), reason="fixture not captured")
def test_fixture_published_at_parses():
    data = json.loads(FIXTURE.read_text())
    for item in data:
        t = Tweet.model_validate(item)
        if t.created_at:
            dt = t.published_at()
            assert dt is not None, f"failed to parse: {t.created_at}"


@pytest.mark.skipif(not FIXTURE.exists(), reason="fixture not captured")
def test_fixture_quoted_tweet_handled():
    data = json.loads(FIXTURE.read_text())
    for item in data:
        t = Tweet.model_validate(item)
        # quoted_tweet may be None or a dict — both should work
        if t.quoted_tweet is not None:
            assert t.quoted_tweet.id
            assert t.quoted_tweet.author
            assert t.quoted_tweet.text
