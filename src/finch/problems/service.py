"""ProblemService：活跃问题状态机（open → closed；≤3 open）。"""

from datetime import UTC, datetime

from finch.problems.models import ActiveProblem, problem_id_for
from finch.storage.repositories import ProblemRepository


class ProblemService:
    """驱动活跃问题生命周期；不调用 LLM、不访问 DB（依赖注入 repository）。"""

    def __init__(self, problems: ProblemRepository) -> None:
        self.problems = problems

    def add(self, *, title: str, why_it_matters: str = "") -> ActiveProblem:
        """新建（幂等）；open 数 ≥ 3 时拒绝，提示先 close 一个。"""
        pid = problem_id_for(title)
        existing = self.problems.get(pid)
        if existing is not None:
            return existing
        if sum(1 for p in self.problems.list_all() if p.status == "open") >= 3:
            raise ValueError("too many open problems (max 3); close one first")
        now = datetime.now(UTC)
        problem = ActiveProblem(
            id=pid,
            title=title.strip(),
            why_it_matters=why_it_matters,
            status="open",
            created_at=now,
            updated_at=now,
        )
        self.problems.upsert(problem)
        return problem

    def list(self, *, status: str | None = None) -> list[ActiveProblem]:
        problems = self.problems.list_all()
        if status is None or status == "all":
            return problems
        return [p for p in problems if p.status == status]

    def show(self, problem_id: str) -> ActiveProblem:
        problem = self.problems.get(problem_id)
        if problem is None:
            raise KeyError(problem_id)
        return problem

    def close(self, problem_id: str, *, reason: str = "") -> ActiveProblem:
        """open → closed（幂等）；记 closed_reason + closed_at。"""
        problem = self.show(problem_id)
        if problem.status == "closed":
            return problem
        now = datetime.now(UTC)
        problem = problem.model_copy(
            update={
                "status": "closed",
                "closed_reason": reason,
                "closed_at": now,
                "updated_at": now,
            }
        )
        self.problems.upsert(problem)
        return problem

    def append_attempt(self, problem_id: str, attempt_id: str) -> ActiveProblem:
        """append-only 回链 PracticeAttempt；重复 id 不追加。"""
        problem = self.show(problem_id)
        if attempt_id in problem.attempt_ids:
            return problem
        problem = problem.model_copy(
            update={
                "attempt_ids": [*problem.attempt_ids, attempt_id],
                "updated_at": datetime.now(UTC),
            }
        )
        self.problems.upsert(problem)
        return problem
