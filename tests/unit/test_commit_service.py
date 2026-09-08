"""Tests for CommitService（commit → IdeaCandidate，Skill 架构 Step 2 Task 1）。"""

from finch.content.models import RecommendedFormat
from finch.evidence.models import Claim, ClaimConfidence, EngineeringEvent
from finch.github.models import CommitDetail, CommitFile
from finch.ideas.commit_service import CommitService
from finch.ideas.models import IdeaGenerator, SourceRef

SHA = "a" * 40
COMMIT_URL = f"https://github.com/acme/proj/commit/{SHA}"


class FakeCommitReader:
    """轻量 double for CommitReader.filter_noise。"""

    def __init__(self, *, noise: bool = False) -> None:
        self._noise = noise

    def filter_noise(self, commits: list[CommitDetail]) -> list[CommitDetail]:
        return [] if self._noise else list(commits)


class FakeExtractor:
    """轻量 double for Extractor.extract（不触发真实 LLM）。"""

    def __init__(self, events: list[EngineeringEvent]) -> None:
        self._events = list(events)
        self.calls: list[tuple[list[CommitDetail], str]] = []

    def extract(
        self, commits: list[CommitDetail], repo: str
    ) -> list[EngineeringEvent]:
        self.calls.append((list(commits), repo))
        return list(self._events)


def _commit() -> CommitDetail:
    return CommitDetail(
        sha=SHA,
        message="feat: node-ize orchestrator",
        author_date="2026-09-01T00:00:00Z",
        html_url=COMMIT_URL,
        parents=[],
        files=[
            CommitFile(
                filename="src/graph/runtime.ts",
                status="modified",
                additions=10,
                deletions=4,
                patch="+export function run",
            )
        ],
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


# ---- 有明确决策的 commit → 1 个 Idea ----

def test_clear_decision_yields_one_idea():
    svc = _service([_event()])
    ideas = svc.to_ideas([_commit()], repo="acme/proj")
    assert len(ideas) == 1
    idea = ideas[0]
    assert idea.origin == "commit"
    assert idea.core_point == "make the orchestrator a deterministic graph"
    assert idea.reader_problem == "orchestrator was hard to rerun"
    assert idea.why_worth_saying == "failures can now be replayed"
    assert idea.recommended_format == RecommendedFormat.SHORT_POST
    assert idea.generator == IdeaGenerator(skill="idea-discovery", version="1.0.0")


def test_source_refs_use_commit_url():
    svc = _service([_event()])
    idea = svc.to_ideas([_commit()], repo="acme/proj")[0]
    assert idea.source_refs == [
        SourceRef(type="commit", ref=COMMIT_URL, summary="feat: node-ize orchestrator")
    ]


# ---- 机械变化 / 私有内容 / 无可提炼观点 → 空 ----

def test_mechanical_commit_yields_nothing():
    svc = _service([_event()], noise=True)
    assert svc.to_ideas([_commit()], repo="acme/proj") == []


def test_private_repo_yields_nothing():
    svc = _service([_event()])
    assert svc.to_ideas([_commit()], repo="acme/proj", repo_is_private=True) == []


def test_unknown_decision_yields_nothing():
    svc = _service([_event(decision=Claim(
        statement="unclear why",
        confidence=ClaimConfidence.UNKNOWN,
    ))])
    assert svc.to_ideas([_commit()], repo="acme/proj") == []


def test_blank_decision_yields_nothing():
    svc = _service([_event(decision=Claim(
        statement="   ",
        confidence=ClaimConfidence.INFERRED,
    ))])
    assert svc.to_ideas([_commit()], repo="acme/proj") == []


# ---- 推断立场 → proposed ----

def test_inferred_decision_position_is_proposed():
    svc = _service([_event()])
    idea = svc.to_ideas([_commit()], repo="acme/proj")[0]
    assert idea.author_position.claim == "failures can now be replayed"
    assert idea.author_position.decision == "make the orchestrator a deterministic graph"
    assert idea.author_position.tradeoff == "orchestrator was hard to rerun"


# ---- 置信度 → boundaries 映射 ----

def test_boundaries_map_confidence():
    svc = _service([_event(
        problem=Claim(statement="hard to rerun", confidence=ClaimConfidence.VERIFIED),
        decision=Claim(
            statement="use a deterministic graph", confidence=ClaimConfidence.INFERRED
        ),
        result=Claim(statement="replay works", confidence=ClaimConfidence.SUPPORTED),
    )])
    idea = svc.to_ideas([_commit()], repo="acme/proj")[0]
    assert idea.boundaries.known == ["hard to rerun", "replay works"]
    assert idea.boundaries.inferred == ["use a deterministic graph"]
    assert idea.boundaries.unknown == []


# ---- 相同输入 → 相同结果（确定性）----

def test_same_input_yields_same_output():
    svc = _service([_event()])
    commits = [_commit()]
    first = svc.to_ideas(commits, repo="acme/proj")
    second = svc.to_ideas(commits, repo="acme/proj")
    assert first == second
    assert first[0].id == second[0].id
