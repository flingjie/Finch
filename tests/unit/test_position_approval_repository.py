from finch.storage.database import Store
from finch.storage.repositories import PositionApprovalRepository


def _repo(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    return PositionApprovalRepository(store)


def test_approve_and_find_active(tmp_path):
    repo = _repo(tmp_path)
    repo.approve("fp1", "job1")
    approval = repo.find_active("fp1")
    assert approval is not None
    assert approval.source_job_id == "job1"
    assert approval.revoked_at is None


def test_find_active_returns_none_for_unknown(tmp_path):
    assert _repo(tmp_path).find_active("nope") is None


def test_revoke_blocks_find_active(tmp_path):
    repo = _repo(tmp_path)
    repo.approve("fp1", "job1")
    repo.revoke("fp1")
    assert repo.find_active("fp1") is None


def test_approve_refreshes_revoked(tmp_path):
    repo = _repo(tmp_path)
    repo.approve("fp1", "job1")
    repo.revoke("fp1")
    repo.approve("fp1", "job2")
    approval = repo.find_active("fp1")
    assert approval is not None and approval.source_job_id == "job2"
    assert approval.revoked_at is None
