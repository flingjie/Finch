"""idea 数据模型。"""

from pydantic import BaseModel


class RewriteIdeaOutput(BaseModel):
    """rewrite 内部输出：只回传正文（idea 草稿 claims 恒为空）。"""

    body: str
