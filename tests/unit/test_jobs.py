"""Tests for ContentJob models and repositories (Spec §8)."""

from finch.codex.runner import CodexRunner
from finch.content.jobs import (
    AuthorPosition,
    ContentJob,
    ContentJobStatus,
    IntendedEffect,
    SuccessCriterion,
    TopicProposal,
    expand_content_job,
    plan_content_topics,
)
from finch.content.models import DraftKind
from finch.evidence.models import ClaimConfidence, EvidenceCard
from finch.storage.database import Store
from finch.storage.repositories import ContentJobRepository


class TestIntendedEffect:
    """Test IntendedEffect model."""

    def test_basic_intended_effect(self):
        effect = IntendedEffect(
            understand="Connection pooling reduces latency",
            believe=None,
            action=None,
        )
        assert effect.understand == "Connection pooling reduces latency"
        assert effect.believe is None
        assert effect.action is None

    def test_intended_effect_with_all_fields(self):
        effect = IntendedEffect(
            understand="What connection pooling is",
            believe="It's worth the complexity",
            action="Configure pool size based on load",
        )
        assert effect.understand == "What connection pooling is"
        assert effect.believe == "It's worth the complexity"
        assert effect.action == "Configure pool size based on load"


class TestAuthorPosition:
    """Test AuthorPosition model."""

    def test_complete_author_position(self):
        pos = AuthorPosition(
            claim="Connection pooling improves throughput",
            decision="Use pool size of 10 connections",
            tradeoff="Increased memory usage per connection",
            change_mind_if="Benchmarks show no improvement",
            confirmed=False,
        )
        assert pos.confirmed is False
        assert "pool size" in pos.decision

    def test_author_position_without_optional_fields(self):
        pos = AuthorPosition(
            claim="Use caching",
            decision="Cache for 5 minutes",
            tradeoff="Stale data risk",
        )
        assert pos.change_mind_if is None
        assert pos.confirmed is False


class TestSuccessCriterion:
    """Test SuccessCriterion model."""

    def test_success_criterion_all_measurement_types(self):
        c1 = SuccessCriterion(
            id="crit_1",
            description="Reader can explain the tradeoff",
            measurement="critic",
        )
        c2 = SuccessCriterion(
            id="crit_2",
            description="Reader implements the pattern",
            measurement="human",
        )
        c3 = SuccessCriterion(
            id="crit_3",
            description="System throughput increases by 10%",
            measurement="outcome",
        )
        assert c1.measurement == "critic"
        assert c2.measurement == "human"
        assert c3.measurement == "outcome"


