"""产品领域文件仓储：镜像 ``storage/repositories.py`` 的读写模式，落到新目录避免与旧数据冲突。"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from finch.products.models import FeedbackReport, Product, ProductSnapshot, Solution
from finch.storage.workspace import Workspace


def _write(ws: Workspace, name: str, key: str, model) -> None:
    ws.write_yaml(ws.dir(name) / f"{ws.safe_filename(key)}.yaml", model)


def _read[T: BaseModel](ws: Workspace, name: str, key: str, model_cls: type[T]) -> T | None:
    return ws.read_yaml(ws.dir(name) / f"{ws.safe_filename(key)}.yaml", model_cls)


def _list_all[T: BaseModel](ws: Workspace, name: str, model_cls: type[T]) -> list[T]:
    out: list[T] = []
    for path in sorted(ws.dir(name).glob("*.yaml")):
        obj = ws.read_yaml(path, model_cls)
        if obj is not None:
            out.append(obj)
    return out


class ProductRepository:
    """``var/products/<id>.yaml``（幂等 upsert）。"""

    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def upsert(self, product: Product) -> None:
        _write(self.ws, "products", product.id, product)

    def get(self, product_id: str) -> Product | None:
        return _read(self.ws, "products", product_id, Product)

    def list_all(self) -> list[Product]:
        return _list_all(self.ws, "products", Product)


class SolutionRepository:
    """``var/products/<product_id>/solution.yaml``（每产品一份，幂等）。"""

    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def _path(self, product_id: str) -> Path:
        return self.ws.dir(f"products/{self.ws.safe_filename(product_id)}") / "solution.yaml"

    def upsert(self, solution: Solution) -> None:
        self.ws.write_yaml(self._path(solution.product_id), solution)

    def get(self, product_id: str) -> Solution | None:
        path = self._path(product_id)
        return self.ws.read_yaml(path, Solution)


class FeedbackRepository:
    """``var/products/<product_id>/feedback/``（raw.jsonl + report.yaml，幂等）。"""

    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def _dir(self, product_id: str) -> Path:
        return self.ws.dir(f"products/{self.ws.safe_filename(product_id)}/feedback")

    def _report_path(self, product_id: str) -> Path:
        return self._dir(product_id) / "report.yaml"

    def _raw_path(self, product_id: str) -> Path:
        return self._dir(product_id) / "raw.jsonl"

    def upsert(self, report: FeedbackReport) -> None:
        self.ws.write_yaml(self._report_path(report.product_id), report)

    def get(self, product_id: str) -> FeedbackReport | None:
        return self.ws.read_yaml(self._report_path(product_id), FeedbackReport)

    def append_raw(self, product_id: str, entry: dict) -> None:
        self.ws.append_jsonl(self._raw_path(product_id), entry)

    def clear_raw(self, product_id: str) -> None:
        """重搜前清空审计日志，避免跨轮累积重复候选。"""
        self._raw_path(product_id).unlink(missing_ok=True)

    def read_raw(self, product_id: str) -> list[dict]:
        return self.ws.read_jsonl(self._raw_path(product_id))


class ProductSnapshotRepository:
    """``var/products/snapshots/<id>.yaml``。"""

    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def upsert(self, snapshot: ProductSnapshot) -> None:
        _write(self.ws, "products/snapshots", snapshot.id, snapshot)

    def get(self, snapshot_id: str) -> ProductSnapshot | None:
        return _read(self.ws, "products/snapshots", snapshot_id, ProductSnapshot)

    def latest(self) -> ProductSnapshot | None:
        items = sorted(self.list_all(), key=lambda s: s.created_at, reverse=True)
        return items[0] if items else None

    def list_all(self) -> list[ProductSnapshot]:
        return _list_all(self.ws, "products/snapshots", ProductSnapshot)
