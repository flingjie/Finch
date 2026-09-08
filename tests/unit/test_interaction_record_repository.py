"""InteractionRecordRepository 单元测试。"""

from datetime import datetime

from sqlmodel import Session, select

from finch.engagement.models import InteractionRecord
from finch.storage.database import Store
from finch.storage.repositories import InteractionRecordRecord, InteractionRecordRepository


def _record(
    proposal_id="x:post_1:draft_reply", peer_id="peer_abc", **overrides
) -> InteractionRecord:
    data = dict(
        id=f"rec_{proposal_id}",
        proposal_id=proposal_id,
        peer_id=peer_id,
        platform="x",
        source_url="https://x.com/alice/status/1",
        published_body="Have you tried recording a failure replay?",
        occurred_at=datetime(2026, 9, 8, 12, 0, 0),
    )
    data.update(overrides)
    return InteractionRecord(**data)


def _repo(tmp_path) -> InteractionRecordRepository:
    store = Store(tmp_path / "db.sqlite")
    store.init()
    return InteractionRecordRepository(store)


def test_upsert_get_roundtrip(tmp_path):
    repo = _repo(tmp_path)
    record = _record()
    repo.upsert(record)

    got = repo.get(record.id)
    assert got is not None
    assert got.proposal_id == "x:post_1:draft_reply"
    assert got.peer_id == "peer_abc"
    assert got.published_body == "Have you tried recording a failure replay?"


def test_same_proposal_records_one_row(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    repo = InteractionRecordRepository(store)

    # 同一 proposal 用同 id 记录两次 → 只留一行（不重复计为两次互动）。
    repo.upsert(_record())
    repo.upsert(_record(published_body="updated body"))

    with Session(store.engine) as session:
        rows = list(session.exec(select(InteractionRecordRecord)))
    assert len(rows) == 1
    assert repo.list_by_proposal("x:post_1:draft_reply")[0].published_body == "updated body"


def test_list_by_peer(tmp_path):
    repo = _repo(tmp_path)
    repo.upsert(_record(proposal_id="x:post_1:draft_reply", peer_id="peer_abc"))
    repo.upsert(_record(proposal_id="x:post_2:draft_reply", peer_id="peer_abc"))
    repo.upsert(_record(proposal_id="x:post_3:draft_reply", peer_id="peer_def"))

    abc = repo.list_by_peer("peer_abc")
    assert {r.proposal_id for r in abc} == {"x:post_1:draft_reply", "x:post_2:draft_reply"}
    assert len(repo.list_by_peer("peer_def")) == 1
    assert len(repo.list_all()) == 3


def test_missing_record_returns_none(tmp_path):
    repo = _repo(tmp_path)
    assert repo.get("rec_missing") is None
    assert repo.list_by_proposal("missing") == []
