"""community-scout 薄 loop: feedback 回灌纯函数 + 编排器（见 CommunityLoop）。"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Protocol, cast

from pydantic import BaseModel, Field

from finch.communities.models import (
    CommunityCandidate,
    CommunityEvidence,
    CommunityFeedback,
    CommunityProfile,
    CommunityResult,
    CommunityRun,
    EntryPoint,
    FeedbackFacts,
    RecommendationState,
    RunIntent,
    RunStep,
    ScoutAction,
    ScoutObservation,
    community_id_for,
)
from finch.communities.repository import CommunityRepository
from finch.communities.service import week_label
from finch.llm.base import StructuredInferenceRunner
from finch.settings import CommunityScoutSettings

if TYPE_CHECKING:
    from finch.webfetch.fetcher import WebFetcher

_ENGAGED = {
    CommunityResult.JOINED,
    CommunityResult.INTERACTED,
    CommunityResult.REPEATED,
    CommunityResult.CONTRIBUTED,
}


def derive_feedback_facts(
    latest_by_identity: dict[str, CommunityFeedback],
    *,
    now: datetime | None = None,
    suppress_window: timedelta = timedelta(weeks=4),
) -> FeedbackFacts:
    """由「每个 identity 的最新一条 feedback」派生出确定性事实。

    硬门禁（Python 强制）：
    - ``ignored`` 在 suppress_window 内 → 排除；
    - ``no_time`` 永不排除（当次约束，不永久过滤）；
    - 已发生互动（joined/interacted/repeated/contributed）→ 进入「继续」框架。
    软排序：每个 identity 的「result + reason_kind + note」摘要，注入 judge。
    """
    clock = now or datetime.now(UTC)
    facts = FeedbackFacts()
    for identity, fb in latest_by_identity.items():
        if fb.result == CommunityResult.IGNORED:
            age = clock - fb.at
            if age < suppress_window:
                weeks = int(age.days / 7)
                facts.excluded[identity] = f"ignored {weeks}w ago"
        if fb.result in _ENGAGED:
            facts.continue_framing.append(identity)
        summary = " ".join(
            p for p in [fb.result.value, fb.reason_kind, fb.note] if p
        ).strip()
        facts.summaries[identity] = summary
    return facts


def candidate_identity(c: CommunityCandidate) -> str:
    """候选的跨周去重键：有规范 URL 用 URL，否则回退 name-hash id（与 identity_key 一致）。"""
    return c.canonical_url or community_id_for(c.name)


class CommunitySearchSource(Protocol):
    """search 动作依赖的窄接口：给定意图与目标，返回有界候选列表（只读）。"""

    def search(self, intent: RunIntent, goal: str, limit: int) -> list[CommunityCandidate]: ...


class WebFetcherSearchSource:
    """最小真实 search 源：对配置的 search_urls 逐个 WebFetcher 抓取，产出候选。

    只做检索（raw fetch），判断交给 inspect 的 LLM。多源社区发现（twitter/reddit/v2ex/GitHub
    Discussions）延后：现有 sources 层投影到 peers 而非 communities。
    """

    def __init__(self, search_urls: list[str], fetcher: WebFetcher | None = None) -> None:
        self.search_urls = search_urls
        self._fetcher = fetcher

    def search(self, intent: RunIntent, goal: str, limit: int) -> list[CommunityCandidate]:
        from finch.webfetch.fetcher import WebFetcher

        fetcher = self._fetcher or WebFetcher()
        out: list[CommunityCandidate] = []
        for url in self.search_urls[:limit]:
            try:
                text = fetcher.fetch(url)
            except Exception:  # noqa: BLE001 — 单源失败不阻塞，如实留缺口
                continue
            out.append(
                CommunityCandidate(
                    name=url.rstrip("/").split("/")[-1] or url,
                    canonical_url=url,
                    source_note="webfetch",
                    evidence_text=text,
                )
            )
        return out


_INSPECT_PROMPT = """\
You are checking whether each candidate community is worth entering or observing for the user.

For each candidate, judge from the provided evidence_text whether:
- there is verifiable public evidence of recent, relevant discussion (not just a homepage);
- there is an open entry point (a still-open discussion) or only reading material;
- the community matches the goal.

If it cannot be verified (no public evidence, stale, or unverifiable), set reject_reason to a
short reason. Otherwise set recommendation_state (observe if no current entry point, actionable
if there is a usable one), why_fit, evidence_urls, and entry_point when available. Do not invent
URLs or evidence; leave fields empty when unknown. Never mark anything "confirmed" or a real
interaction.

