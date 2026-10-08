"""MindMapService：结构化推理生成/增长发散思维导图。

确定性字段（id、source_type/source_ref/content_hash、节点 id/parent_id/expanded、
组合 node_a/node_b）由代码覆盖，不信任模型输出。
"""

import hashlib
from pathlib import Path
from typing import cast

from finch.angle_discovery.mindmap_models import (
    MindMap,
    MindMapNode,
    MindMapSeed,
)
from finch.angle_discovery.service import AngleContext
from finch.ingest.resolver import ResolvedSource
from finch.llm.base import StructuredInferenceRunner

_MAP_VERSION = "1.0.0"
_NEW_PROMPT = Path("prompts/map-mindmap.md")
_EXPAND_PROMPT = Path("prompts/map-expand.md")
_CONNECT_PROMPT = Path("prompts/map-connect.md")


def map_id(content_hash: str) -> str:
    raw = hashlib.sha256(f"{content_hash}:{_MAP_VERSION}".encode()).hexdigest()
    return f"map_{raw[:16]}"


def _txt(value: str) -> str:
    return value or "（未提供）"


def _fmt(items: list[str]) -> str:
    return "\n".join(f"- {it}" for it in items) if items else "（未提供）"


def _next_id(nodes: list[MindMapNode]) -> str:
    return f"n{len(nodes)}"


class MindMapService:
    """发散思维导图领域服务。"""

    def __init__(self, runner: StructuredInferenceRunner) -> None:
        self.runner = runner

    def seed(self, source: ResolvedSource, context: AngleContext | None = None) -> MindMap:
        ctx = context or AngleContext()
        prompt = _NEW_PROMPT.read_text().format(
            sample_size=source.sample_size,
            body=source.body,
            reader=_txt(ctx.reader),
            reader_problem=_txt(ctx.reader_problem),
            author_context=_txt(ctx.author_context),
            practice_refs=_fmt(ctx.practice_refs),
            goal=_txt(ctx.goal),
        )
        seed = cast(MindMapSeed, self.runner.run(prompt, MindMapSeed))
        nodes: list[MindMapNode] = [
            MindMapNode(
                id="n0",
                label=seed.root_label,
                source="AI 假设",
                parent_id=None,
                expanded=True,
            )
        ]
        for branch in seed.branches:
            dim_id = _next_id(nodes)
            nodes.append(
                MindMapNode(
                    id=dim_id,
                    label=branch.dimension,
                    source=branch.dimension_source,
                    parent_id="n0",
                    expanded=True,
                )
            )
            for q in branch.questions:
                nodes.append(
                    MindMapNode(
                        id=_next_id(nodes),
                        label=q.label,
                        source=q.source,
                        parent_id=dim_id,
                    )
                )
        return MindMap(
            id=map_id(source.content_hash),
            source_type=source.source_type,
            source_ref=source.source_ref,
            content_hash=source.content_hash,
            root_label=seed.root_label,
            nodes=nodes,
        )
