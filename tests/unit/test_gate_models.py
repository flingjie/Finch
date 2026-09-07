from finch.gate.models import (
    InputAction,
    InputRequest,
    ProposedPosition,
)


def test_proposed_position_complete():
    assert ProposedPosition(claim="c", decision="d", tradeoff="t").complete() is True
    assert ProposedPosition().complete() is False
    assert ProposedPosition(claim="c", decision="d").complete() is False


def test_input_request_serializes_to_json():
    request = InputRequest(
        run_id="r1",
        job_id="j1",
        topic="topic",
        proposed_position=ProposedPosition(claim="c", decision="d", tradeoff="t"),
    )
    data = request.model_dump(mode="json")
    assert data["type"] == "author_position_confirmation"
    assert data["actions"] == [a.value for a in InputAction]
    assert InputRequest.model_validate(data) == request
