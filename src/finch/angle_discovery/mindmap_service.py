"""MindMapService：结构化推理生成/增长发散思维导图。

确定性字段（id、source_type/source_ref/content_hash、节点 id/parent_id/expanded、
组合 node_a/node_b）由代码覆盖，不信任模型输出。
"""

import hashlib
from pathlib import Path
from typing import cast

from finch.angle_discovery.mindmap_models import (
    MindMap,
    MindMapCombination,
    MindMapExpansion,
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


def _find_node(m: MindMap, node_id: str) -> MindMapNode:
    for n in m.nodes:
        if n.id == node_id:
            return n
    raise RuntimeError(f"node not found: {node_id}")


def _path_labels(m: MindMap, node: MindMapNode) -> list[str]:
    by_id = {n.id: n for n in m.nodes}
    labels = [node.label]
    cur = node
    while cur.parent_id is not None and cur.parent_id in by_id:
        cur = by_id[cur.parent_id]
        labels.append(cur.label)
    return list(reversed(labels))


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

    def expand(
        self, m: MindMap, node_id: str, move: str = "追问", predict: str = ""
    ) -> MindMap:
        node = _find_node(m, node_id)
        path = " → ".join(_path_labels(m, node))
        prompt = _EXPAND_PROMPT.read_text().format(
            root=m.root_label,
            path=path,
            move=move,
            predict=predict or "（未提供）",
        )
        expansion = cast(MindMapExpansion, self.runner.run(prompt, MindMapExpansion))
        updated = m.model_copy(deep=True)
        nodes = updated.nodes
        target = node_id
        if predict.strip():
            p = MindMapNode(
                id=_next_id(nodes), label=predict.strip(), source="我的补充", parent_id=node_id
            )
            nodes.append(p)
            target = p.id
        for q in expansion.nodes:
            nodes.append(
                MindMapNode(id=_next_id(nodes), label=q.label, source=q.source, parent_id=target)
            )
        for n in nodes:
            if n.id == node_id:
                n.expanded = True
        return updated

    def connect(self, m: MindMap, node_a: str, node_b: str) -> MindMap:
        a = _find_node(m, node_a)
        b = _find_node(m, node_b)
        prompt = _CONNECT_PROMPT.read_text().format(
            root=m.root_label,
            node_a=a.label,
            node_b=b.label,
        )
        combo = cast(MindMapCombination, self.runner.run(prompt, MindMapCombination))
        combo = combo.model_copy(update={"node_a": node_a, "node_b": node_b})
        updated = m.model_copy(deep=True)
        updated.combinations.append(combo)
        return updated
