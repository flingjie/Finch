"""PublicationIntent 仓储测试：save + 按主键 get + list，均幂等。"""

from datetime import UTC, datetime

from finch.author.models import PublicationIntent
from finch.storage.database import Store
from finch.storage.repositories import PublicationIntentRepository


def test_publication_intent_upsert_idempotent(tmp_path):
    store = Store(tmp_path / "finch.db")
    store.init()
    repo = PublicationIntentRepository(store)
    intent = PublicationIntent(
        source_type="draft", source_id="d1", approved_body="b", content_hash="h",
        approved_at=datetime.now(UTC), expected_kind="original",
    )
    repo.save(intent)
    repo.save(intent)
    assert len(repo.list()) == 1
    assert repo.get("d1") is not None
