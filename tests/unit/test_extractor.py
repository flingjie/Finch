import pytest

from finch.evidence.extractor import (
    BatchExtractionOutput,
    DuplicateExtractionGroupError,
    ExtractedGroup,
    Extractor,
    IncompleteBatchExtractionError,
    build_cards,
    group_fingerprint,
    pack_batches,
)
from finch.evidence.models import Claim, ClaimConfidence, EngineeringEvent
from finch.github.models import CommitDetail, CommitFile
from finch.settings import ExtractionSettings


def _event(commits, decision=ClaimConfidence.INFERRED):
    return EngineeringEvent(
        id="evt_1",
        repository="flingjie/FDE-Gym",
        commits=commits,
        problem=Claim(statement="p", confidence=ClaimConfidence.VERIFIED),
        decision=Claim(statement="d", confidence=decision),
        result=Claim(statement="r", confidence=ClaimConfidence.VERIFIED),
    )


def _batch(pairs):
    return BatchExtractionOutput(
        items=[ExtractedGroup(group_id=gid, event=ev) for gid, ev in pairs]
    )


class FakeRunner:
    def __init__(self, output):
        self.output = output
        self.calls = 0

    def run(self, prompt, output_model, **kw):
        self.calls += 1
        return self.output


def _commit(sha, msg="feat: node-ize X", fname="src/graph/a.ts",
            date="2026-09-01T00:00:00Z"):
    return CommitDetail(sha=sha, message=msg, author_date=date,
                        html_url=f"https://github.com/flingjie/FDE-Gym/commit/{sha}", parents=[],
                        files=[CommitFile(filename=fname, status="modified")], stats={})


def test_extract_batches_multiple_groups_in_one_call():
    commits = [
        _commit("a" * 40, msg="feat: add cache", fname="src/cache.py",
                date="2026-09-01T00:00:00Z"),
        _commit("b" * 40, msg="fix: login", fname="src/auth.py",
                date="2026-09-03T00:00:00Z"),
    ]
    runner = FakeRunner(_batch([
        ("g_0", _event(["a" * 40])),
        ("g_1", _event(["b" * 40])),
    ]))
    events = Extractor(runner).extract(commits, repo="flingjie/FDE-Gym")
    assert [e.commits for e in events] == [["a" * 40], ["b" * 40]]
    assert runner.calls == 1


def test_build_cards_binds_sources_and_confidence():
    events = [
        EngineeringEvent(
            id="evt_1", repository="flingjie/FDE-Gym", commits=["a" * 40],
            problem=Claim(statement="p", confidence=ClaimConfidence.VERIFIED),
            decision=Claim(statement="d", confidence=ClaimConfidence.INFERRED),
            result=Claim(statement="r", confidence=ClaimConfidence.VERIFIED),
        )
    ]
    cards = build_cards(events)
    assert any(c.confidence is ClaimConfidence.VERIFIED for c in cards)
    assert any(c.confidence is ClaimConfidence.INFERRED for c in cards)
    assert all(c.sources for c in cards)


def test_decision_coerced_not_verified():
    runner = FakeRunner(_batch([("g_0", _event(["a" * 40], decision=ClaimConfidence.VERIFIED))]))
    events = Extractor(runner).extract([_commit("a" * 40)], repo="flingjie/FDE-Gym")
    assert events[0].decision.confidence is ClaimConfidence.INFERRED
    assert events[0].problem.confidence is ClaimConfidence.VERIFIED


def test_model_user_confirmed_problem_and_result_downgraded():
    event = _event(["a" * 40]).model_copy(
        update={
            "problem": Claim(statement="p", confidence=ClaimConfidence.USER_CONFIRMED),
            "result": Claim(statement="r", confidence=ClaimConfidence.USER_CONFIRMED),
        }
    )
    runner = FakeRunner(_batch([("g_0", event)]))
    events = Extractor(runner).extract([_commit("a" * 40)], repo="flingjie/FDE-Gym")
    assert events[0].problem.confidence is ClaimConfidence.SUPPORTED
    assert events[0].result.confidence is ClaimConfidence.SUPPORTED


def test_model_user_confirmed_decision_coerced_to_inferred():
    runner = FakeRunner(
        _batch([("g_0", _event(["a" * 40], decision=ClaimConfidence.USER_CONFIRMED))])
    )
    events = Extractor(runner).extract([_commit("a" * 40)], repo="flingjie/FDE-Gym")
    assert events[0].decision.confidence is ClaimConfidence.INFERRED


def test_extract_resolves_short_commit_shas_to_full():
    commits = [_commit("a" * 40), _commit("b" * 40)]
    runner = FakeRunner(_batch([("g_0", _event(["b" * 8]))]))
    events = Extractor(runner).extract(commits, repo="flingjie/FDE-Gym")
    assert events[0].commits == ["b" * 40]


def test_extract_resolves_sha_with_message_suffix():
    commits = [_commit("a" * 40), _commit("b" * 40)]
    runner = FakeRunner(_batch([("g_0", _event(["b" * 8 + " feat: node-ize X"]))]))
    events = Extractor(runner).extract(commits, repo="flingjie/FDE-Gym")
    assert events[0].commits == ["b" * 40]


