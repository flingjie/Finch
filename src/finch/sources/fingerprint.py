"""工件指纹与幂等键。"""

from __future__ import annotations

import hashlib
import re


def content_fingerprint(text: str, *, url: str = "") -> str:
    """内容指纹：归一化空白后 sha256 前 16 hex。"""
    normalized = re.sub(r"\s+", " ", (text or "").strip().lower())
    payload = f"{url}|{normalized}"
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def artifact_id(source: str, source_type: str, source_id: str) -> str:
    """稳定 artifact_id：``{source}:{source_type}:{source_id}``。"""
    return f"{source}:{source_type}:{source_id}"


def idempotency_key(
    source: str, source_type: str, source_id: str, fingerprint: str
) -> str:
    """幂等键：``{source}:{source_type}:{source_id}:{fingerprint}``。"""
    return f"{source}:{source_type}:{source_id}:{fingerprint}"
