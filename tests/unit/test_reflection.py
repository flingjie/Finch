"""WeeklyReflectionService：定性复盘（指标输入 + LLM 输出校验）。"""

from finch.content.voice import VoiceProfile
from finch.learn.reflection import (
    WeeklyReflection,
    WeeklyReflectionService,
    render_reflection,
)
from finch.learn.weekly import WeeklyReport


class FakeRunner:
    def __init__(self, ret):
        self.ret = ret

    def run(self, prompt, output_model, **kw):
        return self.ret


def _report() -> WeeklyReport:
    return WeeklyReport(reviewed_drafts=2, approved=1, approval_rate=0.5)


def test_reflect_returns_reflection():
    svc = WeeklyReflectionService(FakeRunner(WeeklyReflection(
        insight="想清楚了 X", strongest_expression="那篇回复", meaningful_connection="一次追问",
        next_practice="先讲场景再下判断", stop_doing="堆术语",
        voice_update_candidate="少用'本质上'", new_idea_candidates=["idea_x"],
    )))
    out = svc.reflect(_report(), voice_profile=VoiceProfile())
    assert out.insight == "想清楚了 X"
    assert out.new_idea_candidates == ["idea_x"]


def test_reflect_prompt_includes_messages_and_idea_diffs():
    captured = {}

    class CapRunner:
        def run(self, prompt, output_model, **kw):
            captured["prompt"] = prompt
            return WeeklyReflection(
                insight="i", strongest_expression="s", meaningful_connection="m",
                next_practice="p", stop_doing="x", voice_update_candidate="v",
            )

    WeeklyReflectionService(CapRunner()).reflect(
        _report(),
        message_excerpts=["[rec_1] outbound: thanks for the replay tip"],
        idea_diffs=["[idea_1] before='a' → after='b' (reason=peer counterexample)"],
    )
    assert "thanks for the replay tip" in captured["prompt"]
    assert "peer counterexample" in captured["prompt"]
    assert "Idea position changes" in captured["prompt"]


def test_idea_revision_diff_lines():
    from datetime import UTC, datetime

    from finch.content.jobs import (
        AuthorPosition,
        ContentJob,
        ContentJobStatus,
        PositionRevision,
    )
    from finch.learn.reflection import idea_revision_diff_lines

    job = ContentJob(
        id="idea_1",
        source_card_ids=[],
        reader_problem="r",
        recommended_format="short_post",
        status=ContentJobStatus.PROPOSED,
        author_position=AuthorPosition(claim="v2", decision="d", tradeoff="t"),
        position_revisions=[
            PositionRevision(
                claim="v1", created_at=datetime(2026, 9, 1, tzinfo=UTC), change_reason="init"
            ),
            PositionRevision(
                claim="v2",
                created_at=datetime(2026, 9, 8, tzinfo=UTC),
                change_reason="peer counterexample",
                source_refs=["url:1"],
            ),
        ],
    )
    lines = idea_revision_diff_lines([job])
    assert "before='v1'" in lines[0]
    assert "after='v2'" in lines[0]


def test_render_reflection_leads_with_practice():
    reflection = WeeklyReflection(
        insight="i",
        strongest_expression="那篇回复",
        meaningful_connection="与 Alice 的来回",
        surface_only_interactions="点赞帖",
        conversations_formed_ideas="关系记忆讨论",
        continue_relationships="Alice；Bob",
        next_practice="先讲场景再下判断",
        stop_doing="堆术语",
        voice_update_candidate="少用本质上",
    )
    text = render_reflection(reflection)
    assert text.startswith("下周只练：先讲场景再下判断")
    assert "本周真正有来回：与 Alice 的来回" in text
    assert "下周继续：Alice；Bob" in text
    assert "其余：" in text
    assert "Finch Weekly Reflection" not in text
    assert "uv run finch practice start" in text or "uv run finch connect daily" in text
    assert text.index("下周只练：") < text.index("其余：")
