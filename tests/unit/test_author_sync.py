from datetime import UTC, datetime

from finch.author.models import AuthorPost
from finch.author.sync import sync_posts, verify_account
from finch.settings import AuthorAccountConfig
from finch.storage.database import Store
from finch.storage.repositories import AuthorPostRepository


class _FakeClient:
    def __init__(self, whoami, posts):
        self._whoami = whoami
        self._posts = posts

    def whoami(self):
        return self._whoami

    def user_posts(self, handle, *, limit=200):
        return self._posts


def test_verify_account_mismatch_raises(tmp_path):
    store = Store(tmp_path / "finch.db")
    store.init()
    client = _FakeClient({"user_id": "999", "handle": "other"}, [])
    try:
        verify_account(AuthorAccountConfig(handle="flingjie"), client, store)
        raise AssertionError("expected mismatch error")
    except ValueError:
        pass


def test_sync_posts_idempotent_and_cursor(tmp_path):
    store = Store(tmp_path / "finch.db")
    store.init()
    client = _FakeClient({"user_id": "1", "handle": "flingjie"}, [
        AuthorPost(platform="x", remote_post_id="p1", author_account_id="1",
                   kind="original", body="a", url="u", published_at=datetime.now(UTC)),
    ])
    acc = verify_account(AuthorAccountConfig(handle="flingjie"), client, store)
    assert acc.user_id == "1"
    n1 = sync_posts(acc, client, store, lookback_days=90)
    n2 = sync_posts(acc, client, store, lookback_days=90)
    assert n1 == 1 and n2 == 0  # 幂等
    assert len(AuthorPostRepository(store).list()) == 1
