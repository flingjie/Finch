from finch.evidence.models import ClaimConfidence, EvidenceCard, Source
from finch.evidence.safety import scan_cards


def _card(**kw):
    base = dict(
        id="ev_1", event_id="evt", claim="added trajectory checks",
        sources=[Source(type="commit", url="https://github.com/flingjie/FDE-Gym/commit/abc123")],
        confidence=ClaimConfidence.VERIFIED, publishable=True, topics=[],
    )
    base.update(kw)
    return EvidenceCard(**base)


def test_secret_detected_in_claim():
    c = _card(claim="token ghp_abcdefghijklmnopqrstuvwxyz1234 leaked")
    r = scan_cards([c], repo_is_private={}, known_commit_urls={c.sources[0].url})
    assert r.hard_fail
    assert r.hits[0].code == "secret_detected"


def test_private_repo_or_unpublishable():
    c = _card(publishable=False)
    r = scan_cards([c], repo_is_private={}, known_commit_urls={c.sources[0].url})
    assert any(h.code == "private_repo_content" for h in r.hits)
    c2 = _card(publishable=True)
    r2 = scan_cards(
        [c2],
        repo_is_private={"flingjie/FDE-Gym": True},
        known_commit_urls={c2.sources[0].url},
    )
    assert any(h.code == "private_repo_content" for h in r2.hits)


def test_nonexistent_commit():
    c = _card()
    r = scan_cards([c], repo_is_private={}, known_commit_urls=set())
    assert any(h.code == "nonexistent_commit" for h in r.hits)


def test_clean_card_passes():
    c = _card()
    r = scan_cards(
        [c],
        repo_is_private={"flingjie/FDE-Gym": False},
        known_commit_urls={c.sources[0].url},
    )
    assert r.hard_fail is False


def test_private_repo_content_fails_closed_at_extract(tmp_path):
    """私有仓库内容 fail closed（计划 §6 Task 0.3 不变量「私有内容 fail closed」）。

    extract 节点把私有仓库产出的卡片标记为 publishable=False，scan_cards 命中
    private_repo_content（hard_fail），节点返回 failed —— Graph 在 extract 处阻断，
    私有卡片既不落库也绝不进入 define_jobs / draft / brief，因此不能进入公开草稿。
    """
    from finch.evidence.models import Claim, ClaimConfidence, EngineeringEvent
    from finch.graph.pipeline import make_extract_node
    from finch.graph.runtime import GraphRuntime
    from finch.storage.database import Store
    from finch.storage.repositories import EvidenceRepository

    class DummyExtractor:
        def extract(self, commits, repo):
            return [
                EngineeringEvent(
                    id="evt",
                    repository=repo,
                    commits=["abc123"],
                    problem=Claim(
                        statement="internal launch plan",
                        confidence=ClaimConfidence.VERIFIED,
                    ),
                    decision=Claim(
                        statement="ship behind a flag", confidence=ClaimConfidence.INFERRED
                    ),
                    result=Claim(
                        statement="staged rollout", confidence=ClaimConfidence.VERIFIED
                    ),
                )
            ]

    store = Store(tmp_path / "db.sqlite")
    store.init()
    node = make_extract_node(
        extractor=DummyExtractor(),
        commits_by_repo={"flingjie/private-repo": []},
        repo_is_private={"flingjie/private-repo": True},
        known_commit_urls={"https://github.com/flingjie/private-repo/commit/abc123"},
        cards_repo=EvidenceRepository(store),
    )
    run = GraphRuntime(store, [node]).run()

    assert run.state == "FAILED"
    rec = store.find_node(run.id, "extract_events", "default")
    assert rec is not None
    assert rec.status == "failed"
    assert rec.error_code == "private_repo_content"
    # fail closed：私有卡片不落库，杜绝任何下游路径读到私有证据。
    assert EvidenceRepository(store).get_card("ev_evt_problem") is None
