"""Reddit opencli 客户端单元测试。"""
import json

import pytest

from finch.reddit.models import RedditCommandBlocked, RedditError, RedditSourceUnavailable
from finch.reddit.opencli_client import RedditOpenCliClient, _check_allowlist, _parse_posts


class TestCheckAllowlist:
    def test_allows_search(self):
        _check_allowlist(["opencli", "reddit", "search", "query"])

    def test_allows_hot(self):
        _check_allowlist(["opencli", "reddit", "hot"])

    def test_blocks_comment(self):
        with pytest.raises(RedditCommandBlocked):
            _check_allowlist(["opencli", "reddit", "comment", "post-id", "text"])

    def test_blocks_reply(self):
        with pytest.raises(RedditCommandBlocked):
            _check_allowlist(["opencli", "reddit", "reply", "comment-id", "text"])

    def test_blocks_upvote(self):
        with pytest.raises(RedditCommandBlocked):
            _check_allowlist(["opencli", "reddit", "upvote", "post-id"])

    def test_blocks_unknown_command(self):
        with pytest.raises(RedditCommandBlocked):
            _check_allowlist(["opencli", "reddit", "hack"])


class TestParsePosts:
    def test_parses_list(self):
        data = [
            {"id": "1", "title": "a", "author": "u", "url": "https://reddit.com/1"},
            {"id": "2", "title": "b", "author": "v", "url": "https://reddit.com/2"},
        ]
        posts = _parse_posts(json.dumps(data))
        assert len(posts) == 2
        assert posts[0].id == "1"

    def test_parses_single_dict(self):
        data = {"id": "1", "title": "a", "author": "u", "url": "u"}
        posts = _parse_posts(json.dumps(data))
        assert len(posts) == 1

    def test_skips_invalid_items(self):
        data = [
            {"id": "1", "title": "a", "author": "u", "url": "u"},
            {"not": "a", "post": "object"},
        ]
        posts = _parse_posts(json.dumps(data))
        assert len(posts) == 1

    def test_empty_list(self):
        assert _parse_posts(json.dumps([])) == []

    def test_invalid_json_raises(self):
        from finch.reddit.models import RedditError

        with pytest.raises(RedditError):
            _parse_posts("not json")


class TestRedditOpenCliClientSearch:
    def test_search_returns_posts(self, monkeypatch):
        def fake_run(argv, timeout):
            return {
                "ok": True,
                "exit_code": 0,
                "stdout": json.dumps([{"id": "1", "title": "a", "author": "u", "url": "u"}]),
                "stderr": "",
            }

        monkeypatch.setattr("finch.reddit.opencli_client._run", fake_run)
        client = RedditOpenCliClient()
        posts = client.search("agent reliability")
        assert len(posts) == 1
        assert posts[0].id == "1"

    def test_search_passes_correct_argv(self, monkeypatch):
        captured = {}

        def fake_run(argv, timeout):
            captured["argv"] = argv
            return {"ok": True, "exit_code": 0, "stdout": "[]", "stderr": ""}

        monkeypatch.setattr("finch.reddit.opencli_client._run", fake_run)
        client = RedditOpenCliClient()
        client.search("agent evals", sort="hot", limit=10)
        assert captured["argv"][3] == "agent evals"
        assert "--sort" in captured["argv"]
        assert "hot" in captured["argv"]
        assert "--limit" in captured["argv"]
        assert "10" in captured["argv"]
        assert "-f" in captured["argv"]
        assert "json" in captured["argv"]

    def test_search_uses_background_persistent_flags(self, monkeypatch):
        captured = {}

        def fake_run(argv, timeout):
            captured["argv"] = argv
            return {"ok": True, "exit_code": 0, "stdout": "[]", "stderr": ""}

        monkeypatch.setattr("finch.reddit.opencli_client._run", fake_run)
        RedditOpenCliClient().search("hello")
        argv = captured["argv"]
        assert "--window" in argv
        assert argv[argv.index("--window") + 1] == "background"
        assert "--site-session" in argv
        assert argv[argv.index("--site-session") + 1] == "persistent"

    def test_search_not_logged_in_raises(self, monkeypatch):
        def fake_run(argv, timeout):
            return {
                "ok": False,
                "exit_code": 1,
                "stdout": "",
                "stderr": "Error: not logged in to Reddit",
            }

        monkeypatch.setattr("finch.reddit.opencli_client._run", fake_run)
        with pytest.raises(RedditSourceUnavailable):
            RedditOpenCliClient().search("hello")


