"""Tests for deterministic related-hot-post selection."""

from __future__ import annotations

from datetime import UTC, datetime

from finch.discovery.hot_posts import select_hot_posts
from finch.settings import Settings
from finch.sources.fingerprint import artifact_id, content_fingerprint
from finch.sources.models import AuthorIdentity, RawArtifact, Source


def _art(
    sid: str,
    *,
    platform: str = "x",
    author: str = "alice",
    text: str,
    title: str = "",
    likes: int = 0,
    views: int = 0,
    score: int = 0,
    comments: int = 0,
) -> RawArtifact:
    url = f"https://{platform}.example/{author}/{sid}"
    source = Source.TWITTER if platform == "x" else Source.REDDIT
    source_type = "post"
    metrics: dict[str, object] = {}
    if platform == "x":
        metrics = {"likes": likes, "views": views}
    else:
        metrics = {"score": score, "comments": comments}
    return RawArtifact(
        artifact_id=artifact_id(source.value, source_type, sid),
        source=source,
        source_type=source_type,
        source_id=sid,
        canonical_url=url,
        author_identity=AuthorIdentity(
            platform=platform, external_id=author, handle=author
        ),
        title=title or None,
        text=text,
        published_at=datetime.now(UTC),
        retrieved_at=datetime.now(UTC),
        metrics=metrics,
        content_fingerprint=content_fingerprint(text, url=url),
    )


def _settings() -> Settings:
    return Settings(
        interests={
            "long_term_interests": ["agent reliability", "failure replay"],
            "explore_directions": ["agent observability"],
            "adjacent_queries": ["distributed systems"],
        },
        paths={"var_dir": "/tmp/finch-hot-posts-test"},
    )


def test_related_posts_rank_before_hot_but_unrelated_posts():
    arts = [
        _art("a", text="agent reliability and failure replay in production", likes=1),
        _art("b", text="gardening tips for a small balcony", likes=1000),
        _art("c", text="failure replay gives a smaller test case", likes=10),
    ]
    selected = select_hot_posts(
        arts,
        settings=_settings(),
        question="how do people make failure replay reproducible?",
        limit=5,
    )
    assert [p.artifact_id for p in selected] == [
        "twitter:post:a",
        "twitter:post:c",
        "twitter:post:b",
    ]
    assert selected[0].matched_terms


def test_hot_posts_are_capped_and_heat_orders_within_relevance():
    arts = [
        _art("1", text="agent reliability failure replay one", likes=1),
        _art("2", text="agent reliability failure replay two", likes=2),
        _art("3", text="agent reliability failure replay three", likes=9),
        _art("4", text="agent reliability failure replay four", likes=5),
        _art("5", text="agent reliability failure replay five", likes=7),
        _art("6", text="agent reliability failure replay six", likes=8),
    ]
    selected = select_hot_posts(arts, settings=_settings(), limit=5)
    assert len(selected) == 5
    assert [p.artifact_id for p in selected] == [
        "twitter:post:3",
        "twitter:post:6",
        "twitter:post:5",
        "twitter:post:4",
        "twitter:post:2",
    ]


def test_empty_artifacts_return_empty():
    assert select_hot_posts([], settings=_settings(), limit=5) == []
