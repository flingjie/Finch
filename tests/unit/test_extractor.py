from finch.evidence.extractor import Extractor, build_cards
from finch.evidence.models import Claim, ClaimConfidence, EngineeringEvent
from finch.github.models import CommitDetail, CommitFile


class FakeRunner:
    def run(self, prompt, output_model, **kw):
        return EngineeringEvent(
            id="evt_1", repository="flingjie/FDE-Gym", commits=["a" * 40],
            problem=Claim(statement="p", confidence=ClaimConfidence.VERIFIED),
            decision=Claim(statement="d", confidence=ClaimConfidence.INFERRED),
            result=Claim(statement="r", confidence=ClaimConfidence.VERIFIED),
        )


def _commit(sha, msg="feat: node-ize X", fname="src/graph/a.ts",
            date="2026-09-01T00:00:00Z"):
    return CommitDetail(sha=sha, message=msg, author_date=date,
                        html_url=f"https://github.com/flingjie/FDE-Gym/commit/{sha}", parents=[],
                        files=[CommitFile(filename=fname, status="modified")], stats={})


def test_extract_groups_and_calls_runner():
    commits = [_commit("a" * 40), _commit("b" * 40)]
    events = Extractor(FakeRunner()).extract(commits, repo="flingjie/FDE-Gym")
    assert len(events) == 1
    assert events[0].repository == "flingjie/FDE-Gym"


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
    # problem + result → verified 卡片；decision → inferred 卡片
    assert any(c.confidence is ClaimConfidence.VERIFIED for c in cards)
    assert any(c.confidence is ClaimConfidence.INFERRED for c in cards)
    assert all(c.sources for c in cards)


def test_decision_coerced_not_verified():
    class FakeRunner:
        def run(self, prompt, output_model, **kw):
            return EngineeringEvent(
                id="evt_1", repository="flingjie/FDE-Gym", commits=["a" * 40],
                problem=Claim(statement="p", confidence=ClaimConfidence.VERIFIED),
                decision=Claim(statement="d", confidence=ClaimConfidence.VERIFIED),
                result=Claim(statement="r", confidence=ClaimConfidence.VERIFIED),
            )

    events = Extractor(FakeRunner()).extract([_commit("a" * 40)], repo="flingjie/FDE-Gym")
    assert events[0].decision.confidence is ClaimConfidence.INFERRED
    assert events[0].problem.confidence is ClaimConfidence.VERIFIED
    assert events[0].result.confidence is ClaimConfidence.VERIFIED


def test_extract_resolves_short_commit_shas_to_full():
    commits = [_commit("a" * 40), _commit("b" * 40)]

    class ShortShaRunner:
        def run(self, prompt, output_model, **kw):
            return EngineeringEvent(
                id="evt_1", repository="flingjie/FDE-Gym", commits=["b" * 8],
                problem=Claim(statement="p", confidence=ClaimConfidence.VERIFIED),
                decision=Claim(statement="d", confidence=ClaimConfidence.INFERRED),
                result=Claim(statement="r", confidence=ClaimConfidence.VERIFIED),
            )

    events = Extractor(ShortShaRunner()).extract(commits, repo="flingjie/FDE-Gym")
    assert events[0].commits == ["b" * 40]


def test_extract_resolves_sha_with_message_suffix():
    commits = [_commit("a" * 40), _commit("b" * 40)]

    class MessageSuffixedShaRunner:
        def run(self, prompt, output_model, **kw):
            return EngineeringEvent(
                id="evt_1", repository="flingjie/FDE-Gym",
                commits=["b" * 8 + " feat: node-ize X"],
                problem=Claim(statement="p", confidence=ClaimConfidence.VERIFIED),
                decision=Claim(statement="d", confidence=ClaimConfidence.INFERRED),
                result=Claim(statement="r", confidence=ClaimConfidence.VERIFIED),
            )

    events = Extractor(MessageSuffixedShaRunner()).extract(
        commits, repo="flingjie/FDE-Gym"
    )
    assert events[0].commits == ["b" * 40]


def test_extract_runs_groups_in_parallel():
    import threading

    # 串行实现会在第一个 group 上阻塞至 barrier 超时（BrokenBarrierError）；并行后
    # 两个 group 同时到达 barrier，立即放行。
    barrier = threading.Barrier(2, timeout=5)

    class BarrierRunner:
        def run(self, prompt, output_model, **kw):
            barrier.wait()
            statement = "cache" if "caching" in prompt else "login"
            return EngineeringEvent(
                id=f"evt_{statement}",
                repository="flingjie/FDE-Gym",
                commits=[],
                problem=Claim(statement=statement, confidence=ClaimConfidence.VERIFIED),
                decision=Claim(statement="d", confidence=ClaimConfidence.INFERRED),
                result=Claim(statement="r", confidence=ClaimConfidence.VERIFIED),
            )

    # 消息/文件/时间间隔（2 天 > 90min）均不相关 → 两组
    commits = [
        _commit("a" * 40, msg="feat: add caching layer", fname="src/cache.py",
                date="2026-09-01T00:00:00Z"),
        _commit("b" * 40, msg="fix: login bug", fname="src/auth.py",
                date="2026-09-03T00:00:00Z"),
    ]
    events = Extractor(BarrierRunner()).extract(commits, repo="flingjie/FDE-Gym")
    # 事件顺序 = group 顺序（author_date 升序）
    assert [e.problem.statement for e in events] == ["cache", "login"]
