"""产品确定性打分的单测。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from finch.products.scoring import score_product
from finch.sources.fingerprint import artifact_id, content_fingerprint
from finch.sources.models import AuthorIdentity, RawArtifact, Source


def _art(sid: str, text: str, *, published_at: datetime | None = None) -> RawArtifact:
    url = f"https://x.com/alice/status/{sid}"
    return RawArtifact(
        artifact_id=artifact_id("twitter", "post", sid),
        source=Source.TWITTER,
        source_type="post",
        source_id=sid,
        canonical_url=url,
        author_identity=AuthorIdentity(platform="x", external_id="alice", handle="alice"),
        text=text,
        published_at=published_at,
        retrieved_at=datetime.now(UTC),
        capture_method="adapter",
        content_fingerprint=content_fingerprint(text, url=url),
    )


def test_score_is_deterministic_and_clamped():
    arts = [_art("1", "I built an agent harness, we launched it last week")]
    a = score_product(arts, now=datetime.now(UTC))
    b = score_product(arts, now=datetime.now(UTC))
    assert a == b
    assert 0.0 <= a.total <= 1.0


def test_launch_and_founder_signals_boost_score():
    now = datetime.now(UTC)
    strong = _art(
        "1",
        "We launched a new product for people who struggle with QA. "
        "I built it to solve the pain of flaky tests.",
        published_at=now - timedelta(days=1),
    )
    weak = _art(
        "2",
        "someone shared a link",
        published_at=now - timedelta(days=40),
    )
    strong_score = score_product([strong], now=now)
    weak_score = score_product([weak], now=now)
    assert strong_score.total > weak_score.total
    assert strong_score.launch_signal == 1.0
    assert strong_score.founder_build == 1.0
    assert strong_score.problem_clarity == 1.0


def test_evidence_depth_counts_distinct_artifacts():
    now = datetime.now(UTC)
    one = score_product([_art("1", "we launched a product")], now=now)
    three = score_product(
        [
            _art("1", "we launched a product"),
            _art("2", "we launched a product"),
            _art("3", "we launched a product"),
        ],
        now=now,
    )
    assert three.evidence_depth > one.evidence_depth
