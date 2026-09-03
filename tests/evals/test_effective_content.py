"""Eval corpus: Spec §10 Phase E — seven representative scenarios for the
effective-content system.

The corpus is read-focused and deterministic. It uses scripted fake runners (no real
LLM) to verify the SYSTEM's routing and deterministic aggregation on representative
inputs — not the quality of real model output. One scenario = one clearly-named test.

Scenario map:
1. 强证据 + 清晰判断 → READY
2. 强证据 + 无作者判断 → NEEDS_INPUT
3. 弱证据/无增量 → DO_NOT_WRITE (silently skipped)
4. 通用 AI 套话 → Portability/Specificity fail
5. 有数字但无证据 → hard fail (reject)
6. 有明确取舍且风格自然 → pass
7. 重写两轮仍失败 → 不进审核 (dropped from kept)
"""

import json
from types import SimpleNamespace

from finch.codex.runner import CodexRunner
from finch.content.checkers import (
    CheckContext,
    CheckResult,
    EvidenceChecker,
    PortabilityChecker,
    SpecificityChecker,
    aggregate_checks,
)
from finch.content.jobs import (
    AuthorPosition,
    ContentJob,
    ContentJobStatus,
    IntendedEffect,
    SuccessCriterion,
)
from finch.content.models import ClaimRef, Draft, DraftKind
from finch.evidence.models import ClaimConfidence, EvidenceCard, JudgeScores, MatchResult
from finch.graph.content_nodes import (
    default_checker_suite,
    make_critique_node,
    make_position_gate_node,
)
from finch.graph.context import items_payload
from finch.graph.events import NodeResult
from finch.graph.nodes import Node
from finch.graph.runtime import GraphRuntime
from finch.settings import QualityGates
from finch.storage.database import Store

# --- scripted runners -------------------------------------------------------


class FakeRunner:
    """Returns a fixed object on ``run``; records the prompt and call count."""

    def __init__(self, ret):
        self.ret = ret
        self.calls = 0
        self.last_prompt: str | None = None

    def run(self, prompt, output_model, **kw):
        self.calls += 1
        self.last_prompt = prompt
        return self.ret


class ScriptedRunner:
    """Returns a pass output chosen by the requested output model's class name."""

    def __init__(self, outputs: dict[str, dict]):
        self.outputs = {
            name: SimpleNamespace(**fields) for name, fields in outputs.items()
        }
        self.calls = 0

    def run(self, prompt, output_model, **kw):
        self.calls += 1
        return self.outputs[output_model.__name__]


# Pass outputs for every LLM-backed checker in the default suite.
_PASS_OUTPUTS = {
    "_EntailmentOutput": {"entailment_failed": []},
    "_DecisionOutput": {"expresses_decision": True, "expresses_tradeoff": True, "missing": []},
    "_SpecificityOutput": {"filler_sentences": []},
    "_PortabilityOutput": {"generic_sentences": []},
    "_VoiceOutput": {"matches_voice": True, "non_author_sentences": []},
    "_StructureOutput": {"confirmed_problems": []},
    "_ActionabilityOutput": {"fulfills_effect": True, "missing": []},
    "_SafetyOutput": {"invented_personal_experience": False, "unsupported_metric": False},
}


class Seed(Node):
    """Seeds a context key with a pre-built items payload."""

    model_config = {"extra": "allow"}

    def run(self, ctx):
        return NodeResult(status="succeeded", output=self.seed)


class AlwaysFailChecker:
    """Fails every round with a rewrite-level result (never reject/needs_input)."""

    name = "always_fail"

    def __init__(self):
        self.calls = 0

    def check(self, ctx) -> CheckResult:
        self.calls += 1
        return CheckResult(
            checker=self.name,
            passed=False,
            severity="high",
            locations=["body"],
            issues=["always fails"],
            rewrite_instructions=["fix it"],
        )


# --- fixtures ---------------------------------------------------------------


def _store(tmp_path) -> Store:
    s = Store(tmp_path / "db.sqlite")
    s.init()
    return s


