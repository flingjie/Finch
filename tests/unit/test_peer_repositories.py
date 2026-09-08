"""PeerRepository 单元测试。"""

from sqlmodel import Session, select

from finch.peers.models import PeerProfile, PlatformIdentity
from finch.peers.service import peer_id_for
from finch.storage.database import Store
from finch.storage.repositories import PeerRecord, PeerRepository


def _profile(platform="x", author_id="alice", **overrides) -> PeerProfile:
    data = dict(
        id=peer_id_for(platform, author_id),
        platform_identities=[PlatformIdentity(platform=platform, author_id=author_id)],
    )
    data.update(overrides)
    return PeerProfile(**data)


def _repo(tmp_path) -> PeerRepository:
    store = Store(tmp_path / "db.sqlite")
    store.init()
    return PeerRepository(store)


def test_upsert_get_roundtrip(tmp_path):
    repo = _repo(tmp_path)
    profile = _profile(display_name="Alice")
    repo.upsert(profile)

    got = repo.get(profile.id)
    assert got is not None
    assert got.id == profile.id
    assert got.display_name == "Alice"
    assert got.platform_identities[0].author_id == "alice"


def test_same_author_across_posts_keeps_one_row(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    repo = PeerRepository(store)

    # 同一作者两条帖子 → 同一 peer_id → 只留一行，且内容被更新。
    repo.upsert(_profile())
    repo.upsert(_profile(display_name="Alice Updated"))

    with Session(store.engine) as session:
        rows = list(session.exec(select(PeerRecord)))
    assert len(rows) == 1
    assert repo.get(peer_id_for("x", "alice")).display_name == "Alice Updated"


def test_distinct_authors_are_distinct_peers(tmp_path):
    repo = _repo(tmp_path)
    repo.upsert(_profile(author_id="alice"))
    repo.upsert(_profile(author_id="bob"))
    assert len(repo.list_all()) == 2


def test_list_all_sorted_by_id(tmp_path):
    repo = _repo(tmp_path)
    repo.upsert(_profile(author_id="bob"))
    repo.upsert(_profile(author_id="alice"))
    ids = [p.id for p in repo.list_all()]
    assert ids == sorted(ids)


def test_missing_peer_returns_none(tmp_path):
    repo = _repo(tmp_path)
    assert repo.get("peer_missing") is None
