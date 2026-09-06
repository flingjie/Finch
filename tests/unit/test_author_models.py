from datetime import UTC, datetime

from finch.author.models import AuthorPost, AuthorSyncCursor, PublicationIntent


def test_author_post_metrics_default_none():
    post = AuthorPost(
        platform="x", remote_post_id="p1", author_account_id="a1",
        kind="original", body="hi", url="u", published_at=datetime.now(UTC),
    )
    assert post.likes is None and post.views is None
    assert post.replied_to_post_id is None


def test_publication_intent_shape():
    intent = PublicationIntent(
        source_type="draft", source_id="d1", approved_body="body",
        content_hash="h", approved_at=datetime.now(UTC), expected_kind="original",
    )
    assert intent.expected_kind == "original"


def test_author_sync_cursor():
    cur = AuthorSyncCursor(platform="x", author_account_id="a1", last_seen=datetime.now(UTC))
    assert cur.last_seen is not None
