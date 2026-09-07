"""Eval corpus: Spec §10 Phase E — representative scenarios for the effective-content
system (read-focused, deterministic; scripted fake runners, no real LLM).

One scenario = one clearly-named test. Remaining scenarios after the graph-runtime removal
cover the checker/aggregation layer directly:
4. 通用 AI 套话 → Portability/Specificity fail
5. 有数字但无证据 → hard fail (reject)
6. 有明确取舍且风格自然 → pass
"""

from types import SimpleNamespace

from finch.content.checkers import (
    CheckContext,
    EvidenceChecker,
    PortabilityChecker,
    SpecificityChecker,
    aggregate_checks,
)
from finch.content.critic import default_checker_suite
from finch.content.checkers.portability import _PortabilityFinding
from finch.content.jobs import (
    AuthorPosition,
    ContentJob,
    ContentJobStatus,
)
from finch.content.models import ClaimRef, Draft, DraftKind
from finch.evidence.models import ClaimConfidence, EvidenceCard

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
    "_PortabilityOutput": {"findings": []},
    "_VoiceOutput": {"matches_voice": True, "non_author_sentences": []},
    "_StructureOutput": {"confirmed_problems": []},
    "_SafetyOutput": {"invented_personal_experience": False, "unsupported_metric": False},
}


# --- fixtures ---------------------------------------------------------------


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
) -> AuthorPosition:
    return AuthorPosition(
        claim="pool size 10 is the right call",
        decision=decision,
        tradeoff=tradeoff,
    )


_DEFAULT_POSITION = _position()


def _job(
    job_id: str = "job1",
    source_card_ids: tuple[str, ...] = ("ev1",),
    position: AuthorPosition | None = _DEFAULT_POSITION,
    status: ContentJobStatus = ContentJobStatus.CONFIRMED,
) -> ContentJob:
    return ContentJob(
        id=job_id,
        source_card_ids=list(source_card_ids),
        candidate_id="t1",
        reader_problem="readers don't know how to rate limit",
        author_position=position,
        recommended_format=DraftKind.REPLY,
        status=status,
        core_message="token bucket rate limiting",
    )


# --- the remaining scenarios ------------------------------------------------


def test_scenario_4_generic_ai_boilerplate_fails_portability_and_specificity():
    """通用 AI 套话 → Specificity（确定性）与 Portability（脚本化）均失败。"""
    body = "This is a great, powerful, seamless solution."
    draft = _draft(body=body)
    cards = [_card()]

    specificity = SpecificityChecker().check(CheckContext(draft=draft, cards=cards))
    assert specificity.passed is False
    assert specificity.severity in ("medium", "high")

    runner = FakeRunner(SimpleNamespace(findings=[_PortabilityFinding(sentence=body, kind="boilerplate")]))
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
