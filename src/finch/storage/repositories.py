"""文件工作区仓储：领域模型 → YAML / frontmatter-Markdown / JSONL。

保留原公有方法签名，只把 ``Session.merge/commit`` 换成文件原子写；构造入参由
旧的 SQLite 存储改为 ``Workspace``。二级查找（find_by_generation_key / list_by_* /
list_pending）由「同类独立目录 + glob + Python 过滤」实现。
"""

from datetime import UTC, datetime
from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError

from finch.author.models import PublicationIntent
from finch.content.checkers.base import CheckResult
from finch.content.jobs import ContentJob
from finch.content.models import Draft
from finch.conversations.models import ConversationThread
from finch.engagement.models import (
    ConversationEvidence,
    DiscoverySnapshot,
    EngagementRunStats,
    FeedbackSnapshot,
    InteractionProposal,
    InteractionRecord,
    InteractionStatus,
    Opportunity,
    PresentationRecord,
    RecommendationFeedback,
)
from finch.evidence.models import EvidenceCard
from finch.inbox.models import DecisionRecord
from finch.learn.models import Feedback
from finch.peers.models import PeerProfile
from finch.practice.models import PracticeSession
from finch.storage.workspace import Workspace


def _write(ws: Workspace, name: str, key: str, model: BaseModel) -> None:
    ws.write_yaml(ws.dir(name) / f"{ws.safe_filename(key)}.yaml", model)


def _read[T: BaseModel](ws: Workspace, name: str, key: str, model_cls: type[T]) -> T | None:
    return ws.read_yaml(ws.dir(name) / f"{ws.safe_filename(key)}.yaml", model_cls)


def _list_all[T: BaseModel](ws: Workspace, name: str, model_cls: type[T]) -> list[T]:
    out: list[T] = []
    for path in sorted(ws.dir(name).glob("*.yaml")):
        obj = ws.read_yaml(path, model_cls)
        if obj is not None:
            out.append(obj)
    return out


class EvidenceRepository:
    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def upsert_card(self, card: EvidenceCard) -> None:
        _write(self.ws, "evidence", card.id, card)

    def upsert_cards(self, cards: list[EvidenceCard]) -> None:
        for card in cards:
            self.upsert_card(card)

    def get_card(self, card_id: str) -> EvidenceCard | None:
        return _read(self.ws, "evidence", card_id, EvidenceCard)

    def list_cards(self) -> list[EvidenceCard]:
        return _list_all(self.ws, "evidence", EvidenceCard)


class DraftRepository:
    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def _path(self, draft_id: str) -> Path:
        return self.ws.dir("drafts") / self.ws.safe_filename(draft_id) / "draft.md"

    def upsert_draft(self, draft: Draft) -> None:
        meta = draft.model_dump(mode="json", exclude={"body"})
        self.ws.write_frontmatter(self._path(draft.id), meta, draft.body)

    def get_draft(self, draft_id: str) -> Draft | None:
        path = self._path(draft_id)
        if not path.exists():
            return None
        meta, body = self.ws.read_frontmatter(path)
        return Draft.model_validate({**meta, "body": body})

    def list_drafts(self) -> list[Draft]:
        out: list[Draft] = []
        for path in sorted(self.ws.dir("drafts").glob("*/draft.md")):
            meta, body = self.ws.read_frontmatter(path)
            out.append(Draft.model_validate({**meta, "body": body}))
        return out

    def list_by_job(self, job_id: str) -> list[Draft]:
        return [d for d in self.list_drafts() if d.content_job_id == job_id]


class DecisionRecordRepository:
    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def save(self, record: DecisionRecord) -> None:
        _write(self.ws, "decisions", record.id, record)

    def get(self, job_id: str) -> DecisionRecord | None:
        return _read(self.ws, "decisions", f"dec_{job_id}", DecisionRecord)

    def list(self) -> list[DecisionRecord]:
        return _list_all(self.ws, "decisions", DecisionRecord)


class FeedbackRepository:
    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def save_feedback(self, feedback: Feedback) -> None:
        _write(self.ws, "feedback", feedback.draft_id, feedback)

    def get_feedback(self, draft_id: str) -> Feedback | None:
        return _read(self.ws, "feedback", draft_id, Feedback)

    def list_feedbacks(self) -> list[Feedback]:
        return _list_all(self.ws, "feedback", Feedback)


