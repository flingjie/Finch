"""Unit tests for finch.author.client decode functions."""

from finch.author.client import _int_or_none, decode_author_post


def test_decode_author_post_minimal():
    raw = {"id": "123", "author": "flingjie", "text": "hello", "url": "https://x.com/1"}
    post = decode_author_post("x", "a1", raw)
    assert post.remote_post_id == "123"
    assert post.kind == "original"
    assert post.likes is None  # 缺失字段 → None，而非 0


def test_decode_author_post_reply():
    raw = {"id": "456", "text": "re", "url": "u", "in_reply_to": "123"}
    post = decode_author_post("x", "a1", raw)
    assert post.kind == "reply" and post.replied_to_post_id == "123"


def test_decode_author_post_quote():
    raw = {
        "id": "789",
        "text": "qt",
        "url": "u",
        "quoted_tweet": {"id": "456"},
    }
    post = decode_author_post("x", "a1", raw)
    assert post.kind == "quote"
    assert post.quoted_post_id == "456"


def test_int_or_none():
    assert _int_or_none("42") == 42
    assert _int_or_none(42) == 42
    assert _int_or_none(None) is None
    assert _int_or_none("") is None
    assert _int_or_none("abc") is None
