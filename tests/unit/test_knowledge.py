"""KnowledgeService：幂等 upsert / record_reuse / search。"""

from finch.knowledge.service import KnowledgeService
from finch.storage.repositories import KnowledgeCardRepository
from finch.storage.workspace import Workspace


def _service(tmp_path):
    return KnowledgeService(KnowledgeCardRepository(Workspace(tmp_path)))


def test_upsert_and_idempotent(tmp_path):
    svc = _service(tmp_path)
    a = svc.upsert(claim="先写结论再补证据", basis="user_reported")
    b = svc.upsert(claim="先写结论再补证据", basis="user_reported")
    assert a.id == b.id
    assert a.id.startswith("knowledge_")
    assert len(svc.list_all()) == 1


def test_upsert_merges_refs(tmp_path):
    svc = _service(tmp_path)
    svc.upsert(claim="C", basis="user_reported", attempt_ids=["attempt_1"])
    merged = svc.upsert(
        claim="C", basis="user_reported", attempt_ids=["attempt_2"], problem_ids=["problem_x"]
    )
    assert merged.attempt_ids == ["attempt_1", "attempt_2"]
    assert merged.problem_ids == ["problem_x"]


def test_record_reuse_appends(tmp_path):
    svc = _service(tmp_path)
    c = svc.upsert(claim="C", basis="user_reported")
    c = svc.record_reuse(c.id, problem_id="problem_a", note="用于另一个问题")
    assert len(c.reuse_events) == 1
    assert c.reuse_events[0].problem_id == "problem_a"
    assert svc.show(c.id).reuse_events[0].problem_id == "problem_a"


def test_search_is_substring(tmp_path):
    svc = _service(tmp_path)
    svc.upsert(claim="先写结论", basis="user_reported", applicability="写作汇报")
    svc.upsert(claim="无关结论", basis="inferred")
    assert len(svc.search("结论")) == 2
    assert len(svc.search("写作")) == 1


def test_show_missing_raises(tmp_path):
    svc = _service(tmp_path)
    try:
        svc.show("knowledge_nope")
    except KeyError:
        return
    raise AssertionError("expected KeyError")