class ContentJobRepository:
    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def upsert_job(self, job: ContentJob) -> None:
        _write(self.ws, "ideas", job.id, job)

    def upsert_jobs(self, jobs: list[ContentJob]) -> None:
        for job in jobs:
            self.upsert_job(job)

    def get_job(self, job_id: str) -> ContentJob | None:
        return _read(self.ws, "ideas", job_id, ContentJob)

    def find_by_generation_key(self, generation_key: str) -> ContentJob | None:
        if generation_key is None:
            return None
        for job in self.list_jobs():
            if job.generation_key == generation_key:
                return job
        return None

    def list_jobs(self) -> list[ContentJob]:
        jobs, _ = self._list_jobs_and_failures()
        return jobs

    def list_job_parse_failures(self) -> list[str]:
        _, failures = self._list_jobs_and_failures()
        return failures

    def _list_jobs_and_failures(self) -> tuple[list[ContentJob], list[str]]:
        jobs: list[ContentJob] = []
        failures: list[str] = []
        for path in sorted(self.ws.dir("ideas").glob("*.yaml")):
            try:
                job = self.ws.read_yaml(path, ContentJob)
            except (ValidationError, yaml.YAMLError):
                failures.append(path.stem)
            else:
                if job is not None:
                    jobs.append(job)
        return jobs, failures


class DraftVersionRepository:
    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def upsert_version(self, draft_id: str, round: int, draft: Draft) -> None:
        meta = draft.model_dump(mode="json", exclude={"body"})
        path = (
            self.ws.dir("drafts") / self.ws.safe_filename(draft_id) / "versions" / f"{round}.md"
        )
        self.ws.write_frontmatter(path, meta, draft.body)

    def list_versions(self, draft_id: str) -> list[Draft]:
        d = self.ws.dir("drafts") / self.ws.safe_filename(draft_id) / "versions"
        out: list[Draft] = []
        for path in sorted(d.glob("*.md"), key=lambda p: int(p.stem)):
            meta, body = self.ws.read_frontmatter(path)
            out.append(Draft.model_validate({**meta, "body": body}))
        return out


class CriticReportRepository:
    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def _path(self, draft_id: str) -> Path:
        return self.ws.dir("drafts") / self.ws.safe_filename(draft_id) / "critic.jsonl"

    def upsert_report(
        self, draft_id: str, round: int, checks: list[CheckResult], outcome: str
    ) -> None:
        payload = {
            "draft_id": draft_id,
            "round": round,
            "checks": [c.model_dump(mode="json") for c in checks],
            "outcome": outcome,
            "ts": datetime.now(UTC).isoformat(),
        }
        self.ws.append_jsonl(self._path(draft_id), payload)

    def list_reports(self, draft_id: str) -> list[dict]:
        rows = self.ws.read_jsonl(self._path(draft_id))
        latest = {r["round"]: r for r in rows}
        return [
            {"checks": latest[k]["checks"], "outcome": latest[k]["outcome"]}
            for k in sorted(latest)
        ]

    def list_all_reports(self, since: datetime | None = None) -> dict[str, list[dict]]:
        grouped: dict[str, list[dict]] = {}
        for path in sorted(self.ws.dir("drafts").glob("*/critic.jsonl")):
            for r in self.ws.read_jsonl(path):
                if since is not None and datetime.fromisoformat(r["ts"]) < since:
                    continue
                grouped.setdefault(r["draft_id"], []).append(r)
        result: dict[str, list[dict]] = {}
        for draft_id, rows in grouped.items():
            latest = {r["round"]: r for r in rows}
            result[draft_id] = [
                {"checks": latest[k]["checks"], "outcome": latest[k]["outcome"]}
                for k in sorted(latest)
            ]
        return result


