"""Output-quality golden cases (plan §6 Task 0.1 / §14 item 5).

Three fixtures lock in the first-slice writer behavior. Each fixture encodes an
input (evidence card + ContentJob, plus a discussion candidate for the reply case),
a scripted output body, and must-hold assertions. The test drives write_original /
write_reply with a capturing fake runner and checks two things:

1. the prompt carries the full ContentJob context — reader_problem / core_message /
   why_now / scope / decision / tradeoff, plus candidate fields for replies. This is
   the part the old card-only writer dropped, so these assertions discriminate.
2. the output body satisfies the scenario assertions:
   - commit_only: bounded lesson, no industry-wide generalization;
   - commit_plus_discussion: reply names the real problem it answers;
   - inferred_result: boundary language, not a verified/first-person fact.

Scenario map:
1. 只有 Commit → 只能写个人实践/bounded lesson，不做行业普遍化
2. Commit + 外部讨论 → 回复说明回应了哪个真实问题
3. 结果是推断 → 不得写成已验证结果或第一人称亲历事实
"""

import json
from pathlib import Path

from finch.codex.runner import CodexRunner
from finch.content.claims import validate_draft
from finch.content.jobs import ContentJob
from finch.content.models import ClaimRef, Draft, DraftKind
from finch.content.writer import write_original, write_reply
from finch.evidence.models import ClaimConfidence, EvidenceCard, MatchResult
from finch.twitter.models import DiscussionCandidate

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "output_quality"


class CaptureRunner(CodexRunner):
    """Returns a scripted draft and records the prompt the writer handed it."""

    def __init__(self, body: str, claim: ClaimRef):
        self.body = body
        self.claim = claim
        self.prompt: str | None = None

    def run(self, prompt, output_model, **kw):
        self.prompt = prompt
        return Draft(id="golden", kind=DraftKind.ORIGINAL, body=self.body, claims=[self.claim])


def _load(name: str) -> dict:
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text())


def _run(name: str) -> tuple[dict, Draft, str]:
    """Build the input from a fixture, drive the writer, return (fixture, draft, prompt)."""
    fixture = _load(name)
    cards = [EvidenceCard.model_validate(c) for c in fixture["input"]["cards"]]
    job = ContentJob.model_validate(fixture["input"]["job"])
    claim = ClaimRef.model_validate(fixture["output"]["claim"])
    runner = CaptureRunner(fixture["output"]["body"], claim)

    if fixture["kind"] == "original":
        draft = write_original(runner, cards, job)
    else:
        candidate = DiscussionCandidate.model_validate(fixture["input"]["candidate"])
        match = (
            MatchResult.model_validate(fixture["input"]["match"])
            if fixture["input"].get("match") is not None
            else None
        )
        draft = write_reply(runner, match, candidate, {c.id: c for c in cards}, job)

    assert draft is not None
    assert runner.prompt is not None
    return fixture, draft, runner.prompt


def _assert_holds(fixture: dict, body: str, prompt: str) -> None:
    assertions = fixture["assertions"]
    for phrase in assertions.get("body_contains", []):
        assert phrase in body, f"{phrase!r} missing from output body"
    for phrase in assertions.get("body_not_contains", []):
        assert phrase not in body, f"{phrase!r} unexpectedly present in output body"
    for phrase in assertions.get("prompt_contains", []):
        assert phrase in prompt, f"{phrase!r} missing from writer prompt"
    for phrase in assertions.get("prompt_not_contains", []):
        assert phrase not in prompt, f"{phrase!r} unexpectedly present in writer prompt"


def test_golden_commit_only_is_a_bounded_lesson():
    """只有 Commit：只能写个人实践/bounded lesson，不声称行业普遍如此。"""
    fixture, draft, prompt = _run("commit_only")
    _assert_holds(fixture, draft.body, prompt)


def test_golden_commit_plus_discussion_names_the_real_problem():
    """Commit + 外部讨论：回复能说明回应了哪个真实问题。"""
    fixture, draft, prompt = _run("commit_plus_discussion")
    _assert_holds(fixture, draft.body, prompt)


def test_golden_inferred_result_uses_boundary_language():
    """结果是推断：不得写成已验证结果或第一人称亲历事实。"""
    fixture, draft, prompt = _run("inferred_result")
    _assert_holds(fixture, draft.body, prompt)

    # ClaimConfidence 边界行为：INFERRED 不是 assertable，不能作为已发布事实绑定。
    assert ClaimConfidence.INFERRED.assertable is False
    assert ClaimConfidence.VERIFIED.assertable is True
    inferred = ClaimRef(
        statement="the retry change reduced p99 latency",
        evidence_card_id="ev_inferred",
        confidence=ClaimConfidence.INFERRED,
    )
    violations = validate_draft(
        Draft(id="d", kind=DraftKind.ORIGINAL, body="x", claims=[inferred]),
        card_ids={"ev_inferred"},
    )
    assert violations
