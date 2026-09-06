import pytest

from finch.content.jobs import AuthorPosition, ContentJobStatus, position_fingerprint
from finch.gate.models import InputAction, InputRequest, ProposedPosition
from finch.gate.resolve import parse_position_yaml, position_yaml, resolve_input
from finch.storage.database import Store
from finch.storage.repositories import ContentJobRepository, PositionApprovalRepository


def _store(tmp_path):
    s = Store(tmp_path / "db.sqlite")
    s.init()
    return s


def _seed(tmp_path, *, confirmed=False, change_mind_if=None):
    store = _store(tmp_path)
    jobs = ContentJobRepository(store)
    position = AuthorPosition(
        claim="c", decision="d", tradeoff="t",
        change_mind_if=change_mind_if, confirmed=confirmed,
    )
    job = _job(author_position=position)
    jobs.upsert_job(job)
    return store, jobs, PositionApprovalRepository(store), job


def _job(author_position):
    from finch.content.jobs import ContentJob, IntendedEffect, SuccessCriterion
    from finch.content.models import DraftKind

    return ContentJob(
        id="j1",
        source_card_ids=["ev1"],
        reader_problem="rp",
        audience="aud",
        intended_effect=IntendedEffect(understand="u"),
        author_position=author_position,
        success_criteria=[SuccessCriterion(id="c1", description="d", measurement="critic")],
        recommended_format=DraftKind.REPLY,
        status=ContentJobStatus.READY,
    )


def _request(job):
    p = job.author_position
    return InputRequest(
        run_id="r1", job_id="j1", topic="t",
        proposed_position=ProposedPosition(
            claim=p.claim, decision=p.decision, tradeoff=p.tradeoff,
            change_mind_if=p.change_mind_if,
        ),
        evidence_card_ids=["ev1"],
    )


def test_confirm_sets_confirmed_and_approves(tmp_path):
    store, jobs, approvals, job = _seed(tmp_path)
    msg = resolve_input(
        _request(job), InputAction.CONFIRM, jobs_repo=jobs, approvals_repo=approvals
    )
    assert "confirmed" in msg
    assert jobs.get_job("j1").author_position.confirmed is True
    assert approvals.find_active(position_fingerprint(job.author_position)) is not None


def test_edit_revokes_old_approves_new(tmp_path):
    store, jobs, approvals, job = _seed(tmp_path)
    approvals.approve(position_fingerprint(job.author_position), "j1")
    edited = ProposedPosition(claim="new", decision="d", tradeoff="t")
    msg = resolve_input(
        _request(job), InputAction.EDIT, jobs_repo=jobs, approvals_repo=approvals,
        edited_position=edited,
    )
    assert "edited" in msg
    assert jobs.get_job("j1").author_position.claim == "new"
    assert approvals.find_active(position_fingerprint(job.author_position)) is None
    assert approvals.find_active(position_fingerprint(
        AuthorPosition(claim="new", decision="d", tradeoff="t")
    )) is not None


def test_skip_marks_do_not_write(tmp_path):
    store, jobs, approvals, job = _seed(tmp_path)
    msg = resolve_input(
        _request(job), InputAction.SKIP, jobs_repo=jobs, approvals_repo=approvals,
        skip_reason="not relevant",
    )
    assert "skipped" in msg
    updated = jobs.get_job("j1")
    assert updated.status == ContentJobStatus.DO_NOT_WRITE
    assert updated.reject_reason == "not relevant"


def test_stop_marks_all_active_do_not_write(tmp_path):
    store, jobs, approvals, job = _seed(tmp_path)
    msg = resolve_input(_request(job), InputAction.STOP, jobs_repo=jobs, approvals_repo=approvals)
    assert "stopped" in msg
    assert jobs.get_job("j1").status == ContentJobStatus.DO_NOT_WRITE


def test_confirm_incomplete_position_raises(tmp_path):
    store, jobs, approvals, job = _seed(tmp_path)
    jobs.upsert_job(job.model_copy(update={"author_position": None}))
    request = InputRequest(
        run_id="r1", job_id="j1", topic="t", proposed_position=ProposedPosition(),
    )
    with pytest.raises(ValueError):
        resolve_input(request, InputAction.CONFIRM, jobs_repo=jobs, approvals_repo=approvals)


def test_skip_without_reason_raises(tmp_path):
    store, jobs, approvals, job = _seed(tmp_path)
    with pytest.raises(ValueError):
        resolve_input(_request(job), InputAction.SKIP, jobs_repo=jobs, approvals_repo=approvals)


def test_position_yaml_roundtrip():
    p = ProposedPosition(claim="c", decision="d", tradeoff="t", change_mind_if="x")
    assert parse_position_yaml(position_yaml(p)) == p
