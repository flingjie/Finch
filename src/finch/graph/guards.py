"""Graph Guard：可组合的通过/阻断检查。"""

from pydantic import BaseModel

from .events import NodeResult


class Guard(BaseModel):
    name: str

    def check(self, result: NodeResult, ctx: dict) -> bool:
        """默认守卫：节点成功即通过。"""
        return result.status == "succeeded"
