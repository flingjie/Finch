"""从指定 URL 评估交流机会（规范 §4.2 入口 2）。

``assess_from_url``：webfetch 拉正文 → 同一 ``assess_opportunity`` → 落同一聚合。
作者身份不可得时 ``person_ref`` 为空，不形成以此身份为依据的强推荐。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from finch.llm.base import StructuredInferenceRunner
from finch.opportunities.assess import assess_opportunity, build_opportunity
from finch.opportunities.discover import DiscoverOutcome
from finch.opportunities.models import Opportunity
from finch.opportunities.service import OpportunityService
from finch.profile.render import NONE_MARKER
from finch.webfetch.fetcher import WebFetcher, WebSourceUnavailable

_TEXT_LIMIT = 4000


def _truncate(text: str, n: int = _TEXT_LIMIT) -> str:
    text = (text or "").strip()
    if len(text) <= n:
        return text
    return text[: n - 1] + "…"


@dataclass
class UrlAssessResult:
    """URL 评估结果。"""

    opportunity: Opportunity | None
    outcome: str  # recommended | skipped | eval_failed | fetch_failed
    reason: str = ""
    url: str = ""


def assess_from_url(
    *,
    url: str,
    runner: StructuredInferenceRunner,
    service: OpportunityService,
    user_context: str = "",
    user_practices: str = "",
    fetcher: WebFetcher | None = None,
    person_ref: str | None = None,
    display_name: str = "",
    platform: str = "web",
) -> UrlAssessResult:
    """抓取 URL 正文并做机会判断；失败 fail-soft。"""
    url = (url or "").strip()
    if not url:
        return UrlAssessResult(None, "fetch_failed", "url is required", url="")
    gw = fetcher or WebFetcher()
    try:
        body = gw.fetch(url)
    except WebSourceUnavailable as exc:
        return UrlAssessResult(None, "fetch_failed", str(exc), url=url)

    practices_for_fp = (
        user_practices if user_practices and user_practices != NONE_MARKER else ""
    )
    practice_part = f"\n{practices_for_fp}" if practices_for_fp else ""
    fingerprint = hashlib.sha256(
        f"{url}\n{user_context}{practice_part}\n{body[:2000]}".encode()
    ).hexdigest()[:16]
    person_key = person_ref or "unknown"
    opportunity_id = f"opp_url_{person_key}_{fingerprint}"
    existing = service.get(opportunity_id)
    if existing is not None:
        return UrlAssessResult(existing, "recommended", url=url)

    artifacts_json = json.dumps(
        [
            {
                "artifact_id": f"url:{fingerprint}",
                "title": url,
                "text": _truncate(body),
                "url": url,
            }
        ],
        ensure_ascii=False,
        indent=2,
    )
    draft = assess_opportunity(
        runner,
        peer_id=person_ref or "unknown",
        display_name=display_name or url,
        platform=platform,
        current_work=_truncate(body, 400),
        why_relevant=user_context or "用户指定的讨论",
        their_artifacts_json=artifacts_json,
        user_context=user_context,
        user_practices=user_practices,
    )
    if draft.eval_failed:
        return UrlAssessResult(None, "eval_failed", draft.skip_reason, url=url)
    if not draft.recommend or not draft.contribution.strip():
        return UrlAssessResult(
            None, "skipped", draft.skip_reason or "无最小贡献", url=url
        )
    if not person_ref:
        # 身份未知：写入 open_questions，降低强推荐观感。
        unknowns = list(draft.open_questions)
        note = "对方身份未知（仅有 URL），不据此形成强身份推荐"
        if note not in unknowns:
            unknowns.append(note)
        draft = draft.model_copy(update={"open_questions": unknowns})
    if not draft.thread_ref:
        draft = draft.model_copy(update={"thread_ref": url})
    opp = build_opportunity(
        draft, opportunity_id=opportunity_id, person_ref=person_ref, thread_ref=url
    )
    if opp is None:
        return UrlAssessResult(None, "skipped", "无最小贡献", url=url)
    return UrlAssessResult(service.create_from(opp), "recommended", url=url)


def discover_outcome_from_url_result(result: UrlAssessResult) -> DiscoverOutcome:
    """兼容包装：把 UrlAssessResult 转成 DiscoverOutcome（供测试/统一展示）。"""
    outcome = result.outcome if result.outcome in {
        "recommended", "skipped", "eval_failed"
    } else "skipped"
    return DiscoverOutcome(
        result.opportunity,
        outcome,  # type: ignore[arg-type]
        result.reason,
    )