def _card(cid: str = "ev1") -> EvidenceCard:
    return EvidenceCard(
        id=cid,
        event_id="e",
        claim="token bucket rate limiting",
        sources=[],
        confidence=ClaimConfidence.VERIFIED,
        publishable=True,
        topics=["rate"],
    )


def _match(candidate_id: str = "t1", card_ids: tuple[str, ...] = ("ev1",)) -> MatchResult:
    return MatchResult(
        candidate_id=candidate_id,
        card_ids=list(card_ids),
        scores=JudgeScores(
            relevance=0.9, evidence_strength=0.9, incremental_value=0.9, discussability=0.9
        ),
        timing=1.0,
        relationship_value=0.5,
        score=0.9,
    )


def _draft(
    body: str = "We set the pool size to 10.",
    candidate_id: str = "t1",
    job_id: str | None = None,
    claims: list[ClaimRef] | None = None,
) -> Draft:
    return Draft(
        id="d1",
        kind=DraftKind.REPLY,
        candidate_id=candidate_id,
        body=body,
        claims=claims
        if claims is not None
        else [
            ClaimRef(
                statement="pool size 10",
                evidence_card_id="ev1",
                confidence=ClaimConfidence.VERIFIED,
            )
        ],
        content_job_id=job_id,
    )


def _position(
    decision: str = "Use pool size 10",
    tradeoff: str = "More memory",
    confirmed: bool = True,
) -> AuthorPosition:
    return AuthorPosition(
        claim="pool size 10 is the right call",
        decision=decision,
        tradeoff=tradeoff,
        confirmed=confirmed,
    )


_DEFAULT_POSITION = _position()


def _job(
    job_id: str = "job1",
    source_card_ids: tuple[str, ...] = ("ev1",),
    position: AuthorPosition | None = _DEFAULT_POSITION,
    status: ContentJobStatus = ContentJobStatus.READY,
) -> ContentJob:
    return ContentJob(
        id=job_id,
        source_card_ids=list(source_card_ids),
        candidate_id="t1",
        reader_problem="readers don't know how to rate limit",
        audience="backend engineers",
        intended_effect=IntendedEffect(understand="token bucket rate limiting"),
        author_position=position,
        success_criteria=[
            SuccessCriterion(id="c1", description="critic passes", measurement="critic")
        ],
        recommended_format=DraftKind.REPLY,
        status=status,
    )


# --- the seven scenarios ----------------------------------------------------


def test_scenario_1_strong_evidence_and_confirmed_position_routes_to_ready(tmp_path):
    """强证据 + 清晰判断 → READY：确认立场放行到 ready_jobs，不进入 needs_input。"""
    store = _store(tmp_path)
    nodes = [
        Seed(name="define_jobs", writes="content_jobs", seed=items_payload([_job()])),
        make_position_gate_node(),
    ]
    run = GraphRuntime(store, nodes).run()
    assert run.state == "POSITIONS_READY"
    rec = store.find_node(run.id, "position_gate", "default")
    assert rec is not None
    assert rec.status == "succeeded"
    assert [j["id"] for j in json.loads(rec.output_json)["items"]] == ["job1"]


def test_scenario_2_strong_evidence_without_confirmed_position_needs_input(tmp_path):
    """强证据 + 无作者判断 → NEEDS_INPUT：未确认立场挡在门禁处。"""
    store = _store(tmp_path)
    nodes = [
        Seed(
            name="define_jobs",
            writes="content_jobs",
            seed=items_payload([_job(position=_position(confirmed=False))]),
        ),
        make_position_gate_node(),
    ]
    run = GraphRuntime(store, nodes).run()
    assert run.state == "NEEDS_INPUT"
    rec = store.find_node(run.id, "position_gate", "default")
    assert rec is not None
    assert rec.status == "needs_input"
    assert [j["id"] for j in json.loads(rec.output_json)["items"]] == ["job1"]


