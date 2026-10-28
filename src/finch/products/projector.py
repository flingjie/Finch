"""工件 → 产品候选投影：按产品 key 聚类，从作者身份派生创业者。

``ProductCluster`` 携带聚类内的原始工件，供后续确定性打分；无工件的输入（如 repos）
用空 ``artifacts`` 表示。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime

from finch.products.ids import founder_id_for, product_id_for
from finch.products.models import (
    EvidenceStatus,
    Founder,
    FounderHandle,
    Product,
    ProductEvidence,
)
from finch.products.urls import product_key_for
from finch.sources.models import RawArtifact

_TEXT_LIMIT = 2000
_VALID_PLATFORMS = {"x", "reddit", "github", "v2ex", "weixin", "xiaohongshu", "web"}


def _truncate(text: str, n: int = _TEXT_LIMIT) -> str:
    text = (text or "").strip()
    if len(text) <= n:
        return text
    return text[: n - 1] + "…"


def _coerce_platform(platform: str) -> str:
    return platform if platform in _VALID_PLATFORMS else "web"


@dataclass
class ProductCluster:
    product: Product
    artifacts: list[RawArtifact] = field(default_factory=list)


def cluster_fingerprint(arts: list[RawArtifact]) -> str:
    keys = sorted(
        a.content_fingerprint
        or hashlib.sha256((a.text or "").encode("utf-8")).hexdigest()
        for a in arts
    )
    return hashlib.sha256("\n".join(keys).encode("utf-8")).hexdigest()[:16]


class ProductProjector:
    """把过滤后的原始工件聚类为产品候选。"""

    def build_product_candidates(self, artifacts: list[RawArtifact]) -> list[ProductCluster]:
        grouped: dict[str, list[RawArtifact]] = {}
        for art in artifacts:
            key = product_key_for(art)
            grouped.setdefault(key, []).append(art)

        clusters: list[ProductCluster] = []
        for key, arts in grouped.items():
            clusters.append(self._cluster(key, arts))
        return clusters

    def _cluster(self, key: str, arts: list[RawArtifact]) -> ProductCluster:
        first = arts[0]
        author = first.author_identity
        platform = _coerce_platform((author.platform or "").strip() or "web")
        external_id = author.external_id or author.handle or ""
        founder = Founder(
            founder_id=founder_id_for(platform, external_id) if external_id else "",
            display_name=author.handle or author.external_id or "",
            handles=[
                FounderHandle(
                    platform=platform,  # type: ignore[arg-type]
                    external_id=author.external_id,
                    handle=author.handle,
                )
            ],
            source_refs=[a.artifact_id for a in arts],
            first_seen_at=datetime.now(UTC),
        )
        product = Product(
            id=product_id_for(key),
            key=key,
            founder=founder if external_id else None,
            evidence=[
                ProductEvidence(
                    source_ref=a.artifact_id,
                    url=a.canonical_url,
                    quote=_truncate(a.text),
                    status=EvidenceStatus.INFERRED,
                )
                for a in arts
            ],
            source_refs=[a.artifact_id for a in arts],
            fingerprint=cluster_fingerprint(arts),
            first_seen_at=datetime.now(UTC),
            last_seen_at=datetime.now(UTC),
        )
        return ProductCluster(product=product, artifacts=arts)
