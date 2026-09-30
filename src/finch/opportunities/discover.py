"""首选机会发现：把一个人 + 其近期成果转成一条首选机会并落库（规范 §5/§6）。

``discover_preferred_opportunity`` 是 daily 发现链路的接线点：对首选候选做一次 LLM 机会
判断（``assess_opportunity``），确定性转成 ``Opportunity`` 聚合并落库；已存在则返回既有
（幂等，不重复调 LLM），无贡献点则返回 None。
"""

import json

from finch.llm.base import StructuredInferenceRunner
from finch.opportunities.assess import assess_opportunity, build_opportunity
from finch.opportunities.models import Opportunity
from finch.opportunities.service import OpportunityService
from finch.sources.models import RawArtifact

_TEXT_LIMIT = 600


def _truncate(text: str, n: int = _TEXT_LIMIT) -> str:
    text = (text or "").strip()
    if len(text) <= n:
        return text
    return text[: n - 1] + "…"


def render_artifacts_json(artifacts: list[RawArtifact]) -> str:
    """把候选的近期成果渲染为 prompt 用的 JSON 字符串（截断长文本）。"""
    return json.dumps(
        [
            {
                "artifact_id": a.artifact_id,
                "title": a.title or "",
                "text": _truncate(a.text),
                "url": a.canonical_url,
            }
            for a in artifacts
        ],
        ensure_ascii=False,
        indent=2,
    )


def discover_preferred_opportunity(
    *,
    runner: StructuredInferenceRunner,
    peer_id: str,
    display_name: str,
    platform: str,
    current_work: str,
    why_relevant: str,
    person_ref: str,
    artifacts: list[RawArtifact],
    service: OpportunityService,
    user_context: str = "",
) -> Opportunity | None:
    """对首选候选做机会判断并落库；幂等（已存在返回既有），无贡献点 → None。"""
    opportunity_id = f"opp_{person_ref}"
    existing = service.get(opportunity_id)
    if existing is not None:
        return existing

    draft = assess_opportunity(
        runner,
        peer_id=peer_id,
        display_name=display_name,
        platform=platform,
        current_work=current_work,
        why_relevant=why_relevant,
        their_artifacts_json=render_artifacts_json(artifacts),
        user_context=user_context,
    )
    opp = build_opportunity(draft, opportunity_id=opportunity_id, person_ref=person_ref)
    if opp is None:
        return None
    return service.create_from(opp)
