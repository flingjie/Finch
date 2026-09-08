"""契约扩展：IdeaCandidate / ContentJob 新增 observation/intent/open_question。"""

from finch.content.jobs import AuthorPosition, ContentJob, ContentJobStatus
from finch.content.models import RecommendedFormat
from finch.ideas.models import (
    IdeaBoundaries,
    IdeaCandidate,
    IdeaGenerator,
    SourceRef,
)
from finch.ideas.service import IdeaService


def _candidate() -> IdeaCandidate:
    return IdeaCandidate(
        id="idea_x",
        origin="user",
        core_point="中心主张",
        observation="实际观察",
        reader_problem="读者问题",
        why_worth_saying="为什么值得说",
        intent="exploration",
        open_question="尚未解决什么",
        author_position=AuthorPosition(claim="c", decision="d", tradeoff="t"),
        source_refs=[],
        boundaries=IdeaBoundaries(known=[], inferred=[], unknown=[]),
        recommended_format=RecommendedFormat.SHORT_POST,
        generator=IdeaGenerator(skill="idea-discovery", version="1.0.0"),
    )


class _Jobs:
    def __init__(self):
        self.saved = None

    def find_by_generation_key(self, key):
        return None

    def get_job(self, job_id):
        return None

    def upsert_job(self, job):
        self.saved = job


def test_new_fields_default_to_sane_values():
    idea = IdeaCandidate(
        id="idea_y", origin="commit", core_point="cp", reader_problem="rp",
        why_worth_saying="w", author_position=AuthorPosition(claim="c", decision="d", tradeoff="t"),
        source_refs=[], boundaries=IdeaBoundaries(),
        recommended_format=RecommendedFormat.SHORT_POST,
        generator=IdeaGenerator(skill="idea-discovery", version="1.0.0"),
    )
    assert idea.observation == ""
    assert idea.intent == "stance"
    assert idea.open_question == ""


def test_source_ref_type_allows_conversation():
    ref = SourceRef(type="conversation", ref="ev_1", summary="s")
    assert ref.type == "conversation"


def test_create_candidate_maps_new_fields():
    jobs = _Jobs()
    service = IdeaService(jobs)  # type: ignore[arg-type]
    job = service.create_candidate(_candidate())
    assert job.observation == "实际观察"
    assert job.intent == "exploration"
    assert job.open_question == "尚未解决什么"
    assert job.origin == "user"
    assert job.status == ContentJobStatus.PROPOSED


def test_create_candidate_falls_back_to_id_on_generation_key_change():
    """generation_key 变（如 generator.skill 改名）时，按内容 id 兜底命中既有 job，不覆盖。"""
    existing = ContentJob(
        id="idea_x", source_card_ids=[], reader_problem="rp",
        author_position=AuthorPosition(claim="c", decision="d", tradeoff="t"),
        recommended_format=RecommendedFormat.SHORT_POST, status=ContentJobStatus.CONFIRMED,
        core_message="中心主张",
    )

    class _Repo:
        def find_by_generation_key(self, key):
            return None  # skill 改名导致 generation_key 不再命中

        def get_job(self, job_id):
            return existing

        def upsert_job(self, job):
            raise AssertionError("should not upsert when id-fallback hits existing job")

    service = IdeaService(_Repo())  # type: ignore[arg-type]
    job = service.create_candidate(_candidate())
    assert job is existing
    assert job.status == ContentJobStatus.CONFIRMED


def test_content_job_new_fields_default():
    job = ContentJob(
        id="j", source_card_ids=[], reader_problem="rp",
        recommended_format=RecommendedFormat.SHORT_POST, status=ContentJobStatus.PROPOSED,
    )
    assert job.observation == ""
    assert job.intent == "stance"
    assert job.open_question == ""
    assert job.origin is None
