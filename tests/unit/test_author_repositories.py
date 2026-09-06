"""Author 仓储测试（P0 Task 2）：save + 按主键 get + list，均幂等。"""

from datetime import UTC, datetime

from finch.author.models import (
    AuthorAccount,
    AuthorPost,
    AuthorSyncCursor,
    PublicationIntent,
    PublicationLink,
)
from finch.storage.database import Store
from finch.storage.repositories import (
    AuthorAccountRepository,
    AuthorPostRepository,
    AuthorSyncCursorRepository,
    PublicationIntentRepository,
    PublicationLinkRepository,
)


def test_author_post_upsert_idempotent(tmp_path):
    store = Store(tmp_path / "finch.db")
    store.init()
    repo = AuthorPostRepository(store)
    post = AuthorPost(
        platform="x", remote_post_id="p1", author_account_id="a1", kind="original",
        body="hi", url="u", published_at=datetime.now(UTC),
    )
    repo.save(post)
    repo.save(post)
    assert len(repo.list()) == 1
    assert repo.get("x", "p1") is not None


def test_publication_intent_and_link(tmp_path):
    store = Store(tmp_path / "finch.db")
    store.init()
    intent_repo = PublicationIntentRepository(store)
    link_repo = PublicationLinkRepository(store)
    intent = PublicationIntent(
        source_type="draft", source_id="d1", approved_body="b", content_hash="h",
        approved_at=datetime.now(UTC), expected_kind="original",
    )
    intent_repo.save(intent)
    assert intent_repo.get("d1") is not None
    link_repo.save(PublicationLink(
        source_id="d1", remote_post_id="p1", matched_by="exact_text",
        confidence=1.0, linked_at=datetime.now(UTC),
    ))
    assert link_repo.get("d1").remote_post_id == "p1"


def test_author_account_save_get_list(tmp_path):
    store = Store(tmp_path / "finch.db")
    store.init()
    repo = AuthorAccountRepository(store)
    acc = AuthorAccount(platform="x", user_id="u1", handle="h", verified_at=datetime.now(UTC))
    repo.save(acc)
    repo.save(acc)
    assert len(repo.list()) == 1
    got = repo.get("x", "u1")
    assert got is not None and got.handle == "h"


def test_author_sync_cursor_save_get_list(tmp_path):
    store = Store(tmp_path / "finch.db")
    store.init()
    repo = AuthorSyncCursorRepository(store)
    cur = AuthorSyncCursor(platform="x", author_account_id="a1", last_seen=datetime.now(UTC))
    repo.save(cur)
    repo.save(cur)
    assert len(repo.list()) == 1
    got = repo.get("x", "a1")
    assert got is not None and got.author_account_id == "a1"
