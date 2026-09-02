"""Unit tests for twitter normalizer."""
from finch.twitter.models import Tweet
from finch.twitter.normalizer import (
    deduplicate,
    filter_noise,
    normalize_tweets,
    normalize_url,
)


def test_normalize_url_strips_query_params():
    assert normalize_url("https://x.com/alice/status/123?ref=foo") == "https://x.com/alice/status/123"


def test_normalize_url_upgrades_http():
    assert normalize_url("http://x.com/alice/status/123") == "https://x.com/alice/status/123"


def test_normalize_url_no_change_when_clean():
    assert normalize_url("https://x.com/alice/status/123") == "https://x.com/alice/status/123"


def test_filter_noise_removes_empty_text():
    tweets = [
        Tweet(id="1", author="a", text="", url="u"),
        Tweet(id="2", author="a", text="   ", url="u"),
        Tweet(id="3", author="a", text="valid", url="u"),
    ]
    result = filter_noise(tweets)
    assert len(result) == 1
    assert result[0].id == "3"


def test_filter_noise_removes_ads():
    tweets = [
        Tweet(id="1", author="a", text="buy now limited time", url="u"),
        Tweet(id="2", author="a", text="normal discussion", url="u"),
    ]
    result = filter_noise(tweets)
    assert len(result) == 1
    assert result[0].id == "2"


def test_filter_noise_keeps_valid_tweets():
    tweets = [
        Tweet(id="1", author="a", text="agent evals are hard", url="u"),
        Tweet(id="2", author="b", text="trajectory evaluation", url="u"),
    ]
    result = filter_noise(tweets)
    assert len(result) == 2


def test_deduplicate_by_id():
    tweets = [
        Tweet(id="1", author="a", text="first", url="u"),
        Tweet(id="1", author="a", text="dup", url="u"),
        Tweet(id="2", author="b", text="second", url="u"),
    ]
    result = deduplicate(tweets)
    assert len(result) == 2
    assert result[0].text == "first"  # first occurrence kept


def test_deduplicate_empty_list():
    assert deduplicate([]) == []


def test_normalize_tweets_pipeline():
    tweets = [
        Tweet(id="1", author="a", text="first", url="u"),
        Tweet(id="1", author="a", text="dup", url="u"),
        Tweet(id="2", author="b", text="", url="u"),
        Tweet(id="3", author="c", text="valid", url="u"),
    ]
    result = normalize_tweets(tweets)
    assert len(result) == 2
    ids = {t.id for t in result}
    assert ids == {"1", "3"}
