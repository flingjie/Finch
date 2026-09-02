# tests/unit/test_storage.py
from datetime import datetime, timezone

from finch.storage.database import NodeRecord, RunRecord, Store


def test_store_roundtrip_run_and_node(tmp_path):
    db = tmp_path / "test.db"
    store = Store(db)
    store.init()

    run = RunRecord(id="run1", state="CREATED", created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc))
    store.upsert_run(run)
    assert store.get_run("run1") is not None

    node = NodeRecord(id="n1", run_id="run1", node_name="noop",
                      idempotency_key="k1", status="succeeded",
                      output_json="{}", error_code=None,
                      created_at=datetime.now(timezone.utc))
    store.upsert_node(node)
    found = store.find_node("run1", "noop", "k1")
    assert found is not None
    assert found.status == "succeeded"
