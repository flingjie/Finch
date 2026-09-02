from finch.graph.state import GraphState, advance, can_transition


def test_linear_advance_follows_main_chain():
    assert advance(GraphState.CREATED) == GraphState.PREFLIGHT_PASSED
    assert advance(GraphState.DRAFTED) == GraphState.CRITIQUED


def test_advance_past_review_reaches_waits():
    assert advance(GraphState.EVIDENCE_MATCHED) == GraphState.DRAFTED


def test_terminal_states_are_stable():
    for s in (GraphState.COMPLETED, GraphState.FAILED, GraphState.BLOCKED):
        assert s.is_terminal
        assert advance(s) == s


def test_skipped_advances_to_published():
    assert advance(GraphState.SKIPPED) == GraphState.PUBLISHED


def test_abnormal_flag():
    assert GraphState.FAILED.is_abnormal
    assert not GraphState.CREATED.is_abnormal


def test_can_transition_to_abnormal_from_anywhere():
    assert can_transition(GraphState.CREATED, GraphState.FAILED)
    assert can_transition(GraphState.DRAFTED, GraphState.BLOCKED)


def test_can_transition_review_fork():
    assert can_transition(GraphState.WAITING_FOR_REVIEW, GraphState.APPROVED)
    assert can_transition(GraphState.WAITING_FOR_REVIEW, GraphState.SKIPPED)
