"""SourceResolver：text/file/url → 规范化正文 + hash + 样本数。"""

from finch.style.source_resolver import SourceResolver


class _T:
    def __init__(self, text, url):
        self.text = text
        self.url = url


class _X:
    def thread(self, url, *, limit=50):
        return [_T("你好", "https://x.com/a/1"), _T("世界", "https://x.com/a/2")]


class _Reddit:
    def post(self, url):
        from finch.reddit.models import RedditPost

        return RedditPost(id="p1", title="标题", author="a", url=url, selftext="正文")


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
    assert s.content_hash == s.content_hash  # 稳定


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


def test_resolve_url_scheme_less():
    s = _resolver().resolve_url("x.com/a/status/1")
    assert s.source_type == "url"
    assert "你好" in s.body


def test_resolve_url_routes_reddit():
    s = _resolver().resolve_url("https://reddit.com/r/x/comments/p1")
    assert "标题" in s.body and "正文" in s.body


def test_resolve_url_routes_web():
    s = _resolver().resolve_url("https://example.com/post")
    assert s.body == "网页正文"
