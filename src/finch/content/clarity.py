"""ASD-STE100-inspired clarity review models and preset parsing."""

from typing import Literal

from pydantic import BaseModel, Field

RULES_VERSION = "finch-clarity-v1"

ClarityPreset = Literal["asd-ste100-inspired", "asd-ste100-technical"]

_TECHNICAL_NEEDLES = (
    "asd-ste100-technical",
    "清晰的技术操作说明",
    "技术操作说明",
)


def parse_clarity_preset(instruction: str) -> ClarityPreset:
    """Deterministic preset from revise instruction. Default inspired."""
    lowered = instruction.casefold()
    if "asd-ste100-technical" in lowered:
        return "asd-ste100-technical"
    for needle in _TECHNICAL_NEEDLES[1:]:
        if needle in instruction:
            return "asd-ste100-technical"
    return "asd-ste100-inspired"


class ClarityChange(BaseModel):
    rule_id: str
    before: str
    after: str
    reason: str


class ClarityReview(BaseModel):
    preset: ClarityPreset
    rules_version: str = RULES_VERSION
    changes: list[ClarityChange] = Field(default_factory=list, max_length=3)
    meaning_check: Literal["passed", "needs_review"]
    missing_information: list[str] = Field(default_factory=list)


class ClarityEditOutput(BaseModel):
    """LLM structured output for drafts revise (body + review)."""

    body: str
    clarity_review: ClarityReview
