"""把 ``finch repos`` 最新一轮仓库发现映射为产品候选（次要输入，由配置门控）。

``repos/`` 是产品相关且只读的既有能力，保留为产品发现输入；这里只做适配，不改其内部。
"""

from __future__ import annotations

from datetime import UTC, datetime

from finch.products.ids import founder_id_for, product_id_for
from finch.products.models import (
    EvidenceStatus,
    Founder,
    FounderHandle,
    Product,
    ProductEvidence,
)
from finch.products.projector import ProductCluster
from finch.repos.repository import RepoDiscoveryRepository
from finch.storage.workspace import Workspace


def products_from_latest_repos_run(ws: Workspace) -> list[ProductCluster]:
    """把最新一轮仓库记录映射为产品候选；无运行或运行无仓库 → 空列表。"""
    repo_repo = RepoDiscoveryRepository(ws)
    run_ids = repo_repo.list_run_ids()
    if not run_ids:
        return []
    repos = repo_repo.list_repos(run_ids[-1])

    clusters: list[ProductCluster] = []
    for repo in repos:
        key = f"github.com/{repo.key}"  # 与 urls.normalize_product_key 的 GitHub 键一致
        founder = Founder(
            founder_id=founder_id_for("github", repo.owner),
            display_name=repo.owner,
            handles=[FounderHandle(platform="github", handle=repo.owner)],
            first_seen_at=datetime.now(UTC),
        )
        share_urls = list(repo.source_share_urls)
        product = Product(
            id=product_id_for(key),
            key=key,
            name=repo.name,
            tagline=repo.description or "",
            founder=founder,
            evidence=[
                ProductEvidence(
                    source_ref=u,
                    url=u,
                    quote=repo.description or "",
                    status=EvidenceStatus.INFERRED,
                )
                for u in share_urls
            ],
            source_refs=share_urls,
            # 仓库明星数只作上下文，不参与确定性产品信号总分；给一个中性的种子分。
            score_total=0.5,
            score_dimensions={"github_stars": float(repo.stars or 0)},
            fingerprint=f"github:{repo.key}",
            first_seen_at=datetime.now(UTC),
            last_seen_at=datetime.now(UTC),
        )
        clusters.append(ProductCluster(product=product, artifacts=[]))
    return clusters
