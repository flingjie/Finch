"""Reddit opencli 客户端单元测试。"""
import json

import pytest

from finch.reddit.models import RedditCommandBlocked, RedditSourceUnavailable
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
