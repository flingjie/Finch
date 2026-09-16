"""Unit tests for deterministic artifact filtering."""

from __future__ import annotations

from datetime import UTC, datetime

from finch.discovery.filtering import (
    DUPLICATE,
    EMPTY_CONTENT,
    EXCLUDED_CONTENT,
    INVALID_PLATFORM,
    NO_AUTHOR,
    REPOST,
    filter_artifacts,
    matches_excluded,
)
from finch.sources.fingerprint import artifact_id, content_fingerprint
from finch.sources.models import AuthorIdentity, RawArtifact, Source


def _art(
    sid: str,
    *,
    platform: str = "x",
    author: str = "alice",
    text: str = "some long enough original content about agents",
    source: Source = Source.TWITTER,
    source_type: str = "post",
) -> RawArtifact:
    url = f"https://{platform}.example/{author}/{sid}"
    return RawArtifact(
        artifact_id=artifact_id(source.value, source_type, sid),
        source=source,
        source_type=source_type,
        source_id=sid,
        canonical_url=url,
        author_identity=AuthorIdentity(platform=platform, external_id=author, handle=author),
        text=text,
        retrieved_at=datetime.now(UTC),
        content_fingerprint=content_fingerprint(text, url=url),
    )


def test_keeps_valid_artifact():
    arts = [_art("1")]
    kept, counts = filter_artifacts(arts)
    assert len(kept) == 1
    assert counts == {}


def test_empty_content_rejected():
    kept, counts = filter_artifacts([_art("1", text="hi")])
    assert kept == []
    assert counts.get(EMPTY_CONTENT) == 1


def test_no_author_rejected():
    kept, counts = filter_artifacts([_art("1", author="")])
    assert kept == []
    assert counts.get(NO_AUTHOR) == 1


def test_invalid_platform_rejected():
    kept, counts = filter_artifacts([_art("1", platform="linkedin")])
    assert kept == []
    assert counts.get(INVALID_PLATFORM) == 1


def test_repost_rejected():
    kept, counts = filter_artifacts([_art("1", text="RT @someone look at this")])
    assert kept == []
    assert counts.get(REPOST) == 1


def test_excluded_content_rejected():
    kept, counts = filter_artifacts(
        [_art("1", text="breaking AI news about a funding round")],
        excluded_content=["AI news", "funding and valuation"],
    )
    assert kept == []
    assert counts.get(EXCLUDED_CONTENT) == 1


def test_duplicate_fingerprint_rejected():
    a = _art("1", text="original content about agent reliability")
    b = _art("2", text="original content about agent reliability")
    b = b.model_copy(update={"content_fingerprint": a.content_fingerprint})
    kept, counts = filter_artifacts([a, b])
    assert len(kept) == 1
    assert counts.get(DUPLICATE) == 1


def test_matches_excluded_case_insensitive():
    assert matches_excluded("A Great FUNDING round", ["funding"])
    assert not matches_excluded("A great build in public", ["funding"])


def test_excluded_content_covers_marketing_and_funding():
    # 新闻 / 融资 / 纯转发 / 营销 / 课程 全部被硬过滤。
    junk = [
        _art("n1", text="latest AI news roundup"),
        _art("n2", text="startup raised a huge funding round"),
        _art("n3", text="RT @ceo our product is great"),
        _art("n4", text="sign up for my course promotion about agents"),
    ]
    kept, counts = filter_artifacts(
        junk,
        excluded_content=["AI news", "funding", "course promotion", "pure repost"],
    )
    assert kept == []
    assert counts.get(EXCLUDED_CONTENT, 0) + counts.get(REPOST, 0) >= 3
