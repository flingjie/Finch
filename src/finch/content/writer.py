"""Writer：把证据卡写成英文回复 / 中文日记草稿（contract C4）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, cast

from finch.codex.runner import CodexRunner
from finch.content.claims import validate_draft
from finch.content.jobs import ContentJob
from finch.content.models import Draft, DraftKind
from finch.evidence.models import EvidenceCard, MatchResult
from finch.twitter.models import DiscussionCandidate

if TYPE_CHECKING:
    # Forward reference: CritiqueResult is defined by F5 (finch.content.critic),
    # not yet present when this module is imported standalone.
    from finch.content.critic import CritiqueResult  # type: ignore[import-not-found,import-untyped]

_REPLY_PROMPT_PATH = Path("prompts/draft-reply.md")
_ORIGINAL_PROMPT_PATH = Path("prompts/draft-original.md")

_REWRITE_PROMPT = """\
You rewrite a draft to address the critique issues. Return JSON matching the schema.
Instructions:
- Keep the same id, kind, candidate_id, and language as the Original draft.
- Only use evidence cards listed under Evidence cards, referenced by id.
- Every claim must carry an evidence_card_id and a confidence that the card supports.
- Fix every issue listed under Critique issues.

## Original draft
{body}

## Critique issues
{issues}

## Evidence cards
{cards}
"""


def _render_cards(cards: list[EvidenceCard]) -> str:
    return json.dumps([card.model_dump(mode="json") for card in cards])


def _cards_for_match(
    match: MatchResult, cards_by_id: dict[str, EvidenceCard]
) -> list[EvidenceCard]:
    return [cards_by_id[cid] for cid in match.card_ids if cid in cards_by_id]


def write_reply(
    runner: CodexRunner,
    match: MatchResult | None,
    candidate: DiscussionCandidate,
    cards_by_id: dict[str, EvidenceCard],
    job: ContentJob | None = None,
) -> Draft | None:
    if match:
        match_cards = _cards_for_match(match, cards_by_id)
    else:
        # For non-match jobs, use cards from job's source_card_ids
        match_cards = (
            [cards_by_id[cid] for cid in job.source_card_ids if cid in cards_by_id]
            if job
            else []
        )
    prompt = _REPLY_PROMPT_PATH.read_text().format(
        candidate=candidate.text,
        cards=_render_cards(match_cards),
    )
    draft = cast(Draft, runner.run(prompt, Draft))
    if match:
        card_ids = set(match.card_ids)
    else:
        card_ids = set(job.source_card_ids) if job else set()
    if validate_draft(draft, card_ids=card_ids):
        return None
    result = draft.model_copy(
        update={
            "kind": DraftKind.REPLY,
            "candidate_id": match.candidate_id if match else candidate.id,
            "language": "en",
            "content_job_id": job.id if job else None,
            "position_statement": (
                job.author_position.decision
                if job and job.author_position
                else ""
            ),
        }
    )
    return result


def write_original(
    runner: CodexRunner,
    cards: list[EvidenceCard],
    job: ContentJob | None = None,
) -> Draft | None:
    prompt = _ORIGINAL_PROMPT_PATH.read_text().format(cards=_render_cards(cards))
    draft = cast(Draft, runner.run(prompt, Draft))
    if validate_draft(draft, card_ids={card.id for card in cards}):
        return None
    result = draft.model_copy(
        update={
            "kind": DraftKind.ORIGINAL,
            "candidate_id": None,
            "language": "zh",
            "content_job_id": job.id if job else None,
            "position_statement": (
                job.author_position.decision
                if job and job.author_position
                else ""
            ),
        }
    )
    return result


def rewrite(
    runner: CodexRunner,
    draft: Draft,
    critique: CritiqueResult,
    cards_by_id: dict[str, EvidenceCard],
) -> Draft:
    card_ids = {ref.evidence_card_id for ref in draft.claims}
    cards = [cards_by_id[cid] for cid in card_ids if cid in cards_by_id]
    prompt = _REWRITE_PROMPT.format(
        body=draft.body,
        issues="\n".join(f"- {issue}" for issue in critique.issues),
        cards=_render_cards(cards),
    )
    out = cast(Draft, runner.run(prompt, Draft))
    return out.model_copy(
        update={
            "id": draft.id,
            "kind": draft.kind,
            "candidate_id": draft.candidate_id,
            "language": draft.language,
        }
    )
