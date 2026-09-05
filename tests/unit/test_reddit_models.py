"""Reddit 数据模型单元测试。"""

from datetime import UTC, datetime

from finch.reddit.models import RedditCommandBlocked, RedditPost


def _post(**overrides) -> RedditPost:
    data = dict(
        id="1abc23",
        title="How do you test agent reliability?",
        subreddit="LocalLLaMA",
        author="alice",
        score=128,
        comments=34,
        url="https://www.reddit.com/r/LocalLLaMA/comments/1abc23/title/",
        created_utc=1756627200,
        selftext="We run a failure replay harness.",
    )
    data.update(overrides)
    return RedditPost(**data)


def test_parses_minimal_post():
    post = RedditPost(id="1", title="t", author="a", url="u")
    assert post.id == "1"
    assert post.score == 0
    assert post.comments == 0
    assert post.selftext == ""
    assert post.subreddit is None


def test_coerces_string_counts_and_none_selftext():
    post = _post(score="128", comments="34", selftext=None)
    assert post.score == 128
    assert post.comments == 34
    assert post.selftext == ""


def test_published_at_parses_unix_seconds():
    assert _post(created_utc=1756627200).published_at() == datetime(2025, 8, 31, 8, 0, tzinfo=UTC)


def test_published_at_parses_string_and_float():
    assert _post(created_utc="1756627200").published_at() is not None
    assert _post(created_utc=1756627200.0).published_at() is not None


def test_published_at_none_when_missing_or_invalid():
    assert _post(created_utc=None).published_at() is None
    assert _post(created_utc="not-a-number").published_at() is None


def test_content_title_only_when_selftext_empty():
    assert _post(title="A question", selftext="").content() == "A question"


def test_content_title_plus_truncated_selftext():
    post = _post(title="A question", selftext="x" * 1000)
    content = post.content()
    assert content.startswith("A question\n\n")
    body = content.split("\n\n", 1)[1]
    assert len(body) == 500


def test_command_blocked_error_code():
    assert RedditCommandBlocked().error_code == "COMMAND_BLOCKED"
