"""一次 batch Codex judge：把 ranked pairs 与 evidence cards 交给模型评分。"""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from pydantic import BaseModel

from ..codex.runner import CodexRunner
from ..twitter.models import DiscussionCandidate
from .models import EvidenceCard, JudgeScores, RankedCandidate

_PROMPT_PATH = Path("prompts/match-discussion.md")


class BatchJudgeItem(BaseModel):
    candidate_id: str
    scores: JudgeScores


class BatchJudgeOutput(BaseModel):
    items: list[BatchJudgeItem]


def _to_json_text(models: Sequence[BaseModel]) -> str:
    return json.dumps([m.model_dump(mode="json") for m in models])


def judge_batch(
    runner: CodexRunner,
    ranked: list[RankedCandidate],
    candidates: list[DiscussionCandidate],
    cards: list[EvidenceCard],
) -> BatchJudgeOutput:
    """一次 Codex 调用完成所有 ranked pairs 的评分。

    ranked 为空时不调用 runner，直接返回空 items（0 次调用）。
    """
    if not ranked:
        return BatchJudgeOutput(items=[])
    prompt = _PROMPT_PATH.read_text().format(
        pairs=_to_json_text(ranked),
        candidates=_to_json_text(candidates),
        cards=_to_json_text(cards),
    )
    return cast(BatchJudgeOutput, runner.run(prompt, BatchJudgeOutput))
