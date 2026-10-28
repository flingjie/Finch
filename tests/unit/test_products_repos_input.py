"""repos → 产品候选适配（inputs_repos.py）单测。"""

from __future__ import annotations

from datetime import UTC, datetime

from finch.products.inputs_repos import products_from_latest_repos_run
from finch.repos.models import DiscoveryRun, RepoRecord, RunStatus
from finch.repos.repository import RepoDiscoveryRepository
from finch.storage.workspace import Workspace


def test_maps_latest_repos_run_to_products(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    store = RepoDiscoveryRepository(ws)
    run_id = "run1"
    store.save_run(
        DiscoveryRun(
            run_id=run_id,
            window_start=datetime(2026, 10, 1, tzinfo=UTC),
            window_end=datetime(2026, 10, 2, tzinfo=UTC),
            status=RunStatus.COMPLETED,
        )
    )
    store.save_repo(
        run_id,
        RepoRecord(
            key="acme/widget",
            owner="acme",
            name="widget",
            description="an agent harness",
            stars=10,
            source_share_urls=["https://x.com/a/status/1"],
        ),
    )

    clusters = products_from_latest_repos_run(ws)
    assert len(clusters) == 1
    product = clusters[0].product
    assert product.key == "github.com/acme/widget"
    assert product.name == "widget"
    assert product.founder is not None
    assert product.founder.display_name == "acme"


def test_empty_when_no_repos_run(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    assert products_from_latest_repos_run(ws) == []