def _read_row(kind: str, author: str, text: str, score: int = 1) -> dict:
    """``opencli reddit read`` 的真实行结构（无 id/title/url/selftext 字段）。"""
    return {
        "type": kind, "author": author, "score": score, "text": text,
        "post_hint": "self" if kind == "POST" else "",
        "url_overridden_by_dest": "", "preview_image_url": "", "gallery_urls": [],
    }


POST_URL = "https://reddit.com/r/x/comments/p1/some-slug"


class TestRedditOpenCliClientPost:
    def test_maps_read_rows(self, monkeypatch):
        captured = {}

        def fake_run(argv, timeout):
            captured["argv"] = argv
            return {
                "ok": True, "exit_code": 0,
                "stdout": json.dumps([
                    _read_row("POST", "alice", "标题\n\n正文第一段"),
                    _read_row("L0", "bob", "一条回复", score=2),
                    _read_row("L1", "carol", "  > 嵌套回复"),
                ]),
                "stderr": "",
            }

        monkeypatch.setattr("finch.reddit.opencli_client._run", fake_run)
        post = RedditOpenCliClient().post(POST_URL)
        assert post is not None
        assert post.id == "p1"
        assert post.url == POST_URL
        assert post.title == "标题"
        assert post.selftext == "正文第一段"
        assert post.author == "alice"
        assert post.comment_rows == ["一条回复", "> 嵌套回复"]
        assert post.comments == 2
        assert post.truncated is False
        assert captured["argv"][1:4] == ["reddit", "read", POST_URL]
        assert "-f" in captured["argv"] and "json" in captured["argv"]
        assert "--max-length" in captured["argv"]

    def test_content_includes_comments(self, monkeypatch):
        def fake_run(argv, timeout):
            return {
                "ok": True, "exit_code": 0,
                "stdout": json.dumps([
                    _read_row("POST", "alice", "标题\n\n正文"),
                    _read_row("L0", "bob", "一条回复"),
                ]),
                "stderr": "",
            }

        monkeypatch.setattr("finch.reddit.opencli_client._run", fake_run)
        post = RedditOpenCliClient().post(POST_URL)
        assert post is not None
        assert post.content() == "标题\n\n正文\n\n[评论]\n一条回复"

    def test_returns_none_when_no_rows(self, monkeypatch):
        def fake_run(argv, timeout):
            return {"ok": True, "exit_code": 0, "stdout": "[]", "stderr": ""}

        monkeypatch.setattr("finch.reddit.opencli_client._run", fake_run)
        assert RedditOpenCliClient().post(POST_URL) is None

    def test_shape_drift_raises_instead_of_looking_empty(self, monkeypatch):
        """行结构再次变化时报错，不要退化成「post not found」。"""
        def fake_run(argv, timeout):
            return {
                "ok": True, "exit_code": 0,
                "stdout": json.dumps([{"id": "p1", "title": "t", "author": "a",
                                       "url": "https://reddit.com/r/x/comments/p1"}]),
                "stderr": "",
            }

        monkeypatch.setattr("finch.reddit.opencli_client._run", fake_run)
        with pytest.raises(RedditError):
            RedditOpenCliClient().post(POST_URL)

    def test_flags_truncated_body(self, monkeypatch):
        def fake_run(argv, timeout):
            return {
                "ok": True, "exit_code": 0,
                "stdout": json.dumps([
                    _read_row("POST", "alice", "标题\n\n很长的正文\n... [truncated]"),
                ]),
                "stderr": "",
            }

        monkeypatch.setattr("finch.reddit.opencli_client._run", fake_run)
        post = RedditOpenCliClient().post(POST_URL)
        assert post is not None
        assert post.truncated is True
