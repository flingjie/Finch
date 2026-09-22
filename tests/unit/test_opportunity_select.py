"""交流机会选择单元测试。"""


from discovery_samples import (
    CROSS_DOMAIN_METHOD,
    FAMILIAR_VIEW,
    LEARN_ONLY,
    MISSING_SOURCE,
    NEW_AUTHOR,
    OLD_AUTHOR_NEW_EXPERIMENT,
)

from finch.engagement.models import ConversationScore, ExternalPost, SuggestedMode
from finch.engagement.opportunity import (
    content_fingerprint,
    quality_gate,
    scored_post_to_opportunity,
    select_opportunity_set,
)
from finch.engagement.scoring import ScoredPost


def _scored(post: ExternalPost, *, total: float = 0.85) -> ScoredPost:
    return ScoredPost(
        post=post,
        score=ConversationScore(
            relevance=0.9,
            novelty=0.8,
            discussability=0.8,
            practical_evidence=0.8,
            relationship_value=0.5,
            total=total,
            reasons=["on topic with concrete practice detail"],
        ),
    )


def _opp(post: ExternalPost, **kwargs):
    base = dict(
        why_relevant="Concrete overlap with failure-replay practice and checkpoints",
        opening="Ask how they validate compensation after a partial tool failure",
        suggested_mode=SuggestedMode.DISCUSS,
        novelty_reason="new experiment numbers" if post.id == "old_exp_2" else "",
        topic_tags=list(post.matched_topics) or ["agent"],
    )
    if post.id == "cross_1":
        base["topic_tags"] = ["compensation", "distributed"]
        base["discovered_via"] = "adjacent mechanism"
    if post.id == "learn_1":
        base["suggested_mode"] = SuggestedMode.LEARN
        base["opening"] = ""
        base["topic_tags"] = ["explore", "handoff"]
    base.update(kwargs)
    return scored_post_to_opportunity(_scored(post), **base)


def test_content_fingerprint_ignores_whitespace():
    a = content_fingerprint("hello   world\n")
    b = content_fingerprint("hello world")
    assert a == b


def test_quality_gate_rejects_missing_source():
    opp = _opp(MISSING_SOURCE)
    assert quality_gate(opp) is False


def test_select_unique_authors_and_no_padding():
    opps = [
        _opp(FAMILIAR_VIEW, novelty_reason=""),
        _opp(OLD_AUTHOR_NEW_EXPERIMENT, novelty_reason="new experiment numbers"),
        _opp(NEW_AUTHOR),
        _opp(CROSS_DOMAIN_METHOD),
        _opp(LEARN_ONLY),
    ]
    selected = select_opportunity_set(opps, limit=10)
    peer_ids = [o.peer_id for o in selected]
    assert len(peer_ids) == len(set(peer_ids))
    # alice appears once (new experiment preferred / related hung)
    assert sum(1 for o in selected if o.post and o.post.author_id == "alice") == 1
    assert len(selected) <= 5
    # does not pad beyond qualified
    assert len(select_opportunity_set(opps[:1], limit=10)) == 1


def test_old_author_new_content_kept_over_familiar_without_novelty():
    familiar = _opp(FAMILIAR_VIEW, novelty_reason="")
    fresh = _opp(OLD_AUTHOR_NEW_EXPERIMENT, novelty_reason="new experiment numbers")
    selected = select_opportunity_set(
        [familiar, fresh],
        limit=1,
        familiar_peer_ids={familiar.peer_id},
    )
    assert len(selected) == 1
    assert selected[0].post is not None
    assert selected[0].post.id == "old_exp_2"


def test_stable_id_tie_break():
    a = _opp(NEW_AUTHOR, why_relevant="Concrete overlap A with practice detail here")
    b = _opp(
        CROSS_DOMAIN_METHOD,
        why_relevant="Concrete overlap B with practice detail here",
    )
    # Force equal scores
    a = a.model_copy(update={"score_total": 0.8})
    b = b.model_copy(update={"score_total": 0.8})
    s1 = select_opportunity_set([a, b], limit=2)
    s2 = select_opportunity_set([b, a], limit=2)
    assert [o.id for o in s1] == [o.id for o in s2]


def test_cross_domain_method_not_keyword_blocked():
    selected = select_opportunity_set([_opp(CROSS_DOMAIN_METHOD)], limit=5)
    assert len(selected) == 1
    assert "compensation" in " ".join(selected[0].topic_tags)


def test_seen_fingerprint_suppressed():
    opp = _opp(FAMILIAR_VIEW)
    selected = select_opportunity_set(
        [opp], limit=5, seen_fingerprints={opp.content_fingerprint}
    )
    assert selected == []


def test_assign_next_action_modes():
    from finch.engagement.opportunity import assign_next_action

    assert assign_next_action(SuggestedMode.LEARN, has_practice=False) == ("observe", 5)
    assert assign_next_action(SuggestedMode.DISCUSS, has_practice=False)[0] == "ask"
    assert assign_next_action(SuggestedMode.DISCUSS, has_practice=True)[0] == "reply"
    assert (
        assign_next_action(
            SuggestedMode.INVESTIGATE, has_practice=False, time_budget=20
        )[0]
        == "try"
    )
    assert (
        assign_next_action(
            SuggestedMode.INVESTIGATE, has_practice=False, time_budget=10
        )[0]
        == "ask"
    )


def test_pending_review_excluded_from_core_set():
    from finch.peers.models import EvidenceStatus

    core = _opp(NEW_AUTHOR, evidence_status=EvidenceStatus.SOURCED)
    thin = _opp(
        FAMILIAR_VIEW,
        evidence_status=EvidenceStatus.PENDING_REVIEW,
        why_relevant="Concrete overlap with failure-replay practice and checkpoints",
        opening="Ask how they validate compensation after a partial tool failure",
    )
    selected = select_opportunity_set([core, thin], limit=10)
    assert all(o.evidence_status != EvidenceStatus.PENDING_REVIEW.value for o in selected)
    assert core.id in {o.id for o in selected}


def test_pending_review_fills_only_when_core_empty():
    from finch.peers.models import EvidenceStatus

    thin = _opp(
        NEW_AUTHOR,
        evidence_status=EvidenceStatus.PENDING_REVIEW,
        why_relevant="Concrete overlap with failure-replay practice and checkpoints",
        opening="Ask how they validate compensation after a partial tool failure",
    )
    selected = select_opportunity_set([thin], limit=10)
    assert len(selected) == 1
    assert selected[0].evidence_status == EvidenceStatus.PENDING_REVIEW.value
    assert "待了解" in selected[0].uncertainty


def test_old_opportunity_yaml_loads_without_new_fields():
    """Optional Value Discovery fields must default when absent."""
    from datetime import UTC, datetime

    from finch.engagement.models import Opportunity

    opp = Opportunity.model_validate(
        {
            "id": "opp_legacy",
            "peer_id": "peer_x",
            "why_relevant": "Concrete overlap with failure-replay practice",
            "opening": "Ask about compensation after partial failure",
            "suggested_mode": "discuss",
            "assessed_at": datetime.now(UTC).isoformat(),
        }
    )
    assert opp.next_action is None
    assert opp.estimated_minutes is None
    assert opp.shared_problem == ""
    assert opp.contribution_basis_refs == []
