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


def test_render_reflection_contains_sections():
    reflection = WeeklyReflection(
        insight="i", strongest_expression="s", meaningful_connection="m",
        next_practice="n", stop_doing="x", voice_update_candidate="v",
    )
    text = render_reflection(reflection)
    assert "本周想清楚了什么" in text
    assert "下周训练重点" in text
