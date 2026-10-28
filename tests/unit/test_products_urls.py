"""产品身份抽取（urls.py）单测。"""

from __future__ import annotations

from datetime import UTC, datetime

from finch.products.urls import (
    classify_product_url,
    first_product_url,
    normalize_product_key,
    product_key_for,
    product_url_for_key,
    profile_url_for,
)
from finch.sources.fingerprint import artifact_id, content_fingerprint
from finch.sources.models import AuthorIdentity, RawArtifact, Source


def _art(sid: str, url: str, text: str = "") -> RawArtifact:
    return RawArtifact(
        artifact_id=artifact_id("twitter", "post", sid),
        source=Source.TWITTER,
        source_type="post",
        source_id=sid,
        canonical_url=url,
        author_identity=AuthorIdentity(platform="x", external_id="alice", handle="alice"),
        text=text,
        retrieved_at=datetime.now(UTC),
        capture_method="adapter",
        content_fingerprint=content_fingerprint(text or url, url=url),
    )


def test_normalize_and_classify():
    assert normalize_product_key("https://github.com/acme/widget/") == "github.com/acme/widget"
    assert classify_product_url("https://github.com/acme/widget") == "github.com/acme/widget"
    assert classify_product_url("https://www.producthunt.com/posts/foo") is not None
    assert classify_product_url("https://x.com/alice/status/1") is None  # social


def test_canonical_product_url_wins():
    art = _art("1", "https://github.com/acme/widget")
    assert product_key_for(art) == "github.com/acme/widget"


def test_social_canonical_falls_back_to_text_link():
    art = _art(
        "2",
        "https://x.com/alice/status/2",
        "check out https://github.com/acme/widget",
    )
    assert product_key_for(art) == "github.com/acme/widget"


def test_self_contained_launch_post_uses_own_url():
    art = _art(
        "3",
        "https://x.com/alice/status/3",
        "I built a thing, no product link yet",
    )
    assert product_key_for(art) == "x.com/alice/status/3"


def test_profile_url_for_platforms():
    assert profile_url_for("x", handle="@alice") == "https://x.com/alice"
    assert profile_url_for("github", external_id="acme") == "https://github.com/acme"
    assert profile_url_for("reddit", handle="alice") == "https://www.reddit.com/user/alice"
    assert profile_url_for("weixin", handle="alice") is None


def test_product_url_for_key_rejects_social_links():
    assert product_url_for_key("github.com/acme/widget") == "https://github.com/acme/widget"
    assert product_url_for_key("x.com/alice/status/3") is None
    assert product_url_for_key("t.co/abc") is None


def test_first_product_url_prefers_non_social_link():
    art = _art(
        "4",
        "https://x.com/alice/status/4",
        "we shipped https://github.com/acme/widget today",
    )
    assert first_product_url([art]) == "https://github.com/acme/widget"