def test_extract_compensates_missing_group():
    commits = [
        _commit("a" * 40, msg="feat: add cache", fname="src/cache.py",
                date="2026-09-01T00:00:00Z"),
        _commit("b" * 40, msg="fix: login", fname="src/auth.py",
                date="2026-09-03T00:00:00Z"),
    ]
    first = _batch([("g_0", _event(["a" * 40]))])  # 缺失 g_1
    recovery = _batch([("g_0", _event(["b" * 40]))])  # 补偿批次局部序号仍从 g_0 起

    class RecoveringRunner:
        def __init__(self):
            self.calls = 0

        def run(self, prompt, output_model, **kw):
            self.calls += 1
            return first if self.calls == 1 else recovery

    runner = RecoveringRunner()
    events = Extractor(runner).extract(commits, repo="flingjie/FDE-Gym")
    assert [e.commits for e in events] == [["a" * 40], ["b" * 40]]
    assert runner.calls == 2


def test_extract_rejects_sha_outside_group():
    runner = FakeRunner(_batch([("g_0", _event(["c" * 40]))]))
    with pytest.raises(IncompleteBatchExtractionError):
        Extractor(runner).extract([_commit("a" * 40)], repo="flingjie/FDE-Gym")


def test_extract_rejects_duplicate_group_id():
    runner = FakeRunner(_batch([
        ("g_0", _event(["a" * 40])),
        ("g_0", _event(["a" * 40])),
    ]))
    with pytest.raises(DuplicateExtractionGroupError):
        Extractor(runner).extract([_commit("a" * 40)], repo="flingjie/FDE-Gym")


def test_pack_batches_keeps_small_input_as_one_batch():
    g1 = [_commit("a" * 40)]
    g2 = [_commit("b" * 40)]
    batches = pack_batches([g1, g2], "p {groups}", max_prompt_bytes=10**6, max_groups_per_batch=100)
    assert len(batches) == 1
    assert len(batches[0]) == 2


def test_pack_batches_splits_when_over_budget():
    g1 = [_commit("a" * 40)]
    g2 = [_commit("b" * 40)]
    batches = pack_batches([g1, g2], "p {groups}", max_prompt_bytes=10, max_groups_per_batch=100)
    assert len(batches) == 2


def test_pack_batches_splits_when_over_max_groups():
    g1 = [_commit("a" * 40)]
    g2 = [_commit("b" * 40)]
    batches = pack_batches([g1, g2], "p {groups}", max_prompt_bytes=10**6, max_groups_per_batch=1)
    assert len(batches) == 2


def test_extract_multi_batch_preserves_order():
    commits = [
        _commit("a" * 40, msg="feat: add cache", fname="src/cache.py",
                date="2026-09-01T00:00:00Z"),
        _commit("b" * 40, msg="fix: login", fname="src/auth.py",
                date="2026-09-03T00:00:00Z"),
    ]
    settings = ExtractionSettings(max_groups_per_batch=1, max_concurrent_batches=2)

    class OrderingRunner:
        def __init__(self):
            self.calls: list[int] = []

        def run(self, prompt, output_model, **kw):
            self.calls.append(1)  # list.append 线程安全
            if "cache" in prompt:
                return _batch([("g_0", _event(["a" * 40]))])
            return _batch([("g_0", _event(["b" * 40]))])

    runner = OrderingRunner()
    events = Extractor(runner, settings=settings).extract(commits, repo="flingjie/FDE-Gym")
    assert [e.commits for e in events] == [["a" * 40], ["b" * 40]]
    assert len(runner.calls) == 2


def test_group_fingerprint_differs_on_change():
    g1 = [_commit("a" * 40, msg="feat: add x")]
    g2 = [_commit("a" * 40, msg="fix: y")]
    assert group_fingerprint("r", g1, "v1") == group_fingerprint("r", g1, "v1")
    assert group_fingerprint("r", g1, "v1") != group_fingerprint("r", g2, "v1")
    assert group_fingerprint("r", g1, "v1") != group_fingerprint("r", g1, "v2")


def test_extract_caches_and_reuses(tmp_path):
    cache_path = tmp_path / "cache.json"
    # 第一次：miss → 提取 + 写缓存
    runner1 = FakeRunner(_batch([("g_0", _event(["a" * 40]))]))
    events1 = Extractor(runner1, cache_path=cache_path).extract(
        [_commit("a" * 40)], repo="flingjie/FDE-Gym"
    )
    assert [e.commits for e in events1] == [["a" * 40]]
    assert runner1.calls == 1

    # 第二次：hit → 0 调用
    runner2 = FakeRunner(_batch([]))
    events2 = Extractor(runner2, cache_path=cache_path).extract(
        [_commit("a" * 40)], repo="flingjie/FDE-Gym"
    )
    assert [e.commits for e in events2] == [["a" * 40]]
    assert runner2.calls == 0


def test_extract_does_not_reuse_cache_for_different_group(tmp_path):
    cache_path = tmp_path / "cache.json"
    runner1 = FakeRunner(_batch([("g_0", _event(["a" * 40]))]))
    Extractor(runner1, cache_path=cache_path).extract(
        [_commit("a" * 40, msg="feat: add cache", fname="src/cache.py")],
        repo="flingjie/FDE-Gym",
    )
    assert runner1.calls == 1

    # 不同 message → 不同指纹 → miss → 再提取
    runner2 = FakeRunner(_batch([("g_0", _event(["a" * 40]))]))
    Extractor(runner2, cache_path=cache_path).extract(
        [_commit("a" * 40, msg="fix: login", fname="src/auth.py")],
        repo="flingjie/FDE-Gym",
    )
    assert runner2.calls == 1
