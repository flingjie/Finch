"""机会聚合（规范 §10/§11）：带生命周期状态机的交流机会。"""

from finch.opportunities.models import (
    Artifact,
    ArtifactKind,
    ContributionForm,
    EntryKind,
    EvidenceRef,
    EvidenceTier,
    ExecutionStatus,
    MaterialOrigin,
    Opportunity,
    OpportunityEvent,
    OpportunityStatus,
    Proposal,
)
from finch.opportunities.repository import ArtifactRepository, OpportunityRepository
from finch.opportunities.service import OpportunityConflictError, OpportunityService

__all__ = [
    "EntryKind",
    "EvidenceTier",
    "ContributionForm",
    "OpportunityStatus",
    "EvidenceRef",
    "Proposal",
    "Opportunity",
    "OpportunityEvent",
    "Artifact",
    "ArtifactKind",
    "MaterialOrigin",
    "ExecutionStatus",
    "OpportunityRepository",
    "ArtifactRepository",
    "OpportunityService",
    "OpportunityConflictError",
]
