from datetime import UTC, datetime

from finch.author.models import PublicationIntent


def test_publication_intent_shape():
    intent = PublicationIntent(
        source_type="draft", source_id="d1", approved_body="body",
        content_hash="h", approved_at=datetime.now(UTC), expected_kind="original",
    )
    assert intent.expected_kind == "original"