class TestContentJob:
    """Test ContentJob model."""

    def test_content_job_status_enum_values(self):
        """Test ContentJobStatus enum string values."""
        assert ContentJobStatus.PROPOSED.value == "proposed"
        assert ContentJobStatus.NEEDS_INPUT.value == "needs_input"
        assert ContentJobStatus.READY.value == "ready"
        assert ContentJobStatus.DO_NOT_WRITE.value == "do_not_write"

    def test_basic_content_job(self):
        job = ContentJob(
            id="job_1",
            source_card_ids=["card_1", "card_2"],
            candidate_id=None,
            reader_problem="Readers don't know how to configure connection pooling",
            audience="Backend engineers",
            intended_effect=IntendedEffect(
                understand="How to configure pool size",
                believe=None,
                action=None,
            ),
            author_position=None,
            success_criteria=[],
            recommended_format=DraftKind.REPLY,
            status=ContentJobStatus.PROPOSED,
        )
        assert job.id == "job_1"
        assert job.source_card_ids == ["card_1", "card_2"]

    def test_content_job_with_complete_author_position(self):
        job = ContentJob(
            id="job_ready",
            source_card_ids=["card_1"],
            candidate_id="cand_1",
            reader_problem="Readers face latency issues",
            audience="SREs",
            intended_effect=IntendedEffect(
                understand="Connection pooling works",
                believe=None,
                action=None,
            ),
            author_position=AuthorPosition(
                claim="Pooling helps",
                decision="Use 10 connections",
                tradeoff="More memory",
            ),
            success_criteria=[
                SuccessCriterion(
                    id="c1", description="Got it", measurement="critic"
                )
            ],
            recommended_format=DraftKind.ORIGINAL,
            status=ContentJobStatus.READY,
        )
        assert job.author_position is not None
        assert job.author_position.decision == "Use 10 connections"

    def test_validate_source_cards_subset_check(self):
        """Test that source_card_ids must be a subset of available_card_ids."""
        job = ContentJob(
            id="job_1",
            source_card_ids=["card_a", "card_b"],
            candidate_id=None,
            reader_problem="Problem",
            audience="Engineers",
            intended_effect=IntendedEffect(understand="Solution"),
            author_position=None,
            success_criteria=[],
            recommended_format=DraftKind.REPLY,
            status=ContentJobStatus.READY,
        )
        # Valid: all source cards exist
        assert job.validate_source_cards(["card_a", "card_b", "card_c"]) is True
        # Invalid: card_d not in available
        assert job.validate_source_cards(["card_a", "card_x"]) is False
        # Valid: exact match
        assert job.validate_source_cards(["card_a", "card_b"]) is True
        # Invalid: source has more than available
        assert job.validate_source_cards(["card_a"]) is False

    def test_missing_questions_default_factory(self):
        """Test missing_questions uses empty list by default."""
        job = ContentJob(
            id="job_1",
            source_card_ids=["card_1"],
            candidate_id=None,
            reader_problem="Problem",
            audience="Engineers",
            intended_effect=IntendedEffect(understand="Solution"),
            author_position=None,
            success_criteria=[],
            recommended_format=DraftKind.REPLY,
            status=ContentJobStatus.READY,
        )
        assert job.missing_questions == []

    def test_missing_questions_max_length(self):
        """Test that missing_questions can have at most 3 entries."""
        # This should work - exactly 3 items
        job = ContentJob(
            id="job_1",
            source_card_ids=["card_1"],
            candidate_id=None,
            reader_problem="Problem",
            audience="Engineers",
            intended_effect=IntendedEffect(understand="Solution"),
            author_position=None,
            success_criteria=[],
            recommended_format=DraftKind.REPLY,
            status=ContentJobStatus.READY,
            missing_questions=["q1", "q2", "q3"],
        )
        assert len(job.missing_questions) == 3

    def test_content_job_without_author_position_needs_input(self):
        """Test that missing author_position means NEEDS_INPUT."""
        job = ContentJob(
            id="job_1",
            source_card_ids=["card_1"],
            candidate_id=None,
            reader_problem="Problem",
            audience="Engineers",
            intended_effect=IntendedEffect(understand="Solution"),
            author_position=None,  # Missing!
            success_criteria=[],
            recommended_format=DraftKind.REPLY,
            status=ContentJobStatus.PROPOSED,
        )
        assert job.needs_input() is True

    def test_author_position_missing_decision_needs_input(self):
        """Test that missing author_position.decision means NEEDS_INPUT."""
        job = ContentJob(
            id="job_1",
            source_card_ids=["card_1"],
            candidate_id=None,
            reader_problem="Problem",
            audience="Engineers",
            intended_effect=IntendedEffect(understand="Solution"),
            author_position=AuthorPosition(
                claim="Statement",
                decision="",  # Empty decision!
                tradeoff="Tradeoff",
            ),
            success_criteria=[],
            recommended_format=DraftKind.REPLY,
            status=ContentJobStatus.PROPOSED,
        )
        assert job.needs_input() is True

    def test_author_position_missing_tradeoff_needs_input(self):
        """Test that missing author_position.tradeoff means NEEDS_INPUT."""
        job = ContentJob(
            id="job_1",
            source_card_ids=["card_1"],
            candidate_id=None,
            reader_problem="Problem",
            audience="Engineers",
            intended_effect=IntendedEffect(understand="Solution"),
            author_position=AuthorPosition(
                claim="Statement",
                decision="Decision",
                tradeoff="",  # Empty tradeoff!
            ),
            success_criteria=[],
            recommended_format=DraftKind.REPLY,
            status=ContentJobStatus.PROPOSED,
        )
        assert job.needs_input() is True


