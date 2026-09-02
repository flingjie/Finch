"""Unit tests for twitter models."""


from finch.twitter.models import (
    DiscussionCandidate,
    QuotedTweet,
    Tweet,
    TwitterCommandBlocked,
    TwitterError,
    TwitterRateLimited,
    TwitterSourceUnavailable,
    to_candidate,
)


def test_tweet_parses_basic_fields():
    t = Tweet(
        id="123",
        author="alice",
        text="hello world",
        created_at="Wed Sep 02 06:05:25 +0000 2026",
        likes=5,
        views="42",
        url="https://x.com/alice/status/123",
    )
    assert t.id == "123"
    assert t.author == "alice"
    assert t.likes == 5
    assert t.views == 42  # string coerced to int
    assert t.published_at() is not None


def test_tweet_views_coerce_empty_string():
    t = Tweet(id="1", author="a", text="t", views="", url="u")
    assert t.views == 0


def test_tweet_views_coerce_none():
    t = Tweet(id="1", author="a", text="t", views=None, url="u")  # type: ignore[arg-type]
    assert t.views == 0


def test_tweet_published_at_parses_valid():
    t = Tweet(
        id="1", author="a", text="t",
        created_at="Wed Sep 02 06:05:25 +0000 2026",
        url="u",
    )
    dt = t.published_at()
    assert dt is not None
    assert dt.year == 2026
    assert dt.month == 9
    assert dt.day == 2


def test_tweet_published_at_returns_none_on_bad_format():
    t = Tweet(id="1", author="a", text="t", created_at="not-a-date", url="u")
    assert t.published_at() is None


def test_tweet_published_at_returns_none_when_missing():
    t = Tweet(id="1", author="a", text="t", created_at=None, url="u")  # type: ignore[arg-type]
    assert t.published_at() is None


def test_quoted_tweet_parses():
    q = QuotedTweet(
        id="456",
        author="bob",
        text="quoted text",
        url="https://x.com/bob/status/456",
    )
    assert q.author == "bob"


def test_tweet_with_quoted_tweet():
    t = Tweet(
        id="123",
        author="alice",
        text="main tweet",
        url="u",
        quoted_tweet=QuotedTweet(
            id="456", author="bob", text="quoted", url="u2",
        ),
    )
    assert t.quoted_tweet is not None
    assert t.quoted_tweet.author == "bob"


def test_to_candidate_basic():
    t = Tweet(id="1", author="a", text="hello", url="https://x.com/a/status/1")
    c = to_candidate(t, query_id="q1")
    assert isinstance(c, DiscussionCandidate)
    assert c.id == "1"
    assert c.author_handle == "a"
    assert c.text == "hello"
    assert c.query_id == "q1"
    assert c.source == "twitter"


def test_to_candidate_with_published_at():
    t = Tweet(
        id="1", author="a", text="hello",
        created_at="Wed Sep 02 06:05:25 +0000 2026",
        url="u",
    )
    c = to_candidate(t)
    assert c.published_at is not None


def test_error_codes():
    assert TwitterError("x").error_code == "TWITTER_ERROR"
    assert TwitterSourceUnavailable("x").error_code == "TWITTER_SOURCE_UNAVAILABLE"
    assert TwitterRateLimited("x").error_code == "RATE_LIMITED"
    assert TwitterCommandBlocked("x").error_code == "COMMAND_BLOCKED"
