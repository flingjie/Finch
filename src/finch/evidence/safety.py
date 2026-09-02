"""Deterministic evidence safety scanner (Phase 4 Task A3)."""

import re
from typing import Literal

from pydantic import BaseModel, Field

from finch.evidence.models import EvidenceCard, Source


class SafetyHit(BaseModel):
    code: Literal["secret_detected", "private_repo_content", "nonexistent_commit"]
    detail: str
    card_id: str | None = None


class SafetyReport(BaseModel):
    hits: list[SafetyHit] = Field(default_factory=list)

    @property
    def hard_fail(self) -> bool:
        return bool(self.hits)


_SECRET_PATTERNS = (
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
)

_GITHUB_COMMIT_URL_PATTERN = re.compile(r"https://github\.com/([^/]+)/([^/]+)/commit/[^/]+")


def _extract_owner_repo(url: str) -> str | None:
    """Extract owner/repo from a GitHub commit URL.

    Args:
        url: GitHub commit URL like https://github.com/owner/repo/commit/sha

    Returns:
        owner/repo string or None if URL doesn't match expected pattern.
    """
    match = _GITHUB_COMMIT_URL_PATTERN.match(url)
    if match:
        owner, repo = match.groups()
        return f"{owner}/{repo}"
    return None


def _scan_for_secrets(text: str) -> list[str]:
    """Scan text for secret patterns, return list of matched pattern types."""
    found = []
    for pattern in _SECRET_PATTERNS:
        if pattern.search(text):
            found.append(pattern.pattern)
    return found


def scan_cards(
    cards: list[EvidenceCard],
    *,
    repo_is_private: dict[str, bool],
    known_commit_urls: set[str],
) -> SafetyReport:
    """Scan evidence cards for safety issues.

    Args:
        cards: List of evidence cards to scan.
        repo_is_private: Mapping of owner/repo to boolean indicating if private.
        known_commit_urls: Set of known/valid commit URLs.

    Returns:
        SafetyReport with any hits found.
    """
    hits: list[SafetyHit] = []

    for card in cards:
        # Check for secrets in claim and source URLs
        all_text = card.claim
        for source in card.sources:
            all_text += " " + source.url

        secret_matches = _scan_for_secrets(all_text)
        if secret_matches:
            detail = f"Secret patterns detected in card {card.id}: {len(secret_matches)} pattern(s) matched"
            hits.append(SafetyHit(
                code="secret_detected",
                detail=detail,
                card_id=card.id,
            ))

        # Check for private repo content or unpublishable
        if not card.publishable:
            hits.append(SafetyHit(
                code="private_repo_content",
                detail=f"Card {card.id} is marked as not publishable",
                card_id=card.id,
            ))
        else:
            for source in card.sources:
                owner_repo = _extract_owner_repo(source.url)
                if owner_repo and repo_is_private.get(owner_repo, False):
                    hits.append(SafetyHit(
                        code="private_repo_content",
                        detail=f"Card {card.id} references private repo {owner_repo}",
                        card_id=card.id,
                    ))

        # Check for nonexistent commits
        for source in card.sources:
            if source.type == "commit" and source.url not in known_commit_urls:
                hits.append(SafetyHit(
                    code="nonexistent_commit",
                    detail=f"Commit URL {source.url} not in known commits set for card {card.id}",
                    card_id=card.id,
                ))

    return SafetyReport(hits=hits)
