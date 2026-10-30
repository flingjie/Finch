"""关系领域确定性 ID（sha256 短前缀，匹配仓库惯例）。"""

import hashlib


def _digest(payload: str, n: int = 12) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:n]


def person_id_for(*keys: str) -> str:
    """人物 ID：由身份键（平台+external_id / handle / 任意稳定键）内容派生。"""
    joined = "|".join(k.strip().lower() for k in keys if k and k.strip())
    return f"person_{_digest(joined)}"


def event_id_for(person_id: str, occurred_at: str, source_refs: str) -> str:
    return f"evt_{_digest('|'.join([person_id, occurred_at, source_refs]))}"


def contact_id_for(person_id: str, occurred_at: str, source_ref: str) -> str:
    return f"contact_{_digest('|'.join([person_id, occurred_at, source_ref]))}"


def next_id_for(person_id: str, suggestion: str) -> str:
    return f"next_{_digest('|'.join([person_id, suggestion.strip()]))}"


def briefing_id_for(fingerprint: str, created_at: str) -> str:
    return f"brief_{_digest('|'.join([fingerprint, created_at]))}"


def note_id_for(url: str, text: str) -> str:
    return f"note_{_digest('|'.join([url.strip(), text.strip()]))}"
