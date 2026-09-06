"""作者账号验证 + 发帖增量同步（确定性、幂等）。"""

from datetime import UTC, datetime

from finch.author.models import AuthorAccount, AuthorSyncCursor
from finch.storage.database import Store
from finch.storage.repositories import (
    AuthorAccountRepository,
    AuthorPostRepository,
    AuthorSyncCursorRepository,
)


def verify_account(config, client, store: Store) -> AuthorAccount:
    who = client.whoami()
    if who["handle"].lower() != config.handle.lower():
        raise ValueError(f"whoami handle {who['handle']!r} != config {config.handle!r}")
    account = AuthorAccount(
        platform=config.platform, user_id=who["user_id"],
        handle=who["handle"], verified_at=datetime.now(UTC),
    )
    AuthorAccountRepository(store).save(account)
    return account


def sync_posts(account: AuthorAccount, client, store: Store, *, lookback_days: int) -> int:
    repo = AuthorPostRepository(store)
    cursor_repo = AuthorSyncCursorRepository(store)
    posts = client.user_posts(account.handle)
    new_count = 0
    for post in posts:
        if repo.get(account.platform, post.remote_post_id) is not None:
            continue
        repo.save(post)
        new_count += 1
    # 更新水位（用已见最新 published_at；无帖子则用 now）
    if posts:
        last = max(p.published_at for p in posts)
    else:
        last = datetime.now(UTC)
    cursor_repo.save(AuthorSyncCursor(
        platform=account.platform, author_account_id=account.user_id, last_seen=last,
    ))
    return new_count
