"""Harness: fixture samples → control discover vs shadow validate-upsert → compare metrics."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from finch.problem_discovery.discover import (
    PROBLEM_VERSION,
    ProblemDraft,
    ProblemEvidenceDraft,
    ProblemsExtraction,
    discover_problems,
)
from finch.problem_discovery.importing import import_discussion
from finch.problem_discovery.repository import (
    DiscoveryRunRepository,
    DiscussionRepository,
    ProblemRepository,
)
from finch.problem_discovery.shadow import compare_runs, export_candidates, validate_upsert
from finch.sources.store import ArtifactRepository
from finch.storage.workspace import Workspace

_FIXTURES = Path("tests/fixtures/problem_discovery_shadow/manifest.json")


def _load_samples() -> list[dict]:
    data = json.loads(_FIXTURES.read_text(encoding="utf-8"))
    return list(data["samples"])


def _quote_from(text: str, min_len: int = 12) -> str:
    words = text.split()
    if len(words) >= 4:
        return " ".join(words[:4])
    return text[: max(min_len, min(40, len(text)))]


def _control_runner(source_texts: dict[str, str]):
    """Deterministic stand-in for assess_problems (same draft quality bar as shadow)."""

    class _Runner:
        def run(self, prompt: str, output_model: type[BaseModel], *, timeout: float = 600.0):
            problems: list[ProblemDraft] = []
            for ref, text in sorted(source_texts.items()):
                quote = _quote_from(text)
                if quote.lower() not in text.lower() and quote not in text:
                    # ensure substring after normalize-ish: use first 20 chars present
                    quote = text.strip()[:20]
                title = text.strip().split(".")[0][:80] or f"problem from {ref}"
                problems.append(
                    ProblemDraft(
                        title=title,
                        task="reproduce reported failure",
                        evidence=[
                            ProblemEvidenceDraft(
                                source_ref=ref, quote=quote, claim="user-reported failure"
                            )
                        ],
                        unknowns=["severity outside this fixture unknown"],
                        next_action="observe",
                    )
                )
            return ProblemsExtraction(problems=problems)

    return _Runner()


def _shadow_drafts(source_texts: dict[str, str], *, inject_bad: bool = True) -> ProblemsExtraction:
    problems: list[ProblemDraft] = []
    for ref, text in sorted(source_texts.items()):
        quote = text.strip()[:24] if len(text.strip()) >= 24 else text.strip()
        title = text.strip().split(".")[0][:80] or f"problem from {ref}"
        problems.append(
            ProblemDraft(
                title=title,
                task="reproduce reported failure",
                evidence=[
                    ProblemEvidenceDraft(
                        source_ref=ref,
                        quote=quote,
                        claim="user-reported failure",
                        url="https://evil.example/should-be-ignored",
                    )
                ],
                unknowns=["severity outside this fixture unknown"],
                next_action="observe",
            )
        )
    if inject_bad:
        problems.append(
            ProblemDraft(
                title="invented citation should lose evidence",
                evidence=[
                    ProblemEvidenceDraft(
                        source_ref=next(iter(source_texts)),
                        quote="zzz-not-in-any-fixture-body-zzzz",
                    )
                ],
            )
        )
    return ProblemsExtraction(problems=problems)


def test_shadow_experiment_fixtures_vs_control(tmp_path):
    samples = _load_samples()
    assert 8 <= len(samples) <= 12

    ws = Workspace(tmp_path)
    ws.ensure()
    for s in samples:
        import_discussion(
            ws, url=s["url"], text=s["text"], title=s["title"], author="fixture"
        )

    arts = ArtifactRepository(ws)
    discs = DiscussionRepository(ws)
    export = export_candidates(
        artifact_repo=arts,
        discussion_repo=discs,
        version=PROBLEM_VERSION,
        lookback_hours=720,
        max_sources=40,
        max_chars_per_source=4000,
        now=datetime(2026, 10, 30, 20, 0, tzinfo=UTC),
    )
    assert export.budget_stats["source_count"] == len(samples)
    source_texts = {row["id"]: row["text"] for row in export.sources}

    control_problems = discover_problems(
        _control_runner(source_texts),
        problem_repo=ProblemRepository(ws, shadow=False),
        artifact_repo=arts,
        discussion_repo=discs,
        run_repo=DiscoveryRunRepository(ws, shadow=False),
        version=PROBLEM_VERSION,
        force=True,
        now=datetime(2026, 10, 30, 20, 0, tzinfo=UTC),
    )
    assert control_problems is not None
    assert len(control_problems) == len(samples)

    shadow = validate_upsert(
        _shadow_drafts(source_texts, inject_bad=True),
        artifact_repo=arts,
        discussion_repo=discs,
        problem_repo=ProblemRepository(ws, shadow=True),
        run_repo=DiscoveryRunRepository(ws, shadow=True),
        version=PROBLEM_VERSION,
        now=datetime(2026, 10, 30, 20, 0, tzinfo=UTC),
    )
    # Good drafts kept; invented quote → card may keep with empty evidence
    assert shadow.kept_count == len(samples) + 1
    invented = next(p for p in shadow.problems if "invented" in p.title)
    assert invented.evidence == []

    # No invented URLs survived
    for p in shadow.problems:
        for ev in p.evidence:
            assert "evil.example" not in ev.url

    cmp = compare_runs(
        control_run_repo=DiscoveryRunRepository(ws, shadow=False),
        shadow_run_repo=DiscoveryRunRepository(ws, shadow=True),
        control_problem_repo=ProblemRepository(ws, shadow=False),
        shadow_problem_repo=ProblemRepository(ws, shadow=True),
    )
    assert cmp.fingerprint_match is True
    # Titles differ slightly → problem_ids may differ; evidence counts should be healthy
    assert cmp.control_evidence_count >= len(samples)
    assert cmp.shadow_evidence_count >= len(samples)
    # Invented card has zero evidence — already asserted
    metrics = {
        "samples": len(samples),
        "control_cards": len(control_problems),
        "shadow_cards": shadow.kept_count,
        "shadow_evidence": cmp.shadow_evidence_count,
        "control_evidence": cmp.control_evidence_count,
        "invented_url_rate": 0,
        "fingerprint_match": cmp.fingerprint_match,
        "gate_drops_recorded": len(shadow.dropped),
    }
    # Persist metrics path for results doc authors (test workspace ephemeral).
    assert metrics["invented_url_rate"] == 0
    assert metrics["fingerprint_match"] is True
