"""摄取阶段（阶段 A）：per-SHA 增量发现 + 两段有界预算 + 预分组 group。"""

from datetime import UTC, datetime, timedelta

from ..evidence.budget import rank_pending, select_groups
from ..settings import Settings
from ..storage.repositories import (
    CommitIngestionRecord,
    CommitIngestionRepository,
    RepoCursorRepository,
)
from .change_grouper import group_commits
from .commit_reader import is_noise
from .gh_client import GhClient
from .local_repo import LocalRepoClient, find_local_clone
from .models import CommitDetail, CommitSummary


def _as_utc(dt: datetime) -> datetime:
    """SQLite 往返丢失 tzinfo；补回 UTC，使 ``now`` 与 ``discovered_at`` 可相减。"""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def assign_group_ids(
    records: list[CommitIngestionRecord],
) -> dict[str, list[CommitIngestionRecord]]:
    """append-only 分组：已分配（group_id 非空）冻结；未分配经 ``group_commits`` 分组，
    新组 group_id = 首 commit sha（author 顺序最早）。返回 group_id -> records。"""
    assigned: dict[str, list[CommitIngestionRecord]] = {}
    unassigned: list[CommitIngestionRecord] = []
    for r in records:
        if r.group_id is not None:
            assigned.setdefault(r.group_id, []).append(r)
        else:
            unassigned.append(r)
    if not unassigned:
        return assigned

    details_by_sha = {
        r.sha: CommitDetail.model_validate_json(r.payload_json) for r in unassigned
    }
    ordered = sorted(unassigned, key=lambda r: details_by_sha[r.sha].author_date)
    new_groups = group_commits([details_by_sha[r.sha] for r in ordered])
    record_by_sha = {r.sha: r for r in unassigned}
    for group in new_groups:
        gid = group[0].sha
        for detail in group:
            record = record_by_sha[detail.sha]
            record.group_id = gid
            assigned.setdefault(gid, []).append(record)
    return assigned


class Ingestor:
    """把新 commit 增量落 ledger，并产出本轮预算内的预分组 group。"""

    def __init__(
        self,
        gh: GhClient,
        settings: Settings,
        ingestion: CommitIngestionRepository,
        cursor: RepoCursorRepository,
    ) -> None:
        self.gh = gh
        self.settings = settings
        self.ingestion = ingestion
        self.cursor = cursor

    def ingest(
        self, repos: list[str], existing_topics: set[str]
    ) -> dict[str, list[list[CommitDetail]]]:
        now = datetime.now(UTC)
        budget = self.settings.daily_budget
        return {
            repo: self._ingest_repo(repo, existing_topics, now, budget) for repo in repos
        }

    def _ingest_repo(
        self, repo: str, existing_topics: set[str], now: datetime, budget
    ) -> list[list[CommitDetail]]:
        # 1) 发现新 commit summary（per-SHA 增量）
        known = self.ingestion.known_shas(repo)
        summaries = self._discover(repo, known)
        if summaries:
            self.ingestion.upsert_pending(repo, summaries)
            self.cursor.advance(repo, summaries[0].sha)  # newest-first，仅成功后推进

        # 2) 段 1：从 pending 池选出 detail-fetch 候选
        pending = self.ingestion.list_pending(repo)
        items = [
            (CommitSummary.model_validate_json(r.payload_json), _as_utc(r.discovered_at))
            for r in pending
        ]
        ranked = rank_pending(items, existing_topics, budget, now)
        for summary in ranked[: budget.max_detail_fetches]:
            detail = self._fetch_detail(repo, summary.sha)
            if is_noise(detail):
                self.ingestion.mark_skipped(repo, summary.sha)
            else:
                self.ingestion.store_detail(repo, detail)

        # 3) 段 2：append-only 分组（冻结已分配、只给新 commit 分组）+ 选出本轮提取预算
        grouped = self.ingestion.list_grouped(repo)
        groups_by_id = assign_group_ids(grouped)
        self.ingestion.mark_group_ids(
            repo,
            [(r.sha, gid) for gid, members in groups_by_id.items() for r in members],
        )
        groups = [
            [CommitDetail.model_validate_json(r.payload_json) for r in members]
            for members in groups_by_id.values()
        ]
        discovered = {
            r.sha: _as_utc(r.discovered_at)
            for members in groups_by_id.values()
            for r in members
        }
        return select_groups(groups, existing_topics, budget, discovered, now)

    def _discover(self, repo: str, known: set[str]) -> list[CommitSummary]:
        local = find_local_clone(repo, self.settings.paths.local_repos_dirs)
        if not known:
            # 首次：有界 backfill，只取最近 lookback_hours 内的 commit。
            since = (
                datetime.now(UTC)
                - timedelta(hours=self.settings.repository_discovery.lookback_hours)
            ).isoformat()
            if local is not None:
                return LocalRepoClient(repo, local).list_commits(repo, since=since)
            return self.gh.list_commits(repo, since=since)
        if local is not None:
            all_summaries = LocalRepoClient(repo, local).list_commits(repo, since=None)
        else:
            all_summaries = self.gh.list_commits_newest_first(repo)
        new: list[CommitSummary] = []
        for s in all_summaries:
            if s.sha in known:
                break
            new.append(s)
        return new

    def _fetch_detail(self, repo: str, sha: str) -> CommitDetail:
        local = find_local_clone(repo, self.settings.paths.local_repos_dirs)
        if local is not None:
            return LocalRepoClient(repo, local).commit_detail(repo, sha)
        return self.gh.commit_detail(repo, sha)
