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


def _commit(sha):
    return CommitDetail(sha=sha, message="feat: node-ize X", author_date="2026-09-01T00:00:00Z",
                        html_url=f"https://github.com/flingjie/FDE-Gym/commit/{sha}", parents=[],
                        files=[CommitFile(filename="src/graph/a.ts", status="modified")], stats={})


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
