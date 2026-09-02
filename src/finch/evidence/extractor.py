"""从 Commit 组提取 Engineering Event 并生成 Evidence Card（spec 2.2）。"""

from pathlib import Path
from typing import cast

from ..codex.runner import CodexRunner
from ..github.change_grouper import group_commits
from ..github.models import CommitDetail
from .models import EngineeringEvent, EvidenceCard, Source

_PROMPT_PATH = Path("prompts/extract-engineering-event.md")


def _render_commits(commits: list[CommitDetail]) -> str:
    lines = []
    for c in commits:
        lines.append(f"- {c.sha[:8]} {c.message}")
        for f in c.files[:12]:
            lines.append(f"    {f.status} {f.filename} (+{f.additions}/-{f.deletions})")
    return "\n".join(lines)


class Extractor:
    def __init__(self, runner: CodexRunner):
        self.runner = runner

    def extract(self, commits: list[CommitDetail], repo: str) -> list[EngineeringEvent]:
        events: list[EngineeringEvent] = []
        for group in group_commits(commits):
            prompt = _PROMPT_PATH.read_text().replace("{commits}", _render_commits(group))
            event = cast(EngineeringEvent, self.runner.run(prompt, EngineeringEvent))
            if event.repository != repo:
                event = event.model_copy(update={"repository": repo})
            events.append(event)
        return events


def build_cards(events: list[EngineeringEvent]) -> list[EvidenceCard]:
    cards: list[EvidenceCard] = []
    for ev in events:
        base = f"https://github.com/{ev.repository}/commit/"
        for claim, label in ((ev.problem, "problem"), (ev.result, "result")):
            cards.append(EvidenceCard(
                id=f"ev_{ev.id}_{label}",
                event_id=ev.id,
                claim=claim.statement,
                sources=[Source(type="commit", url=base + c) for c in ev.commits],
                confidence=claim.confidence,
                publishable=True,
                topics=[],
            ))
        cards.append(EvidenceCard(
            id=f"ev_{ev.id}_decision",
            event_id=ev.id,
            claim=ev.decision.statement,
            sources=[Source(type="commit", url=base + c) for c in ev.commits],
            confidence=ev.decision.confidence,
            publishable=True,
            topics=[],
        ))
    return cards
