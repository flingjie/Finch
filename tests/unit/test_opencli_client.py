"""Unit tests for OpenCliTwitterClient."""
import json

import pytest

from finch.twitter.models import TwitterCommandBlocked, TwitterSourceUnavailable
from finch.twitter.opencli_client import OpenCliClient, _check_allowlist, _parse_tweets


class TestCheckAllowlist:
    def test_allows_search(self):
        _check_allowlist(["opencli", "twitter", "search", "query"])

    def test_allows_timeline(self):
        _check_allowlist(["opencli", "twitter", "timeline"])

    def test_blocks_post(self):
        with pytest.raises(TwitterCommandBlocked):
            _check_allowlist(["opencli", "twitter", "post", "hello"])

    def test_blocks_reply(self):
        with pytest.raises(TwitterCommandBlocked):
            _check_allowlist(["opencli", "twitter", "reply", "123", "text"])

    def test_blocks_like(self):
        with pytest.raises(TwitterCommandBlocked):
            _check_allowlist(["opencli", "twitter", "like", "123"])

    def test_blocks_follow(self):
        with pytest.raises(TwitterCommandBlocked):
            _check_allowlist(["opencli", "twitter", "follow", "user"])

    def test_blocks_browser_click(self):
        with pytest.raises(TwitterCommandBlocked):
            _check_allowlist(["opencli", "twitter", "click"])

    def test_blocks_unknown_command(self):
        with pytest.raises(TwitterCommandBlocked):
            _check_allowlist(["opencli", "twitter", "hack"])


class TestParseTweets:
    def test_parses_list(self):
        data = [
            {"id": "1", "author": "a", "text": "hello", "url": "u"},
            {"id": "2", "author": "b", "text": "world", "url": "u"},
        ]
        tweets = _parse_tweets(json.dumps(data))
        assert len(tweets) == 2
        assert tweets[0].id == "1"

    def test_parses_single_dict(self):
        data = {"id": "1", "author": "a", "text": "hello", "url": "u"}
        tweets = _parse_tweets(json.dumps(data))
        assert len(tweets) == 1

    def test_skips_invalid_items(self):
        data = [
            {"id": "1", "author": "a", "text": "hello", "url": "u"},
            {"not": "a", "tweet": "object"},
        ]
        tweets = _parse_tweets(json.dumps(data))
        assert len(tweets) == 1

    def test_empty_list(self):
        assert _parse_tweets(json.dumps([])) == []

    def test_invalid_json_raises(self):
        from finch.twitter.models import TwitterError

        with pytest.raises(TwitterError):
            _parse_tweets("not json")