class InteractionRepository:
    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def upsert(self, candidate: InteractionProposal, run_id: str) -> None:
        # run_id 保留签名兼容；文件形式不单独持久化（原列仅用于回填，从未被读取）。
        _write(self.ws, "interactions/proposals", candidate.id, candidate)

    def get(self, candidate_id: str) -> InteractionProposal | None:
        return _read(self.ws, "interactions/proposals", candidate_id, InteractionProposal)

    def find_by_generation_key(self, generation_key: str) -> InteractionProposal | None:
        if generation_key is None:
            return None
        for c in self.list_all():
            if c.generation_key == generation_key:
                return c
        return None

    def list_pending(self) -> list[InteractionProposal]:
        return [c for c in self.list_all() if c.status == InteractionStatus.PROPOSED]

    def list_all(self) -> list[InteractionProposal]:
        return _list_all(self.ws, "interactions/proposals", InteractionProposal)

    def list_executed(self) -> list[InteractionProposal]:
        return [c for c in self.list_all() if c.status == InteractionStatus.EXECUTED]

    def _update(self, candidate_id: str, **updates: object) -> None:
        candidate = self.get(candidate_id)
        if candidate is None:
            raise KeyError(candidate_id)
        self.upsert(candidate.model_copy(update=updates), run_id="")

    def approve(self, candidate_id: str) -> None:
        self._update(candidate_id, status=InteractionStatus.APPROVED, reject_reason=None)

    def reject(self, candidate_id: str, reason: str) -> None:
        self._update(candidate_id, status=InteractionStatus.REJECTED, reject_reason=reason)

    def edit(self, candidate_id: str, revised_draft: str) -> None:
        self._update(candidate_id, revised_draft=revised_draft)

    def record_execution(self, candidate_id: str, outcome: str, detail: str) -> None:
        candidate = self.get(candidate_id)
        if candidate is None:
            raise KeyError(candidate_id)
        self.upsert(
            candidate.model_copy(update={"status": InteractionStatus.EXECUTED}), run_id=""
        )


class OpportunityRepository:
    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def upsert(self, opportunity: Opportunity) -> None:
        _write(self.ws, "interactions/opportunities", opportunity.id, opportunity)

    def get(self, opportunity_id: str) -> Opportunity | None:
        return _read(self.ws, "interactions/opportunities", opportunity_id, Opportunity)

    def list_all(self) -> list[Opportunity]:
        return _list_all(self.ws, "interactions/opportunities", Opportunity)

    def list_by_ids(self, ids: list[str]) -> list[Opportunity]:
        by_id = {o.id: o for o in self.list_all()}
        return [by_id[i] for i in ids if i in by_id]


class DiscoverySnapshotRepository:
    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def upsert(self, snapshot: DiscoverySnapshot) -> None:
        _write(self.ws, "interactions/discovery-snapshots", snapshot.id, snapshot)

    def get(self, snapshot_id: str) -> DiscoverySnapshot | None:
        return _read(
            self.ws, "interactions/discovery-snapshots", snapshot_id, DiscoverySnapshot
        )

    def list_all(self) -> list[DiscoverySnapshot]:
        return _list_all(self.ws, "interactions/discovery-snapshots", DiscoverySnapshot)

    def latest(self) -> DiscoverySnapshot | None:
        items = sorted(self.list_all(), key=lambda s: s.created_at, reverse=True)
        return items[0] if items else None


class PresentationRecordRepository:
    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def upsert(self, record: PresentationRecord) -> None:
        _write(self.ws, "interactions/presentations", record.id, record)

    def list_for_snapshot(self, snapshot_id: str) -> list[PresentationRecord]:
        return [r for r in self.list_all() if r.snapshot_id == snapshot_id]

    def list_all(self) -> list[PresentationRecord]:
        return _list_all(self.ws, "interactions/presentations", PresentationRecord)

    def presented_ids(self, snapshot_id: str) -> set[str]:
        return {r.opportunity_id for r in self.list_for_snapshot(snapshot_id)}


class RecommendationFeedbackRepository:
    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def upsert(self, feedback: RecommendationFeedback) -> None:
        _write(self.ws, "interactions/recommendation-feedback", feedback.id, feedback)

    def list_all(self) -> list[RecommendationFeedback]:
        return _list_all(
            self.ws, "interactions/recommendation-feedback", RecommendationFeedback
        )


class FeedbackSnapshotRepository:
    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def upsert(self, snapshot: FeedbackSnapshot) -> None:
        _write(self.ws, "interactions/snapshots", snapshot.id, snapshot)

    def get(self, interaction_id: str) -> FeedbackSnapshot | None:
        matches = [s for s in self.list_all() if s.interaction_id == interaction_id]
        return matches[0] if matches else None

    def list_all(self) -> list[FeedbackSnapshot]:
        return _list_all(self.ws, "interactions/snapshots", FeedbackSnapshot)


