"""Gate 解析与决策落地：把一次作者决策落到 job/approval（不负责 replay）。"""

import yaml

from finch.content.jobs import AuthorPosition, ContentJobStatus, position_fingerprint
from finch.storage.repositories import ContentJobRepository, PositionApprovalRepository

from .models import InputAction, InputRequest, ProposedPosition


def _to_author_position(position: ProposedPosition, confirmed: bool = True) -> AuthorPosition:
    return AuthorPosition(
        claim=position.claim,
        decision=position.decision,
        tradeoff=position.tradeoff,
        change_mind_if=position.change_mind_if,
        confirmed=confirmed,
    )


def position_yaml(position: ProposedPosition) -> str:
    """把立场渲染成预填 YAML（供 --edit 编辑器）。"""
    return yaml.safe_dump(
        {
            "claim": position.claim,
            "decision": position.decision,
            "tradeoff": position.tradeoff,
            "change_mind_if": position.change_mind_if,
        },
        sort_keys=False,
        allow_unicode=True,
    )


def parse_position_yaml(text: str) -> ProposedPosition:
    """从 YAML 文本解析立场；非法输入抛 ValueError。"""
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError("position file is not a YAML mapping")
    return ProposedPosition(
        claim=str(data.get("claim") or ""),
        decision=str(data.get("decision") or ""),
        tradeoff=str(data.get("tradeoff") or ""),
        change_mind_if=data.get("change_mind_if"),
    )


def resolve_input(
    request: InputRequest,
    action: InputAction,
    *,
    jobs_repo: ContentJobRepository,
    approvals_repo: PositionApprovalRepository | None = None,
    edited_position: ProposedPosition | None = None,
    skip_reason: str | None = None,
) -> str:
    """把一次决策落到 job/approval 上，返回人类可读摘要。不负责 replay。"""
    job = jobs_repo.get_job(request.job_id)
    if job is None:
        raise ValueError(f"job not found: {request.job_id}")
    proposed_fp = position_fingerprint(
        _to_author_position(request.proposed_position, confirmed=False)
    )

    if action is InputAction.CONFIRM:
        position = job.author_position
        if (
            position is None
            or not position.claim
            or not position.decision
            or not position.tradeoff
        ):
            raise ValueError("position incomplete; use --edit or --file")
        confirmed = position.model_copy(update={"confirmed": True})
        jobs_repo.upsert_job(job.model_copy(update={"author_position": confirmed}))
        if approvals_repo is not None:
            approvals_repo.approve(position_fingerprint(confirmed), request.job_id)
        return f"confirmed {request.job_id}"

    if action is InputAction.EDIT:
        if edited_position is None or not edited_position.complete():
            raise ValueError("edited position incomplete")
        position = _to_author_position(edited_position, confirmed=True)
        jobs_repo.upsert_job(job.model_copy(update={"author_position": position}))
        if approvals_repo is not None:
            new_fp = position_fingerprint(position)
            if new_fp != proposed_fp:
                approvals_repo.revoke(proposed_fp)
            approvals_repo.approve(new_fp, request.job_id)
        return f"edited {request.job_id}"

    if action is InputAction.SKIP:
        if not skip_reason:
            raise ValueError("--skip requires --reason")
        jobs_repo.upsert_job(
            job.model_copy(
                update={
                    "status": ContentJobStatus.DO_NOT_WRITE,
                    "reject_reason": skip_reason,
                }
            )
        )
        if approvals_repo is not None:
            approvals_repo.revoke(proposed_fp)
        return f"skipped {request.job_id}"

    if action is InputAction.STOP:
        for existing in jobs_repo.list_jobs():
            if existing.status != ContentJobStatus.DO_NOT_WRITE:
                jobs_repo.upsert_job(
                    existing.model_copy(
                        update={
                            "status": ContentJobStatus.DO_NOT_WRITE,
                            "reject_reason": "stopped original track",
                        }
                    )
                )
        if approvals_repo is not None:
            approvals_repo.revoke(proposed_fp)
        return "stopped original track"

    raise ValueError(f"unsupported action: {action}")
