"""Weekly reflection accepts relationship-only weeks (Value Discovery v2)."""

from finch.engagement.metrics import RelationshipMetrics
from finch.learn.reflection import WeeklyReflectionService
from finch.learn.weekly import WeeklyReport


class _FakeRunner:
    def __init__(self) -> None:
        self.last_prompt = ""

    def run(self, prompt, output_model, **kw):
        self.last_prompt = prompt
        return output_model(
            insight="ok",
            strongest_expression="none",
            meaningful_connection="none this week",
            surface_only_interactions="",
            conversations_formed_ideas="",
            continue_relationships="none",
            next_practice="write one concrete follow-up",
            stop_doing="generic advice lists",
            voice_update_candidate="",
            new_idea_candidates=[],
        )


def test_weekly_reflection_with_only_relationship_data():
    runner = _FakeRunner()
    svc = WeeklyReflectionService(runner)
    reflection = svc.reflect(
        WeeklyReport(),
        relationship_metrics=RelationshipMetrics(),
        observation_notes=[],
        open_commitments=[],
    )
    assert "no trial/usage activity" in runner.last_prompt
    assert "Do not invent a commercial funnel" in runner.last_prompt
    assert reflection.next_practice


def test_weekly_reflection_passes_separate_fact_blocks():
    runner = _FakeRunner()
    svc = WeeklyReflectionService(runner)
    svc.reflect(
        WeeklyReport(),
        observation_notes=["[t1] usage_feedback: faster import"],
        open_commitments=["[t1] open (self): send sample"],
    )
    assert "faster import" in runner.last_prompt
    assert "send sample" in runner.last_prompt
    assert "## Observation / usage notes" in runner.last_prompt
    assert "## Open commitments" in runner.last_prompt
