from datetime import UTC, datetime, timedelta

from finch.author.models import AuthorPost, PublicationIntent
from finch.author.reconcile import reconcile
from finch.storage.database import Store
from finch.storage.repositories import (
    AuthorPostRepository,
    PublicationIntentRepository,
    PublicationLinkRepository,
)


def _intent(source_id="d1", body="hello world"):
    return PublicationIntent(
        source_type="draft", source_id=source_id, approved_body=body, content_hash="h",
        approved_at=datetime.now(UTC) - timedelta(days=1), expected_kind="original",
    )


def _post(pid, body, published_at=None):
    return AuthorPost(
        platform="x", remote_post_id=pid, author_account_id="a1", kind="original",
        body=body, url=f"u/{pid}", published_at=published_at or datetime.now(UTC),
    )


def test_reconcile_exact_match(tmp_path):
    store = Store(tmp_path / "finch.db")
    store.init()
    PublicationIntentRepository(store).save(_intent())
    AuthorPostRepository(store).save(_post("p1", "hello world"))
    result = reconcile(store)
    assert len(result.linked) == 1 and result.linked[0].matched_by == "exact_text"


def test_reconcile_similar_unique(tmp_path):
    store = Store(tmp_path / "finch.db")
    store.init()
    PublicationIntentRepository(store).save(_intent(body="hello world this is a test"))
    AuthorPostRepository(store).save(_post("p1", "hello world this is the test"))
    result = reconcile(store)
    assert len(result.linked) == 1 and result.linked[0].matched_by == "similar_text"


def test_reconcile_multiple_needs_manual(tmp_path):
    store = Store(tmp_path / "finch.db")
    store.init()
    PublicationIntentRepository(store).save(_intent())
    AuthorPostRepository(store).save(_post("p1", "hello world"))
    AuthorPostRepository(store).save(_post("p2", "hello world"))
    result = reconcile(store)
    assert result.linked == [] and len(result.needs_manual) == 1


def test_reconcile_no_candidate_awaiting(tmp_path):
    store = Store(tmp_path / "finch.db")
    store.init()
    PublicationIntentRepository(store).save(_intent())
    result = reconcile(store)
    assert result.linked == [] and result.awaiting == ["d1"]


def test_reconcile_does_not_double_link_same_post(tmp_path):
    store = Store(tmp_path / "finch.db")
    store.init()
    PublicationIntentRepository(store).save(_intent(source_id="d1"))
    PublicationIntentRepository(store).save(_intent(source_id="d2"))
    AuthorPostRepository(store).save(_post("p1", "hello world"))
    result = reconcile(store)
    # 一个帖子只能被关联到一个 intent，另一个保持 awaiting。
    assert len(result.linked) == 1
    assert result.awaiting == ["d2"]
    assert len(PublicationLinkRepository(store).list()) == 1
