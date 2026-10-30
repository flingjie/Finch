"""问题发现领域确定性 ID（sha256 短前缀，匹配仓库惯例）。"""

import hashlib


def _digest(payload: str, n: int = 12) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:n]


def problem_id_for(title: str) -> str:
    """问题 ID：按规范化标题内容寻址（折叠空白、小写），实现去重/幂等。"""
    return f"prob_{_digest(' '.join(title.strip().split()).lower())}"


def discussion_id_for(url: str, text: str) -> str:
    return f"disc_{_digest('|'.join([url.strip(), text.strip()]))}"
