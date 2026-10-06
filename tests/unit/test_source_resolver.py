"""SourceResolver：text/file/url → 规范化正文 + hash + 样本数 + 覆盖缺口。"""

from finch.ingest.resolver import SourceResolver


class _T:
    def __init__(self, text, url, author="a", quoted_tweet=None, has_media=False):
        self.text = text
        self.url = url
        self.author = author
        self.quoted_tweet = quoted_tweet
        self.has_media = has_media
        self.media_urls = []


class _Quote:
    def __init__(self, author, text, url):
        self.author = author
        self.text = text
        self.url = url


class _X:
    def thread(self, url, *, limit=50):
        return [
            _T("你好", "https://x.com/a/1", author="alice"),
            _T(
                "世界",
                "https://x.com/a/2",
                author="bob",
                quoted_tweet=_Quote("carol", "引用的话", "https://x.com/c/9"),
            ),
            _T("附图", "https://x.com/a/3", author="dave", has_media=True),
        ]


class _Reddit:
    def __init__(self, selftext="正文"):
        self.selftext = selftext

    def post(self, url):
        from finch.reddit.models import RedditPost

        return RedditPost(id="p1", title="标题", author="a", url=url, selftext=self.selftext)


class _Web:
    def fetch(self, url):
        return "网页正文"


def _resolver():
    return SourceResolver(_X(), _Reddit(), _Web())


def test_resolve_text():
    s = _resolver().resolve_text("一句话")
    assert s.body == "一句话"
    assert s.sample_size == 1
    assert s.source_type == "text"
    assert s.coverage == []


def test_resolve_file_multi_sample(tmp_path):
    p = tmp_path / "posts.md"
    p.write_text("第一篇\n\n---\n\n第二篇\n\n---\n\n第三篇")
    s = _resolver().resolve_file(str(p))
    assert s.sample_size == 3
    assert s.source_type == "file"
    assert "---" in s.body


def test_resolve_url_routes_x():
    s = _resolver().resolve_url("https://x.com/a/status/1")
    assert s.source_type == "url"
    assert "你好" in s.body and "世界" in s.body


def test_resolve_url_x_preserves_attribution():
    s = _resolver().resolve_url("https://x.com/a/status/1")
    assert "[1] @alice" in s.body
    assert "[2] @bob" in s.body
    assert "[引用 @carol]" in s.body
    assert "引用的话" in s.body


def test_resolve_url_x_media_coverage():
    s = _resolver().resolve_url("https://x.com/a/status/1")
    assert any("媒体" in g for g in s.coverage)


def test_resolve_url_scheme_less():
    s = _resolver().resolve_url("x.com/a/status/1")
    assert s.source_type == "url"
    assert "你好" in s.body


def test_resolve_url_routes_reddit():
    s = _resolver().resolve_url("https://reddit.com/r/x/comments/p1")
    assert "标题" in s.body and "正文" in s.body


def test_resolve_url_reddit_full_selftext():
    long_text = "x" * 1000
    r = SourceResolver(_X(), _Reddit(long_text), _Web())
    s = r.resolve_url("https://reddit.com/r/x/comments/p1")
    assert long_text in s.body


def test_resolve_url_reddit_empty_selftext_coverage():
    r = SourceResolver(_X(), _Reddit(""), _Web())
    s = r.resolve_url("https://reddit.com/r/x/comments/p1")
    assert any("无正文" in g for g in s.coverage)


def test_resolve_url_routes_web():
    s = _resolver().resolve_url("https://example.com/post")
    assert s.body == "网页正文"
