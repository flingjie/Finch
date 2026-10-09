"""风格观察：从多次练习中积累候选观察，用户确认后才进入 voice profile。

聚合是确定性的（按练习维度 + 所选写法分组，≥3 个已完成会话触发）；特点文案由分组键
直接生成，证据用会话内用户原句。观察不自动写画像，确认后由用户走 ``finch voice`` 流程。
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from finch.practice.models import (
    ObservationStatus,
    PracticeSession,
    StyleObservation,
    StyleObservationEvidence,
)
from finch.storage.repositories import PracticeSessionRepository, StyleObservationRepository

_TRIGGER_MIN = 3


class StyleObservationService:
    """从已完成练习会话提出候选风格观察，并管理确认状态。"""

    def __init__(
        self,
        sessions: PracticeSessionRepository,
        observations: StyleObservationRepository,
    ) -> None:
        self.sessions = sessions
        self.observations = observations

    def propose(self, *, force: bool = False) -> list[StyleObservation]:
        """确定性聚合：按 (practice_dimension, 所选写法) 分组，≥3 个触发候选观察。

        已存在（含 pending/accepted/rejected）的观察默认跳过，``force`` 时覆盖。
        rejected 观察保留在存储中，不删除，避免反复提出同一判断。
        """
        groups: dict[tuple[str, str], list[PracticeSession]] = {}
        for s in self.sessions.list_all():
            if s.status != "finished":
                continue
            key = self._group_key(s)
            if key is None:
                continue
            groups.setdefault(key, []).append(s)

        proposed: list[StyleObservation] = []
        for (dimension, label), group in groups.items():
            if len(group) < _TRIGGER_MIN:
                continue
            obs_id = self._id_for(dimension, label)
            if not force and self.observations.get(obs_id) is not None:
                continue
            obs = StyleObservation(
                id=obs_id,
                characteristic=self._characteristic(dimension, label),
                evidence=[
                    StyleObservationEvidence(session_id=s.id, quote=self._quote(s))
                    for s in group
                ],
                possible_from_prompt="可能来自当次练习任务要求，需人工确认",
                status="pending",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            self.observations.upsert(obs)
            proposed.append(obs)
        return proposed

    def list_all(self) -> list[StyleObservation]:
        return self.observations.list_all()

    def accept(self, obs_id: str) -> StyleObservation:
        return self._set_status(obs_id, "accepted")

    def reject(self, obs_id: str) -> StyleObservation:
        return self._set_status(obs_id, "rejected")

    def correct(self, obs_id: str, note: str) -> StyleObservation:
        obs = self._require(obs_id)
        note = note.strip()
        counter = obs.counterexamples
        if note:
            counter = f"{counter}\n{note}" if counter else note
        obs = obs.model_copy(
            update={
                "status": "corrected",
                "counterexamples": counter,
                "updated_at": datetime.now(UTC),
            }
        )
        self.observations.upsert(obs)
        return obs

    def _set_status(self, obs_id: str, status: ObservationStatus) -> StyleObservation:
        obs = self._require(obs_id)
        obs = obs.model_copy(update={"status": status, "updated_at": datetime.now(UTC)})
        self.observations.upsert(obs)
        return obs

    def _require(self, obs_id: str) -> StyleObservation:
        obs = self.observations.get(obs_id)
        if obs is None:
            raise KeyError(obs_id)
        return obs

    def _group_key(self, session: PracticeSession) -> tuple[str, str] | None:
        """返回 (维度, 写法标签)；无法确定时返回 None。"""
        if (
            session.selected_option is not None
            and 0 <= session.selected_option < len(session.options)
        ):
            label = session.options[session.selected_option].name
            return (session.practice_dimension, label)
        if session.method_id:
            return (session.practice_dimension, f"method:{session.method_id}")
        return None

    def _characteristic(self, dimension: str, label: str) -> str:
        dim = dimension or "未标维度"
        return f"反复选择以「{dim}」维度、用「{label}」写法展开"

    def _quote(self, session: PracticeSession) -> str:
        """风格证据只引用可追溯的用户创作片段：混合/AI 示例时退回首稿。"""
        if session.final_source == "user_authored":
            return session.final_expression or session.initial_attempt
        return session.initial_attempt

    @staticmethod
    def _id_for(dimension: str, label: str) -> str:
        raw = f"{dimension}\x1f{label}".encode()
        return "sobs_" + hashlib.sha256(raw).hexdigest()[:8]