class TestOpenCliClientSearch:
    def test_search_returns_tweets(self, monkeypatch):
        def fake_run(argv, timeout):
            return {
                "ok": True,
                "exit_code": 0,
                "stdout": json.dumps([
                    {"id": "1", "author": "a", "text": "hello", "url": "u"},
                ]),
                "stderr": "",
            }

        monkeypatch.setattr("finch.twitter.opencli_client._run", fake_run)
        client = OpenCliClient()
        tweets = client.search("hello")
        assert len(tweets) == 1
        assert tweets[0].id == "1"

    def test_search_passes_correct_argv(self, monkeypatch):
        captured = {}

        def fake_run(argv, timeout):
            captured["argv"] = argv
            return {"ok": True, "exit_code": 0, "stdout": "[]", "stderr": ""}

        monkeypatch.setattr("finch.twitter.opencli_client._run", fake_run)
        client = OpenCliClient()
        client.search("agent evals", product="live", limit=10)
        assert captured["argv"][3] == "agent evals"
        assert "--product" in captured["argv"]
        assert "live" in captured["argv"]
        assert "--limit" in captured["argv"]
        assert "10" in captured["argv"]
        assert "-f" in captured["argv"]
        assert "json" in captured["argv"]

    def test_search_uses_background_persistent_flags(self, monkeypatch):
        captured = {}

        def fake_run(argv, timeout):
            captured["argv"] = argv
            return {"ok": True, "exit_code": 0, "stdout": "[]", "stderr": ""}

        monkeypatch.setattr("finch.twitter.opencli_client._run", fake_run)
        OpenCliClient().search("hello")
        argv = captured["argv"]
        assert "--window" in argv
        assert argv[argv.index("--window") + 1] == "background"
        assert "--site-session" in argv
        assert argv[argv.index("--site-session") + 1] == "persistent"

    def test_search_honors_opencli_window_env(self, monkeypatch):
        monkeypatch.setenv("OPENCLI_WINDOW", "foreground")
        captured = {}

        def fake_run(argv, timeout):
            captured["argv"] = argv
            return {"ok": True, "exit_code": 0, "stdout": "[]", "stderr": ""}

        monkeypatch.setattr("finch.twitter.opencli_client._run", fake_run)
        OpenCliClient().search("hello")
        argv = captured["argv"]
        assert argv[argv.index("--window") + 1] == "foreground"

    def test_search_not_logged_in_raises(self, monkeypatch):
        def fake_run(argv, timeout):
            return {
                "ok": False,
                "exit_code": 1,
                "stdout": "",
                "stderr": "Error: not logged in to Twitter",
            }

        monkeypatch.setattr("finch.twitter.opencli_client._run", fake_run)
        client = OpenCliClient()
        with pytest.raises(TwitterSourceUnavailable):
            client.search("hello")

    def test_search_bridge_offline_raises(self, monkeypatch):
        def fake_run(argv, timeout):
            return {
                "ok": False,
                "exit_code": 1,
                "stdout": "",
                "stderr": "Browser bridge connection refused",
            }

        monkeypatch.setattr("finch.twitter.opencli_client._run", fake_run)
        client = OpenCliClient()
        with pytest.raises(TwitterSourceUnavailable):
            client.search("hello")


class TestOpenCliClientBookmarks:
    def test_bookmarks_returns_tweets(self, monkeypatch):
        def fake_run(argv, timeout):
            return {
                "ok": True,
                "exit_code": 0,
                "stdout": json.dumps([
                    {"id": "1", "author": "a", "text": "bookmark", "url": "u"},
                ]),
                "stderr": "",
            }

        monkeypatch.setattr("finch.twitter.opencli_client._run", fake_run)
        client = OpenCliClient()
        tweets = client.bookmarks(limit=5)
        assert len(tweets) == 1


class TestOpenCliClientThread:
    def test_thread_returns_tweets(self, monkeypatch):
        def fake_run(argv, timeout):
            return {
                "ok": True,
                "exit_code": 0,
                "stdout": json.dumps([
                    {"id": "1", "author": "a", "text": "thread", "url": "u"},
                ]),
                "stderr": "",
            }

        monkeypatch.setattr("finch.twitter.opencli_client._run", fake_run)
        client = OpenCliClient()
        tweets = client.thread("https://x.com/a/status/1")
        assert len(tweets) == 1


class TestOpenCliClientTimeline:
    def test_timeline_returns_tweets(self, monkeypatch):
        def fake_run(argv, timeout):
            return {
                "ok": True,
                "exit_code": 0,
                "stdout": json.dumps([
                    {"id": "1", "author": "a", "text": "timeline", "url": "u"},
                ]),
                "stderr": "",
            }

        monkeypatch.setattr("finch.twitter.opencli_client._run", fake_run)
        client = OpenCliClient()
        tweets = client.timeline(limit=5)
        assert len(tweets) == 1


class TestOpenCliClientProfile:
    def test_profile_returns_tweet(self, monkeypatch):
        def fake_run(argv, timeout):
            return {
                "ok": True,
                "exit_code": 0,
                "stdout": json.dumps([
                    {"id": "1", "author": "alice", "text": "profile", "url": "u"},
                ]),
                "stderr": "",
            }

        monkeypatch.setattr("finch.twitter.opencli_client._run", fake_run)
        client = OpenCliClient()
        result = client.profile("alice")
        assert result is not None
        assert result.author == "alice"

    def test_profile_empty_returns_none(self, monkeypatch):
        def fake_run(argv, timeout):
            return {"ok": True, "exit_code": 0, "stdout": "[]", "stderr": ""}

        monkeypatch.setattr("finch.twitter.opencli_client._run", fake_run)
        client = OpenCliClient()
        assert client.profile("alice") is None
