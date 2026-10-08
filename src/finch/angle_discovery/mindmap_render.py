"""MindMap → Mermaid 渲染（纯函数，无副作用）。"""

from __future__ import annotations

from finch.angle_discovery.mindmap_models import MindMap, MindMapCombination, MindMapNode


def _children(nodes: list[MindMapNode]) -> dict[str | None, list[MindMapNode]]:
    out: dict[str | None, list[MindMapNode]] = {}
    for n in nodes:
        out.setdefault(n.parent_id, []).append(n)
    return out


def _walk(
    node: MindMapNode,
    children: dict[str | None, list[MindMapNode]],
    depth: int,
    max_depth: int | None,
    lines: list[str],
) -> None:
    kids = children.get(node.id, [])
    if not kids:
        return
    if not node.expanded or (max_depth is not None and depth >= max_depth):
        lines.append(f"{'  ' * (depth + 2)}（{len(kids)} 个待展开）")
        return
    for k in kids:
        tag = "" if children.get(k.id) else f"〔{k.source}〕"
        lines.append(f"{'  ' * (depth + 2)}{k.label}{tag}")
        _walk(k, children, depth + 1, max_depth, lines)


def render_mindmap(m: MindMap, max_depth: int | None = None) -> str:
    children = _children(m.nodes)
    root = next((n for n in m.nodes if n.parent_id is None), None)
    if root is None:
        return "（空导图）"
    lines = ["```mermaid", "mindmap", f"  root(({root.label}))"]
    _walk(root, children, 0, max_depth, lines)
    lines.append("```")
    return "\n".join(lines)


def _q(s: str) -> str:
    return s.replace("\n", " ").replace('"', "'")


def render_combination(combo: MindMapCombination, labels: dict[str, str]) -> str:
    a = labels.get(combo.node_a, combo.node_a)
    b = labels.get(combo.node_b, combo.node_b)
    c = combo.angle_title or "组合角度"
    lines = [
        "```mermaid",
        "flowchart TD",
        f'    A["{_q(a)}"] --> C["{_q(c)}"]',
        f'    B["{_q(b)}"] --> C',
    ]
    if combo.thesis:
        lines.append(f'    C --> D["{_q(combo.thesis)}"]')
    if combo.validation_gap:
        lines.append(f'    C --> E["待验证：{_q(combo.validation_gap)}"]')
    lines.append("```")
    if combo.connection_rationale:
        lines.append(f"- 连接理由：{combo.connection_rationale}")
    if combo.incremental_value:
        lines.append(f"- 新增价值：{combo.incremental_value}")
    if combo.applicable_boundary:
        lines.append(f"- 适用边界：{combo.applicable_boundary}")
    if combo.validation_gap:
        lines.append(f"- 验证缺口：{combo.validation_gap}")
    return "\n".join(lines)