def test_scenario_3_do_not_write_is_silently_skipped(tmp_path):
    """弱证据/无增量 → DO_NOT_WRITE：门禁静默跳过（empty ready_jobs，仍是正常成功）。"""
    job = _job(status=ContentJobStatus.DO_NOT_WRITE, position=None)
    store = _store(tmp_path)
    nodes = [
        Seed(name="define_jobs", writes="content_jobs", seed=items_payload([job])),
        make_position_gate_node(),
    ]
    run = GraphRuntime(store, nodes).run()
    assert run.state == "POSITIONS_READY"
    rec = store.find_node(run.id, "position_gate", "default")
    assert rec is not None
    assert rec.status == "succeeded"
    assert json.loads(rec.output_json)["items"] == []


def test_scenario_4_generic_ai_boilerplate_fails_portability_and_specificity():
    """通用 AI 套话 → Specificity（确定性）与 Portability（脚本化）均失败。"""
    body = "This is a great, powerful, seamless solution."
    draft = _draft(body=body)
    cards = [_card()]

    specificity = SpecificityChecker().check(CheckContext(draft=draft, cards=cards))
    assert specificity.passed is False
    assert specificity.severity in ("medium", "high")

    runner = FakeRunner(SimpleNamespace(generic_sentences=[body]))
    portability = PortabilityChecker(runner).check(CheckContext(draft=draft, cards=cards))
    assert portability.passed is False
    assert portability.severity == "high"

    # 无 hard_fail、无 needs_input → 触发 rewrite 而非放行。
    assert aggregate_checks([specificity, portability]) == "rewrite"


def test_scenario_5_numbers_without_evidence_hard_fail():
    """有数字但无证据 → EvidenceChecker hard_fail，聚合 → reject（不可被平均分掩盖）。"""
    draft = _draft(
        body="Our optimization cut p99 latency by 40%.",
        claims=[
            ClaimRef(
                statement="cut p99 latency by 40%",
                evidence_card_id="ev_999",
                confidence=ClaimConfidence.VERIFIED,
            )
        ],
    )
    result = EvidenceChecker().check(CheckContext(draft=draft, cards=[_card("ev1")]))
    assert result.passed is False
    assert result.severity == "hard_fail"
    assert result.locations
    assert aggregate_checks([result]) == "reject"


def test_scenario_6_concrete_decision_and_natural_style_pass():
    """有明确取舍且风格自然 → 全部检查器通过，聚合 → pass。"""
    draft = _draft(body="We set the pool size to 10 to cut p99 latency by 40%.", job_id="job1")
    job = _job(position=_DEFAULT_POSITION)
    cards = [_card("ev1")]

    suite = default_checker_suite(ScriptedRunner(_PASS_OUTPUTS))
    checks = [checker.check(CheckContext(draft=draft, cards=cards, job=job)) for checker in suite]

    assert [c.checker for c in checks if not c.passed] == []
    assert aggregate_checks(checks) == "pass"


def test_scenario_7_unfixable_draft_dropped_after_two_rewrites(tmp_path):
    """重写两轮仍失败 → 不进审核：max_rewrite_rounds 后草稿从 kept 中丢弃。"""
    checker = AlwaysFailChecker()

    def rewrite(runner, draft, failed_checks, cards_by_id, job=None):
        return draft.model_copy(update={"body": f"{draft.body} (revised)"})

    store = _store(tmp_path)
    nodes = [
        Seed(name="draft", writes="drafts", seed=items_payload([_draft()])),
        Seed(name="match_evidence", writes="match_results", seed=items_payload([_match()])),
        Seed(name="extract_events", writes="evidence_cards", seed=items_payload([_card()])),
        Seed(name="define_jobs", writes="content_jobs", seed=items_payload([])),
        Seed(name="position_gate", writes="ready_jobs", seed=items_payload([])),
        make_critique_node(
            CodexRunner(), rewrite, QualityGates(max_rewrite_rounds=2), checkers=[checker]
        ),
    ]
    run = GraphRuntime(store, nodes).run()
    assert run.state == "CRITIQUED"
    rec = store.find_node(run.id, "critique", "default")
    assert rec is not None
    payload = json.loads(rec.output_json)
    assert payload["items"] == []
    assert any("failed critique after 2 rewrites" in w for w in payload["warnings"])
    assert checker.calls == 3
