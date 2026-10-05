"""idea 落库把 content_type / attempt_id / problem_id 从候选透传到 ContentJob。"""

from finch.content.jobs import AuthorPosition, SourceKind
from finch.content.models import ContentType, RecommendedFormat
from finch.ideas.models import IdeaBoundaries, IdeaCandidate, IdeaGenerator, SourceRef
from finch.ideas.service import IdeaService
from finch.storage.repositories import ContentJobRepository
from finch.storage.workspace import Workspace


def _candidate(**kw) -> IdeaCandidate:
    base = dict(
        id="idea_x",
        origin="practice",
        core_point="重试只在输入不变时值得",
        reader_problem="读者在纠结何时加重试",
        why_worth_saying="给一个可判断的条件",
        author_position=AuthorPosition(
            claim="重试只在输入不变时值得", decision="先判输入是否稳定", tradeoff="多一次调用"
        ),
        source_refs=[SourceRef(type="attempt", ref="attempt_abc", summary="一次尝试")],
        boundaries=IdeaBoundaries(),
        recommended_format=RecommendedFormat.SHORT_POST,
        generator=IdeaGenerator(skill="idea-discovery", version="1.0.0"),
        source_kind="attempt",
    )
    base.update(kw)
    return IdeaCandidate(**base)


def test_source_kind_and_ref_accept_attempt():
    assert "attempt" in SourceKind.__args__


def test_create_candidate_threads_attempt_fields(tmp_path):
    ws = Workspace(tmp_path)
    cand = _candidate(
        content_type=ContentType.JUDGMENT_SHIFT,
        attempt_id="attempt_abc",
        problem_id="problem_def",
    )
    job = IdeaService(ContentJobRepository(ws)).create_candidate(cand)
    assert job.content_type == ContentType.JUDGMENT_SHIFT
    assert job.attempt_id == "attempt_abc"
    assert job.problem_id == "problem_def"


def test_create_candidate_defaults_none(tmp_path):
    ws = Workspace(tmp_path)
    job = IdeaService(ContentJobRepository(ws)).create_candidate(_candidate())
    assert job.content_type is None
    assert job.attempt_id is None
    assert job.problem_id is None
