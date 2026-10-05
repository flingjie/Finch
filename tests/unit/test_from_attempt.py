"""FragmentService.from_attempt：把实践尝试提炼为 idea，证据分列。"""

from datetime import UTC, datetime

from finch.content.jobs import AuthorPosition
from finch.content.models import ContentType
from finch.ideas.fragment_service import FragmentService, IdeaDraftOutput
from finch.practice.attempts import PracticeAttempt


class FakeRunner:
    def __init__(self, out: IdeaDraftOutput):
        self.out = out

    def run(self, prompt, output_model, **kw):
        assert "## Practice attempt" in prompt
        return self.out


def _attempt() -> PracticeAttempt:
    return PracticeAttempt(
        id="attempt_abc",
        problem_id="problem_def",
        problem="重试何时值得",
        attempt="给调用加重试",
        observation="多数失败卡在输入没变",
        unknown="重试阈值怎么定",
        next_step="测 3 个案例",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def test_from_attempt_observed_and_unknown_split():
    out = IdeaDraftOutput(
        core_point="重试只在输入不变时值得",
        observation="多数失败卡在输入没变",
        reader_problem="读者纠结何时加重试",
        why_worth_saying="给出可判断的条件",
        intent="stance",
        author_position=AuthorPosition(
            claim="重试只在输入不变时值得", decision="先判输入稳定", tradeoff="多一次调用"
        ),
        source_kind="attempt",
        facts=["多数失败卡在输入没变"],
        evidence_status="observed",
        content_type=ContentType.JUDGMENT_SHIFT,
    )
    idea = FragmentService(FakeRunner(out)).from_attempt(_attempt())
    assert idea.origin == "practice"
    assert idea.source_kind == "attempt"
    assert idea.evidence_status == "observed"
    assert idea.attempt_id == "attempt_abc"
    assert idea.problem_id == "problem_def"
    assert idea.content_type == ContentType.JUDGMENT_SHIFT
    assert "重试阈值怎么定" in idea.boundaries.unknown
    assert "测 3 个案例" in idea.boundaries.unknown
    assert any(r.type == "attempt" for r in idea.source_refs)