class TestContentJobRepository:
    """Test ContentJobRepository persistence."""

    def test_upsert_and_get_job(self, tmp_path):
        """Test upsert_job and get_job."""
        store = Store(tmp_path / "db.sqlite")
        store.init()
        repo = ContentJobRepository(store)

        job = ContentJob(
            id="job_1",
            source_card_ids=["card_1"],
            candidate_id=None,
            reader_problem="Problem",
            audience="Engineers",
            intended_effect=IntendedEffect(understand="Solution"),
            author_position=None,
            success_criteria=[],
            recommended_format=DraftKind.REPLY,
            status=ContentJobStatus.READY,
        )
        repo.upsert_job(job)

        got = repo.get_job("job_1")
        assert got is not None
        assert got.id == "job_1"

    def test_upsert_jobs_batch(self, tmp_path):
        """Test batch upsert_jobs inserts and overwrites in one transaction."""
        store = Store(tmp_path / "db.sqlite")
        store.init()
        repo = ContentJobRepository(store)
        jobs = [
            ContentJob(
                id=f"job_{i}",
                source_card_ids=["card_1"],
                candidate_id=None,
                reader_problem=f"Problem {i}",
                audience="Engineers",
                intended_effect=IntendedEffect(understand="Solution"),
                author_position=None,
                success_criteria=[],
                recommended_format=DraftKind.REPLY,
                status=ContentJobStatus.READY,
            )
            for i in range(3)
        ]
        repo.upsert_jobs(jobs)
        assert [j.id for j in repo.list_jobs()] == ["job_0", "job_1", "job_2"]

        updated = [j.model_copy(update={"reader_problem": f"P{i}v2"}) for i, j in enumerate(jobs)]
        repo.upsert_jobs(updated)
        assert len(repo.list_jobs()) == 3
        assert [j.reader_problem for j in repo.list_jobs()] == ["P0v2", "P1v2", "P2v2"]

    def test_update_existing_job(self, tmp_path):
        """Test that upsert_job updates existing job."""
        store = Store(tmp_path / "db.sqlite")
        store.init()
        repo = ContentJobRepository(store)

        job1 = ContentJob(
            id="job_1",
            source_card_ids=["card_1"],
            candidate_id=None,
            reader_problem="Problem v1",
            audience="Engineers",
            intended_effect=IntendedEffect(understand="Solution"),
            author_position=None,
            success_criteria=[],
            recommended_format=DraftKind.REPLY,
            status=ContentJobStatus.READY,
        )
        repo.upsert_job(job1)

        job2 = job1.model_copy(update={"reader_problem": "Problem v2"})
        repo.upsert_job(job2)

        got = repo.get_job("job_1")
        assert got is not None
        assert got.reader_problem == "Problem v2"

    def test_list_jobs(self, tmp_path):
        """Test list_jobs returns all jobs."""
        store = Store(tmp_path / "db.sqlite")
        store.init()
        repo = ContentJobRepository(store)

        job1 = ContentJob(
            id="job_1",
            source_card_ids=["card_1"],
            candidate_id=None,
            reader_problem="Problem 1",
            audience="Engineers",
            intended_effect=IntendedEffect(understand="Solution 1"),
            author_position=None,
            success_criteria=[],
            recommended_format=DraftKind.REPLY,
            status=ContentJobStatus.READY,
        )
        job2 = ContentJob(
            id="job_2",
            source_card_ids=["card_2"],
            candidate_id=None,
            reader_problem="Problem 2",
            audience="SREs",
            intended_effect=IntendedEffect(understand="Solution 2"),
            author_position=None,
            success_criteria=[],
            recommended_format=DraftKind.ORIGINAL,
            status=ContentJobStatus.DO_NOT_WRITE,
        )

        repo.upsert_job(job1)
        repo.upsert_job(job2)

        jobs = repo.list_jobs()
        assert len(jobs) == 2
        assert jobs[0].id == "job_1"
        assert jobs[1].id == "job_2"

    def test_get_nonexistent_job(self, tmp_path):
        """Test get_job returns None for nonexistent job."""
        store = Store(tmp_path / "db.sqlite")
        store.init()
        repo = ContentJobRepository(store)

        assert repo.get_job("nonexistent") is None


class _ExplodingRunner(CodexRunner):
    def __init__(self) -> None:
        self.calls = 0

    def run(self, prompt, output_model, **kwargs):
        self.calls += 1
        raise AssertionError("runner must not be called when there are no evidence cards")


def test_plan_content_topics_skips_runner_when_no_cards():
    runner = _ExplodingRunner()
    out = plan_content_topics(runner, [], [], [])
    assert out.items == []
    assert runner.calls == 0


def test_expand_content_job_forces_confirmed_false():
    class _JobRunner(CodexRunner):
        def run(self, prompt, output_model, **kw):
            return ContentJob(
                id="job1", source_card_ids=["ev1"], candidate_id="t1",
                reader_problem="r", audience="a",
                intended_effect=IntendedEffect(understand="u"),
                author_position=AuthorPosition(
                    claim="c", decision="d", tradeoff="t", confirmed=True,
                ),
                success_criteria=[], recommended_format=DraftKind.REPLY,
                status=ContentJobStatus.READY,
            )

    topic = TopicProposal(id="tp1", title="t", card_ids=["ev1"], candidate_id="t1")
    card = EvidenceCard(
        id="ev1", event_id="e", claim="token bucket", sources=[],
        confidence=ClaimConfidence.VERIFIED, publishable=True, topics=["rate"],
    )
    job = expand_content_job(_JobRunner(), topic, {"ev1": card}, None)
    assert job.author_position is not None
    assert job.author_position.confirmed is False
