"""PracticeAttemptService：实践尝试状态机（open → verified / closed）。"""

from datetime import UTC, datetime

from finch.practice.attempts import PracticeAttempt, attempt_id_for
from finch.storage.repositories import PracticeAttemptRepository, ProblemRepository


class PracticeAttemptService:
    """驱动实践尝试生命周期；确定性状态转换，不调用 LLM。"""

    def __init__(
        self,
        attempts: PracticeAttemptRepository,
        problems: ProblemRepository,
    ) -> None:
        self.attempts = attempts
        self.problems = problems

    def add(
        self,
        *,
        problem_id: str | None = None,
        problem: str,
        attempt: str,
        observation: str,
        unknown: str = "",
        next_step: str = "",
        source_refs: list[str] | None = None,
    ) -> PracticeAttempt:
        """新建（幂等）；给 problem_id 时回链到 ActiveProblem.attempt_ids。"""
        if problem_id is not None and self.problems.get(problem_id) is None:
            raise ValueError(f"problem not found: {problem_id}")
        aid = attempt_id_for(problem.strip(), attempt, observation)
        existing = self.attempts.get(aid)
        if existing is not None:
            return existing
        now = datetime.now(UTC)
        obj = PracticeAttempt(
            id=aid,
            problem_id=problem_id,
            problem=problem.strip(),
            attempt=attempt,
            observation=observation,
            unknown=unknown,
            next_step=next_step,
            source_refs=list(source_refs or []),
            created_at=now,
            updated_at=now,
        )
        self.attempts.upsert(obj)
        if problem_id is not None:
            self._append_attempt(problem_id, aid)
        return obj

    def list(self, *, status: str | None = None) -> list[PracticeAttempt]:
        attempts = self.attempts.list_all()
        if status is None or status == "all":
            return attempts
        return [a for a in attempts if a.status == status]

    def show(self, attempt_id: str) -> PracticeAttempt:
        attempt = self.attempts.get(attempt_id)
        if attempt is None:
            raise KeyError(attempt_id)
        return attempt

    def verify(self, attempt_id: str, *, result: str) -> PracticeAttempt:
        """open → verified，回填 result；唯一把未验证标为已验证的路径。"""
        attempt = self.show(attempt_id)
        if attempt.status != "open":
            raise ValueError(
                f"illegal transition: attempt {attempt_id} in status {attempt.status}; "
                "only open -> verified is legal"
            )
        attempt = attempt.model_copy(
            update={"status": "verified", "result": result, "updated_at": datetime.now(UTC)}
        )
        self.attempts.upsert(attempt)
        return attempt

    def close(self, attempt_id: str) -> PracticeAttempt:
        """置 closed（弃置一条素材）；幂等。"""
        attempt = self.show(attempt_id)
        if attempt.status == "closed":
            return attempt
        attempt = attempt.model_copy(
            update={"status": "closed", "updated_at": datetime.now(UTC)}
        )
        self.attempts.upsert(attempt)
        return attempt

    def _append_attempt(self, problem_id: str, attempt_id: str) -> None:
        problem = self.problems.get(problem_id)
        if problem is None or attempt_id in problem.attempt_ids:
            return
        problem = problem.model_copy(
            update={
                "attempt_ids": [*problem.attempt_ids, attempt_id],
                "updated_at": datetime.now(UTC),
            }
        )
        self.problems.upsert(problem)