class ConversationEvidenceRepository:
    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def upsert(self, evidence: ConversationEvidence) -> None:
        _write(self.ws, "interactions/evidence", evidence.id, evidence)

    def get(self, evidence_id: str) -> ConversationEvidence | None:
        return _read(self.ws, "interactions/evidence", evidence_id, ConversationEvidence)

    def list_unverified(self) -> list[ConversationEvidence]:
        return [e for e in self.list_all() if not e.verified]

    def list_all(self) -> list[ConversationEvidence]:
        return _list_all(self.ws, "interactions/evidence", ConversationEvidence)

    def list_verified(self) -> list[ConversationEvidence]:
        return [e for e in self.list_all() if e.verified]

    def mark_verified(self, evidence_id: str) -> None:
        evidence = self.get(evidence_id)
        if evidence is None:
            raise KeyError(evidence_id)
        self.upsert(evidence.model_copy(update={"verified": True}))

    def list_by_interaction(self, interaction_id: str) -> list[ConversationEvidence]:
        return [e for e in self.list_all() if e.interaction_id == interaction_id]


class EngagementRunStatsRepository:
    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def upsert(self, stats: EngagementRunStats) -> None:
        _write(self.ws, "interactions/run-stats", stats.run_id, stats)

    def list_all(self) -> list[EngagementRunStats]:
        return _list_all(self.ws, "interactions/run-stats", EngagementRunStats)


class PublicationIntentRepository:
    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def save(self, intent: PublicationIntent) -> None:
        _write(self.ws, "publication-intents", f"intent_{intent.source_id}", intent)

    def get(self, source_id: str) -> PublicationIntent | None:
        return _read(self.ws, "publication-intents", f"intent_{source_id}", PublicationIntent)

    def list(self) -> list[PublicationIntent]:
        return _list_all(self.ws, "publication-intents", PublicationIntent)


class PracticeSessionRepository:
    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def upsert(self, session: PracticeSession) -> None:
        _write(self.ws, "practice", session.id, session)

    def get(self, session_id: str) -> PracticeSession | None:
        return _read(self.ws, "practice", session_id, PracticeSession)


class PeerRepository:
    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def upsert(self, profile: PeerProfile) -> None:
        _write(self.ws, "peers", profile.id, profile)

    def get(self, peer_id: str) -> PeerProfile | None:
        return _read(self.ws, "peers", peer_id, PeerProfile)

    def list_all(self) -> list[PeerProfile]:
        return _list_all(self.ws, "peers", PeerProfile)


class InteractionRecordRepository:
    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def upsert(self, record: InteractionRecord) -> None:
        _write(self.ws, "interactions/records", record.id, record)

    def get(self, record_id: str) -> InteractionRecord | None:
        return _read(self.ws, "interactions/records", record_id, InteractionRecord)

    def list_by_proposal(self, proposal_id: str) -> list[InteractionRecord]:
        return [r for r in self.list_all() if r.proposal_id == proposal_id]

    def list_by_peer(self, peer_id: str) -> list[InteractionRecord]:
        return [r for r in self.list_all() if r.peer_id == peer_id]

    def find_by_platform_message_id(
        self, platform_message_id: str
    ) -> InteractionRecord | None:
        for r in self.list_all():
            if r.platform_message_id == platform_message_id:
                return r
        return None

    def find_by_source_url(self, source_url: str) -> InteractionRecord | None:
        for r in self.list_all():
            if r.source_url == source_url:
                return r
        return None

    def list_all(self) -> list[InteractionRecord]:
        return _list_all(self.ws, "interactions/records", InteractionRecord)


class ConversationThreadRepository:
    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def upsert(self, thread: ConversationThread) -> None:
        _write(self.ws, "conversations", thread.id, thread)

    def get(self, thread_id: str) -> ConversationThread | None:
        return _read(self.ws, "conversations", thread_id, ConversationThread)

    def list_by_peer(self, peer_id: str) -> list[ConversationThread]:
        return [t for t in self.list_all() if t.peer_id == peer_id]

    def list_all(self) -> list[ConversationThread]:
        return _list_all(self.ws, "conversations", ConversationThread)
