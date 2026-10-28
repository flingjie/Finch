"""产品卡片服务：对产品聚类批量调用 LLM 填卡片字段（无总分）。

镜像 ``CreatorEvidenceService``：每批一次调用，批级 fail-soft；LLM 永不写 ``score_total``。
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

from finch.llm.base import StructuredInferenceRunner
from finch.products.models import Product
from finch.products.repository import ProductRepository
from finch.storage.workspace import Workspace

_BATCH_PROMPT_PATH = Path("prompts/product-card-batch.md")
_CARD_VERSION = "product-card-batch-v1"
_MAX_CANDIDATES = 20
_BATCH_SIZE = 5


class ProductCardItem(BaseModel):
    """LLM 输出项（无 score/total）。"""

    key: str
    name: str = ""
    tagline: str = ""
    founder_name: str = ""
    target_user: str = ""
    problem_solved: str = ""
    core_solution: str = ""
    differentiation: str = ""
    progress: str = ""
    why_worth_attention: str = ""


class ProductCardBatchOutput(BaseModel):
    """一次批量调用返回多个产品的卡片字段。"""

    products: list[ProductCardItem] = Field(default_factory=list)


def _chunks(seq: list, n: int) -> list[list]:
    return [seq[i : i + n] for i in range(0, len(seq), n)]


def _products_payload(products: list[Product]) -> str:
    rows = [
        {
            "key": p.key,
            "name": p.name,
            "founder": p.founder.display_name if p.founder else "",
            "evidence": [
                {"url": e.url, "quote": e.quote} for e in p.evidence if e.quote or e.url
            ],
        }
        for p in products
    ]
    return json.dumps(rows, ensure_ascii=False, indent=2)


class ProductCardService:
    def __init__(
        self,
        workspace: Workspace,
        *,
        runner: StructuredInferenceRunner | None = None,
        max_candidates: int = _MAX_CANDIDATES,
        batch_size: int = _BATCH_SIZE,
    ) -> None:
        self.ws = workspace
        self.runner = runner
        self.max_candidates = max_candidates
        self.batch_size = batch_size
        self.repo = ProductRepository(workspace)

    def fill(self, products: list[Product]) -> int:
        """批量填卡片字段并落库；返回已保存产品数。失败批跳过，不阻断。"""
        if self.runner is None or not products:
            return 0

        saved = 0
        for batch in _chunks(products[: self.max_candidates], self.batch_size):
            try:
                saved += self._fill_batch(batch)
            except Exception:  # noqa: BLE001
                continue
        return saved

    def _fill_batch(self, batch: list[Product]) -> int:
        assert self.runner is not None
        by_key = {p.key: p for p in batch}
        prompt = _BATCH_PROMPT_PATH.read_text().format(products=_products_payload(batch))
        output = self.runner.run(prompt, ProductCardBatchOutput)
        assert isinstance(output, ProductCardBatchOutput)
        saved = 0
        for item in output.products:
            product = by_key.get(item.key)
            if product is None:
                continue
            updated = product.model_copy(
                update={
                    "name": item.name or product.name,
                    "tagline": item.tagline,
                    "target_user": item.target_user,
                    "problem_solved": item.problem_solved,
                    "core_solution": item.core_solution,
                    "differentiation": item.differentiation,
                    "progress": item.progress,
                    "why_worth_attention": item.why_worth_attention,
                    "card_version": _CARD_VERSION,
                }
            )
            if item.founder_name and updated.founder is not None:
                updated.founder = updated.founder.model_copy(
                    update={"display_name": item.founder_name}
                )
            self.repo.upsert(updated)
            saved += 1
        return saved
