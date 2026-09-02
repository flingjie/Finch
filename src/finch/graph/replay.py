"""Replay 骨架：从指定节点安全重放，复用已完成节点记录。"""

from .nodes import Node
from .runtime import GraphRuntime
from ..storage.database import RunRecord, Store


def replay(
    store: Store,
    nodes: list[Node],
    run_id: str,
    from_node: str | None = None,
) -> RunRecord:
    """复用 from_node 之前已成功的节点，重新执行其后的节点。"""
    if from_node is None:
        # 全量重放：复用全部已成功节点，等价于恢复运行。
        return GraphRuntime(store, nodes).run(run_id=run_id)

    names = [n.name for n in nodes]
    if from_node not in names:
        raise ValueError(f"unknown from_node: {from_node}")

    start_idx = names.index(from_node)
    replay_nodes = nodes[start_idx:]
    # 已成功的节点记录会令 Runtime 自动跳过；这里仅重放 from_node 起的节点。
    rt = GraphRuntime(store, replay_nodes)
    return rt.run(run_id=run_id)
