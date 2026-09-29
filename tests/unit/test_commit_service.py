"""Tests for CommitService（commit → FactBundle，发散前置）。"""

from finch.evidence.models import Claim, ClaimConfidence, EngineeringEvent
from finch.github.models import CommitDetail, CommitFile
from finch.ideas.commit_service import CommitService
from finch.ideas.models import SourceRef

SHA = "a" * 40
COMMIT_URL = f"https://github.com/acme/proj/commit/{SHA}"


class FakeCommitReader:
    def __init__(self, *, noise: bool = False) -> None:
        self._noise = noise

    def filter_noise(self, commits: list[CommitDetail]) -> list[CommitDetail]:
        return [] if self._noise else list(commits)


class FakeExtractor:
    def __init__(self, events: list[EngineeringEvent]) -> None:
        self._events = list(events)
        self.calls: list[tuple[list[CommitDetail], str]] = []

    def extract(self, commits: list[CommitDetail], repo: str) -> list[EngineeringEvent]:
        self.calls.append((list(commits), repo))
        return list(self._events)


def _commit() -> CommitDetail:
    return CommitDetail(
        sha=SHA,
        message="feat: node-ize orchestrator",
        author_date="2026-09-01T00:00:00Z",
        html_url=COMMIT_URL,
        parents=[],
        files=[CommitFile(filename="src/graph/runtime.ts", status="modified",
                          additions=10, deletions=4, patch="+export function run")],
        stats={},
    )


def _event(**overrides) -> EngineeringEvent:
    data = dict(
        id="evt_acme_proj_node_runtime",
        repository="acme/proj",
        commits=[SHA],
        problem=Claim(
            statement="orchestrator was hard to rerun",
            confidence=ClaimConfidence.SUPPORTED,
        ),
        decision=Claim(
            statement="make the orchestrator a deterministic graph",
            confidence=ClaimConfidence.INFERRED,
        ),
        result=Claim(
            statement="failures can now be replayed",
            confidence=ClaimConfidence.SUPPORTED,
        ),
        missing_context=[],
        topics=["deterministic execution"],
    )
    data.update(overrides)
    return EngineeringEvent(**data)


def _service(events: list[EngineeringEvent], *, noise: bool = False) -> CommitService:
    return CommitService(FakeCommitReader(noise=noise), FakeExtractor(events))


def test_clear_material_yields_one_bundle():
    svc = _service([_event()])
    bundles = svc.to_facts([_commit()], repo="acme/proj")
    assert len(bundles) == 1
    b = bundles[0]
    assert b.origin == "practice"
    assert b.source_kind == "commit"
    assert b.evidence_status == "observed"
    assert b.facts == [
        "orchestrator was hard to rerun",
        "make the orchestrator a deterministic graph",
        "failures can now be replayed",
    ]


def test_source_refs_use_commit_url():
    svc = _service([_event()])
    b = svc.to_facts([_commit()], repo="acme/proj")[0]
    assert b.source_refs == [
        SourceRef(type="commit", ref=COMMIT_URL, summary="feat: node-ize orchestrator")
    ]


def test_mechanical_commit_yields_nothing():
    svc = _service([_event()], noise=True)
    assert svc.to_facts([_commit()], repo="acme/proj") == []


def test_private_repo_yields_nothing():
    svc = _service([_event()])
    assert svc.to_facts([_commit()], repo="acme/proj", repo_is_private=True) == []


def test_unknown_decision_still_yields_if_result_present():
    # 门禁放宽：decision UNKNOWN 但 result 有内容 → 仍进入发散
    svc = _service([_event(
        decision=Claim(statement="unclear why", confidence=ClaimConfidence.UNKNOWN),
    )])
    bundles = svc.to_facts([_commit()], repo="acme/proj")
    assert len(bundles) == 1


def test_all_unknown_yields_nothing():
    svc = _service([_event(
        problem=Claim(statement="", confidence=ClaimConfidence.UNKNOWN),
        decision=Claim(statement="", confidence=ClaimConfidence.UNKNOWN),
        result=Claim(statement="", confidence=ClaimConfidence.UNKNOWN),
    )])
    assert svc.to_facts([_commit()], repo="acme/proj") == []


def test_boundaries_map_confidence():
    svc = _service([_event(
        problem=Claim(statement="hard to rerun", confidence=ClaimConfidence.VERIFIED),
        decision=Claim(statement="use a deterministic graph", confidence=ClaimConfidence.INFERRED),
        result=Claim(statement="replay works", confidence=ClaimConfidence.SUPPORTED),
    )])
    b = svc.to_facts([_commit()], repo="acme/proj")[0]
    assert b.boundaries.known == ["hard to rerun", "replay works"]
    assert b.boundaries.inferred == ["use a deterministic graph"]
    assert b.boundaries.unknown == []


def test_same_input_yields_same_output():
    svc = _service([_event()])
    commits = [_commit()]
    first = svc.to_facts(commits, repo="acme/proj")
    second = svc.to_facts(commits, repo="acme/proj")
    assert first == second