Goal: {goal}
Feedback summaries (latest per community; for soft ranking only): {summaries}

## Candidates

{candidates}

Return JSON matching the schema.
"""

_PROPOSE_PROMPT = """\
You write up to {max_cards} community cards from verified candidates. Order by problem fit and
a concrete next step. Each card needs name, canonical_url, recommendation_state, why_fit,
evidence_urls, and (when actionable) entry_point. Do not invent first-person experience or
interaction facts; evidence_urls must be the real public URLs from the candidate. If a candidate
is in the continue-framing list, frame the next step as continuing the discussion, never "first
join".

Continue-framing identities: {continue_framing}

## Verified candidates

{verified}

Return JSON matching the schema.
"""


class InspectedCandidate(BaseModel):
    """inspect 判定后的一个候选：reject_reason 非空即淘汰。"""

    name: str
    canonical_url: str = ""
    recommendation_state: RecommendationState | None = None
    why_fit: list[str] = Field(default_factory=list)
    recent_evidence: list[CommunityEvidence] = Field(default_factory=list)
    entry_point: EntryPoint | None = None
    evidence_urls: list[str] = Field(default_factory=list)
    reject_reason: str = ""


class InspectOutput(BaseModel):
    candidates: list[InspectedCandidate] = Field(default_factory=list)


class ProposeOutput(BaseModel):
    cards: list[CommunityProfile] = Field(default_factory=list)


def _render_candidates(candidates: list[CommunityCandidate]) -> str:
    return "\n".join(
        f"- {c.name} | {c.canonical_url or '(no url)'} | {c.source_note}\n  {c.evidence_text[:400]}"
        for c in candidates
    )


def _render_verified(profiles: list[CommunityProfile]) -> str:
    lines = []
    for p in profiles:
        state = p.recommendation_state.value if p.recommendation_state else "-"
        lines.append(f"- {p.name} | {p.canonical_url} | {state}")
    return "\n".join(lines)


def _matches_verified(card: CommunityProfile, verified: list[CommunityProfile]) -> bool:
    """propose 卡必须对应一个已验证候选（按 name，或两者非空时按 canonical_url）。"""
    for v in verified:
        if card.name.strip().lower() == v.name.strip().lower():
            return True
        if card.canonical_url and v.canonical_url and card.canonical_url == v.canonical_url:
            return True
    return False


class CommunityLoop:
    """有界确定性 loop：search → inspect → propose → finish。

    LLM 只在 inspect（核验/分层）与 propose（写卡）两个判断点被单发调用；Python 决定
    下一步、预算、去重、门禁与 trace。
    """

    def __init__(
        self,
        runner: StructuredInferenceRunner,
        search_source: CommunitySearchSource,
        repo: CommunityRepository,
        *,
        budget: CommunityScoutSettings,
    ) -> None:
        self.runner = runner
        self.search_source = search_source
        self.repo = repo
        self.budget = budget

    def run(self, intent: RunIntent, goal: str, *, now: datetime | None = None) -> CommunityRun:
        clock = now or datetime.now(UTC)
        digest = hashlib.sha256(goal.encode()).hexdigest()[:6]
        run_id = f"run_{clock.strftime('%Y%m%d%H%M%S')}_{digest}"
        run = CommunityRun(
            run_id=run_id, intent=intent, goal=goal, week=week_label(clock), status="running"
        )
        self.repo.append_run(run)

        try:
            facts = derive_feedback_facts(
                self.repo.latest_feedback_by_identity(),
                now=clock,
                suppress_window=timedelta(weeks=self.budget.suppress_window_weeks),
            )

            # search
            raw = self.search_source.search(intent, goal, self.budget.max_candidates)
            kept = [c for c in raw if candidate_identity(c) not in facts.excluded]
            self._step(
                run_id,
                ScoutAction.SEARCH,
                ScoutObservation(
                    candidates=kept,
                    gap_note=f"excluded {len(raw) - len(kept)} by hard gate",
                ),
                decision=f"kept {len(kept)} after hard gate",
                outcome=f"candidates_found={len(kept)}",
                llm_calls=0,
            )
            run = run.model_copy(update={"candidates_found": len(kept)})

            # inspect（有界：最多 max_reinspect_rounds 次「再 inspect 下一批」）
            verified: list[CommunityProfile] = []
            inspected = 0
            reinspect_rounds = 0
            if not kept:
                # 硬门禁排除了全部候选：仍写一条 INSPECT，保持 trace 四步齐全。
                self._step(
                    run_id,
                    ScoutAction.INSPECT,
                    ScoutObservation(gap_note="hard gate excluded all candidates"),
                    decision="nothing to inspect",
                    outcome="inspected=0",
                    llm_calls=0,
                )
            while inspected < len(kept) and reinspect_rounds <= self.budget.max_reinspect_rounds:
                batch = kept[inspected : inspected + self.budget.inspect_batch]
                batch_names = {c.name.strip().lower() for c in batch}
                batch_name_by_lower = {c.name.strip().lower(): c.name for c in batch}
                summary_lines = "\n".join(
                    f"{k}: {v}" for k, v in facts.summaries.items()
                ) or "(none)"
                out = cast(InspectOutput, self.runner.run(
                    _INSPECT_PROMPT.format(
                        goal=goal,
                        summaries=summary_lines,
                        candidates=_render_candidates(batch),
                    ),
                    InspectOutput,
                ))
                batch_verified: list[CommunityProfile] = []
                batch_rejected: list[dict] = []
                for ic in out.candidates:
                    key = ic.name.strip().lower()
                    if key not in batch_names:
                        batch_rejected.append(
                            {"name": ic.name, "reason": "未匹配输入候选（丢弃）"}
                        )
                        continue
                    if ic.reject_reason:
                        batch_rejected.append(
                            {"name": batch_name_by_lower[key], "reason": ic.reject_reason}
                        )
                        continue
                    batch_verified.append(
                        CommunityProfile(
                            name=ic.name,
                            canonical_url=ic.canonical_url,
                            recommendation_state=ic.recommendation_state,
                            why_fit=ic.why_fit,
                            recent_evidence=ic.recent_evidence,
                            entry_point=ic.entry_point,
                            evidence_urls=ic.evidence_urls,
                        )
                    )
                verified.extend(batch_verified)
                inspected += len(batch)
                self._step(
                    run_id,
                    ScoutAction.INSPECT,
                    ScoutObservation(verified=list(batch_verified), rejected=batch_rejected),
                    decision=(
                        f"verified {len(batch_verified)} / rejected "
                        f"{len(batch) - len(batch_verified)}"
                    ),
                    outcome=f"inspected={inspected}",
                    llm_calls=1,
                )
                if batch_verified:
                    break  # 有通过核验的候选，不再 re-inspect
                reinspect_rounds += 1

            # propose
            cards: list[CommunityProfile] = []
            if verified:
                propose_out = cast(ProposeOutput, self.runner.run(
                    _PROPOSE_PROMPT.format(
                        max_cards=self.budget.max_cards,
                        continue_framing=", ".join(facts.continue_framing) or "(none)",
                        verified=_render_verified(verified),
                    ),
                    ProposeOutput,
                ))
                cards = [
                    card
                    for card in propose_out.cards[: self.budget.max_cards]
                    if _matches_verified(card, verified)
                ]
            self._step(
                run_id,
                ScoutAction.PROPOSE,
                ScoutObservation(cards=list(cards)),
                decision=f"proposed {len(cards)} cards",
                outcome=f"cards_proposed={len(cards)}",
                llm_calls=1 if cards else 0,
            )

            # finish
            for card in cards:
                card = card.model_copy(update={"week": run.week})
                if not card.id:
                    card = card.model_copy(update={"id": community_id_for(card.name)})
                self.repo.append_candidate(card)
            finished = run.model_copy(
                update={
                    "status": "done",
                    "cards_proposed": len(cards),
                    "budget_used": inspected,
                    "finished_at": datetime.now(UTC),
                }
            )
            self.repo.append_run(finished)
            self._step(
                run_id,
                ScoutAction.FINISH,
                ScoutObservation(),
                decision=f"saved {len(cards)} cards",
                outcome=f"status={finished.status}",
                llm_calls=0,
            )
            return finished
        except Exception:
            failed = run.model_copy(update={"status": "failed", "finished_at": clock})
            self.repo.append_run(failed)
            raise

    def _step(self, run_id, action, observation, *, decision, outcome, llm_calls):
        self.repo.append_step(
            RunStep(
                run_id=run_id,
                action=action,
                observation=observation,
                decision=decision,
                outcome=outcome,
                llm_calls=llm_calls,
            )
        )
