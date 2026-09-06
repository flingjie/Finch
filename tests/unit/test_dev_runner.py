import json

import pytest

from finch.dev.runner import run_node
from finch.settings import Settings


def _recall_fixture(tmp_path):
    p = tmp_path / "recall.json"
    p.write_text(
        json.dumps(
            {
                "candidates": {
                    "items": [
                        {
                            "id": "c1",
                            "author_handle": "a",
                            "text": "agent harness reliability",
                            "url": "u",
                        },
                    ]
                },
                "evidence_cards": {
                    "items": [
                        {
                            "id": "ev1",
                            "event_id": "evt1",
                            "claim": "agent harness reliability matters",
                            "confidence": "SUPPORTED",
                        },
                    ]
                },
            }
        )
    )
    return p


def test_run_node_recall_succeeds(tmp_path):
    result = run_node("recall", _recall_fixture(tmp_path), Settings())
    assert result.status == "succeeded"
    assert result.output["items"]  # ranked_candidates 非空


def test_run_node_missing_input_raises(tmp_path):
    p = tmp_path / "empty.json"
    p.write_text("{}")
    with pytest.raises(ValueError):
        run_node("recall", p, Settings())


def test_run_node_function_rejected(tmp_path):
    with pytest.raises(ValueError):
        run_node("select_groups", tmp_path / "x.json", Settings())


def test_run_node_unknown_feature(tmp_path):
    with pytest.raises(KeyError):
        run_node("nope", tmp_path / "x.json", Settings())
