"""Tests for ContentJob models and repositories (idea 候选流实体)."""

from finch.content.jobs import (
    AuthorPosition,
    ContentJob,
    ContentJobStatus,
)
from finch.content.models import RecommendedFormat
from finch.storage.database import Store
from finch.storage.repositories import ContentJobRepository


class TestAuthorPosition:
    """Test AuthorPosition model."""

    def test_complete_author_position(self):
        pos = AuthorPosition(
            claim="Connection pooling improves throughput",
            decision="Use pool size of 10 connections",
            tradeoff="Increased memory usage per connection",
            change_mind_if="Benchmarks show no improvement",
        )
        assert "pool size" in pos.decision

    def test_author_position_without_optional_fields(self):
        pos = AuthorPosition(
            claim="Use caching",
            decision="Cache for 5 minutes",
            tradeoff="Stale data risk",
        )
        assert pos.change_mind_if is None


class TestContentJob:
    """Test ContentJob model."""

    def test_content_job_status_enum_values(self):
        """Test ContentJobStatus enum string values."""
        assert ContentJobStatus.PROPOSED.value == "proposed"
        assert ContentJobStatus.CONFIRMED.value == "confirmed"
        assert ContentJobStatus.DRAFTED.value == "drafted"
        assert ContentJobStatus.SKIPPED.value == "skipped"

    def test_content_job_core_defaults(self):
        """Test the additive fields default to their minimal values."""
        job = ContentJob(
            id="job_1",
            source_card_ids=["card_1"],
            candidate_id=None,
            reader_problem="Problem",
            author_position=None,
            recommended_format=RecommendedFormat.REPLY,
            status=ContentJobStatus.PROPOSED,
        )
        assert job.core_message == ""
        assert job.why_now == ""

    def test_basic_content_job(self):
        job = ContentJob(
            id="job_1",
            source_card_ids=["card_1", "card_2"],
            candidate_id=None,
            reader_problem="Readers don't know how to configure connection pooling",
            author_position=None,
            recommended_format=RecommendedFormat.REPLY,
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
            author_position=AuthorPosition(
                claim="Pooling helps",
                decision="Use 10 connections",
                tradeoff="More memory",
            ),
            recommended_format=RecommendedFormat.SHORT_POST,
            status=ContentJobStatus.CONFIRMED,
        )
        assert job.author_position is not None
        assert job.author_position.decision == "Use 10 connections"


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
            author_position=None,
            recommended_format=RecommendedFormat.REPLY,
            status=ContentJobStatus.CONFIRMED,
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
                author_position=None,
                recommended_format=RecommendedFormat.REPLY,
                status=ContentJobStatus.CONFIRMED,
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
            author_position=None,
            recommended_format=RecommendedFormat.REPLY,
            status=ContentJobStatus.CONFIRMED,
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
            author_position=None,
            recommended_format=RecommendedFormat.REPLY,
            status=ContentJobStatus.CONFIRMED,
        )
        job2 = ContentJob(
            id="job_2",
            source_card_ids=["card_2"],
            candidate_id=None,
            reader_problem="Problem 2",
            author_position=None,
            recommended_format=RecommendedFormat.SHORT_POST,
            status=ContentJobStatus.SKIPPED,
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
