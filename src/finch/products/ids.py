"""产品 / 创业者 / 方案 的确定性 ID（sha256 短前缀，匹配仓库惯例）。"""

import hashlib


def product_id_for(key: str) -> str:
    digest = hashlib.sha256(key.strip().lower().encode("utf-8")).hexdigest()
    return f"prod_{digest[:12]}"


def founder_id_for(platform: str, external_id: str) -> str:
    payload = "|".join([platform.strip().lower(), external_id.strip().lower()])
    return f"founder_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:12]}"


def solution_id_for(
    product_id: str, fingerprint: str, *, version: str, feedback_fingerprint: str = ""
) -> str:
    digest = hashlib.sha256(
        f"{product_id}:{fingerprint}:{version}:{feedback_fingerprint}".encode()
    ).hexdigest()
    return f"sol_{digest[:12]}"
