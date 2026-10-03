"""Prompt 模板占位符必须与代码 .format() 传参集合一致（防止 KeyError / 静默遗漏）。"""

from pathlib import Path
from string import Formatter

EXPECTED: dict[str, set[str]] = {
    "prompts/opportunity.md": {
        "peer_id",
        "display_name",
        "platform",
        "current_work",
        "why_relevant",
        "their_artifacts",
        "user_context",
        "user_practices",
    },
    "prompts/prepare-contribution.md": {
        "topic",
        "entry_kind",
        "why_me",
        "why_continue",
        "contribution",
        "form",
        "expected_output",
        "scope",
        "cost_note",
        "evidence",
        "voice_summary",
        "user_positions",
        "user_practices",
        "user_reaction",
    },
    "prompts/practice-profile-draft.md": {"repo", "readme"},
    "prompts/clarity-revise.md": {
        "preset",
        "rules",
        "instruction",
        "job_context",
        "body",
        "cards",
    },
    "prompts/analyze-article.md": {
        "sample_size",
        "body",
    },
}

ROOT = Path(__file__).resolve().parents[2]


def _placeholders(text: str) -> set[str]:
    return {f for _, f, _, _ in Formatter().parse(text) if f}


def test_prompt_placeholders_match_code():
    for rel, expected in EXPECTED.items():
        got = _placeholders((ROOT / rel).read_text())
        assert got == expected, f"{rel}: placeholders {got} != expected {expected}"
