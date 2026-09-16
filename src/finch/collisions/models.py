"""跨领域碰撞与小实验领域模型。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field


class CollisionCard(BaseModel):
    """结构相似碰撞（非表面类比）。"""

    collision_id: str
    their_domain: str
    your_domain: str
    surface_similarity: str
    structural_similarity: str
    shared_question: str
    transferable_mechanism: str
    falsifiable_hypothesis: str
    artifact_ids: list[str] = Field(default_factory=list)
    person_id: str = ""
    peer_id: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ExperimentStatus(StrEnum):
    PLANNED = "planned"
    RUNNING = "running"
    COMPLETED = "completed"
    STOPPED = "stopped"
    FAILED = "failed"


class MicroExperiment(BaseModel):
    """一周内可完成的小实验。"""

    experiment_id: str
    collision_id: str
    hypothesis: str
    minimal_action: str
    observation_plan: str
    stop_condition: str
    status: ExperimentStatus = ExperimentStatus.PLANNED
    started_at: datetime | None = None
    due_at: datetime | None = None
    outcome: str = ""
    evidence_notes: list[str] = Field(default_factory=list)
    result_kind: Literal["method", "artifact", "failure_review", ""] = ""


def build_collision(
    *,
    their_domain: str,
    your_domain: str,
    surface_similarity: str,
    structural_similarity: str,
    shared_question: str,
    transferable_mechanism: str,
    falsifiable_hypothesis: str,
    artifact_ids: list[str],
    person_id: str = "",
    peer_id: str = "",
) -> CollisionCard | None:
    """至少两个来源证据；必须有结构相似与可证伪假设。"""
    if len(artifact_ids) < 2:
        return None
    if not structural_similarity.strip() or not falsifiable_hypothesis.strip():
        return None
    # Reject pure surface analogy without structural content
    if structural_similarity.strip().lower() == surface_similarity.strip().lower():
        return None
    return CollisionCard(
        collision_id=f"col_{uuid4().hex[:12]}",
        their_domain=their_domain,
        your_domain=your_domain,
        surface_similarity=surface_similarity,
        structural_similarity=structural_similarity,
        shared_question=shared_question,
        transferable_mechanism=transferable_mechanism,
        falsifiable_hypothesis=falsifiable_hypothesis,
        artifact_ids=list(artifact_ids),
        person_id=person_id,
        peer_id=peer_id,
    )


def start_experiment(
    collision: CollisionCard,
    *,
    minimal_action: str,
    observation_plan: str,
    stop_condition: str,
    now: datetime | None = None,
) -> MicroExperiment:
    """从碰撞启动一周内实验。"""
    clock = now or datetime.now(UTC)
    return MicroExperiment(
        experiment_id=f"exp_{uuid4().hex[:12]}",
        collision_id=collision.collision_id,
        hypothesis=collision.falsifiable_hypothesis,
        minimal_action=minimal_action,
        observation_plan=observation_plan,
        stop_condition=stop_condition,
        status=ExperimentStatus.RUNNING,
        started_at=clock,
        due_at=clock + timedelta(days=7),
    )


def complete_experiment(
    experiment: MicroExperiment,
    *,
    outcome: str,
    result_kind: Literal["method", "artifact", "failure_review"],
    evidence_notes: list[str] | None = None,
    stopped: bool = False,
) -> MicroExperiment:
    return experiment.model_copy(
        update={
            "status": ExperimentStatus.STOPPED
            if stopped
            else (
                ExperimentStatus.FAILED
                if result_kind == "failure_review"
                else ExperimentStatus.COMPLETED
            ),
            "outcome": outcome,
            "result_kind": result_kind,
            "evidence_notes": list(evidence_notes or []),
        }
    )


def pick_weekly_collision(cards: list[CollisionCard]) -> CollisionCard | None:
    """每周只深度处理一个最值得验证的碰撞（假说最可证伪优先）。"""
    if not cards:
        return None
    return max(cards, key=lambda c: (len(c.falsifiable_hypothesis), len(c.artifact_ids)))
