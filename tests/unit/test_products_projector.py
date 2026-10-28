"""产品投影（projector.py）单测：工件按产品 key 聚类。"""

from __future__ import annotations

from datetime import UTC, datetime

from finch.products.projector import ProductProjector
from finch.sources.fingerprint import artifact_id, content_fingerprint
from finch.sources.models import AuthorIdentity, RawArtifact, Source


def _art(sid: str, author: str, url: str, text: str) -> RawArtifact:
    return RawArtifact(
        artifact_id=artifact_id("twitter", "post", sid),
        source=Source.TWITTER,
        source_type="post",
        source_id=sid,
        canonical_url=url,
        author_identity=AuthorIdentity(platform="x", external_id=author, handle=author),
        text=text,
        retrieved_at=datetime.now(UTC),
        capture_method="adapter",
        content_fingerprint=content_fingerprint(text, url=url),
    )


def test_clusters_artifacts_by_product_key():
    arts = [
        _art("1", "alice", "https://github.com/acme/widget", "launch of widget"),
        _art("2", "bob", "https://github.com/acme/widget", "widget looks useful"),
        _art("3", "carol", "https://github.com/other/tool", "other tool launched"),
    ]
    clusters = ProductProjector().build_product_candidates(arts)
    by_key = {c.product.key: c for c in clusters}
    assert set(by_key) == {"github.com/acme/widget", "github.com/other/tool"}
    assert len(by_key["github.com/acme/widget"].artifacts) == 2
    assert by_key["github.com/acme/widget"].product.founder is not None
    assert by_key["github.com/acme/widget"].product.product_url == (
        "https://github.com/acme/widget"
    )


def test_founder_from_first_artifact():
    arts = [
        _art("1", "alice", "https://github.com/acme/widget", "launch"),
        _art("2", "alice", "https://github.com/acme/widget", "update"),
    ]
    cluster = ProductProjector().build_product_candidates(arts)[0]
    assert cluster.product.founder.display_name == "alice"
    assert cluster.product.founder.handles[0].url == "https://x.com/alice"
    assert cluster.product.fingerprint
