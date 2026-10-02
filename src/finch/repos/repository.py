"""repo-discovery 文件持久化：var/repos/{run_id}/。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from finch.repos.models import DiscoveryRun, RankingSnapshot, RepoRecord, TweetRecord
from finch.storage.workspace import Workspace


class RepoDiscoveryRepository:
    """一次运行的 tweets / repos / run / ranking 读写。"""

    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def _run_dir(self, run_id: str) -> Path:
        return self.ws.dir(f"repos/{self.ws.safe_filename(run_id)}")

    def save_run(self, run: DiscoveryRun) -> None:
        run.updated_at = datetime.now(UTC)
        self.ws.write_yaml(self._run_dir(run.run_id) / "run.yaml", run)

    def get_run(self, run_id: str) -> DiscoveryRun | None:
        return self.ws.read_yaml(self._run_dir(run_id) / "run.yaml", DiscoveryRun)

    def list_run_ids(self) -> list[str]:
        root = self.ws.dir("repos")
        return sorted(p.name for p in root.iterdir() if p.is_dir())

    def save_tweet(self, run_id: str, tweet: TweetRecord) -> None:
        path = self._run_dir(run_id) / "tweets" / f"{self.ws.safe_filename(tweet.tweet_id)}.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        self.ws.write_yaml(path, tweet)

    def get_tweet(self, run_id: str, tweet_id: str) -> TweetRecord | None:
        path = self._run_dir(run_id) / "tweets" / f"{self.ws.safe_filename(tweet_id)}.yaml"
        return self.ws.read_yaml(path, TweetRecord)

    def list_tweets(self, run_id: str) -> list[TweetRecord]:
        d = self._run_dir(run_id) / "tweets"
        if not d.exists():
            return []
        out: list[TweetRecord] = []
        for path in sorted(d.glob("*.yaml")):
            obj = self.ws.read_yaml(path, TweetRecord)
            if obj is not None:
                out.append(obj)
        return out

    def save_repo(self, run_id: str, repo: RepoRecord) -> None:
        path = self._run_dir(run_id) / "repos" / f"{self.ws.safe_filename(repo.key)}.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        self.ws.write_yaml(path, repo)

    def get_repo(self, run_id: str, key: str) -> RepoRecord | None:
        path = self._run_dir(run_id) / "repos" / f"{self.ws.safe_filename(key)}.yaml"
        return self.ws.read_yaml(path, RepoRecord)

    def list_repos(self, run_id: str) -> list[RepoRecord]:
        d = self._run_dir(run_id) / "repos"
        if not d.exists():
            return []
        out: list[RepoRecord] = []
        for path in sorted(d.glob("*.yaml")):
            obj = self.ws.read_yaml(path, RepoRecord)
            if obj is not None:
                out.append(obj)
        return out

    def save_ranking(self, snapshot: RankingSnapshot) -> None:
        name = f"ranking_{snapshot.sort.value}_{snapshot.topic_filter}.yaml"
        self.ws.write_yaml(self._run_dir(snapshot.run_id) / name, snapshot)

    def get_ranking(
        self, run_id: str, *, sort: str = "x_heat", topic: str = "all"
    ) -> RankingSnapshot | None:
        name = f"ranking_{sort}_{topic}.yaml"
        return self.ws.read_yaml(self._run_dir(run_id) / name, RankingSnapshot)

    def try_acquire_lock(self, run_id: str, *, ttl_seconds: int = 3600) -> bool:
        """简单锁文件：存在且未过期 → False；否则写入。"""
        lock = self._run_dir(run_id) / "lock.json"
        now = datetime.now(UTC)
        if lock.exists():
            try:
                data = json.loads(lock.read_text(encoding="utf-8"))
                acquired = datetime.fromisoformat(data["acquired_at"])
                if (now - acquired).total_seconds() < ttl_seconds:
                    return False
            except (json.JSONDecodeError, KeyError, ValueError):
                pass
        self.ws.atomic_write(
            lock,
            json.dumps({"acquired_at": now.isoformat()}, ensure_ascii=False),
        )
        return True

    def release_lock(self, run_id: str) -> None:
        lock = self._run_dir(run_id) / "lock.json"
        if lock.exists():
            lock.unlink(missing_ok=True)
