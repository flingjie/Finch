"""Unit tests for Source connectors normalize + orchestrator idempotency."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from finch.sources.connectors.reddit import RedditConnector
from finch.sources.connectors.twitter import TwitterConnector
from finch.sources.connectors.v2ex import V2exConnector
from finch.sources.connectors.weixin import WeixinConnector
from finch.sources.connectors.xiaohongshu import XiaohongshuConnector
from finch.sources.models import OpenCliResult, ResultKind, Source
from finch.sources.orchestrator import DiscoveryOrchestrator
from finch.sources.store import ArtifactRepository
from finch.storage.workspace import Workspace

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "opencli"


def _result_from_fixture(name: str) -> OpenCliResult:
    raw = json.loads((FIXTURES / name).read_text())
    rows = raw if isinstance(raw, list) else [raw]
    return OpenCliResult(rows=rows, exit_code=0, kind=ResultKind.SUCCESS)


class TestNormalize:
    def test_twitter_fixture(self):
        arts = TwitterConnector().normalize(_result_from_fixture("twitter-search.json"))
        assert len(arts) >= 1
        assert arts[0].source == Source.TWITTER
        assert arts[0].author_identity.platform == "x"
        assert arts[0].artifact_id.startswith("twitter:post:")
        assert arts[0].content_fingerprint

    def test_reddit_fixture(self):
        arts = RedditConnector().normalize(_result_from_fixture("reddit-search.json"))
        assert len(arts) == 1
        assert arts[0].source == Source.REDDIT
        assert arts[0].author_identity.handle == "example_user"

    def test_v2ex_empty_fixture(self):
        arts = V2exConnector().normalize(_result_from_fixture("v2ex-hot.json"))
        assert arts == []

    def test_v2ex_row(self):
        result = OpenCliResult(
            rows=[
                {
                    "id": "123",
                    "title": "Hello",
                    "content": "world",
                    "url": "https://v2ex.com/t/123",
                    "member": {"username": "bob"},
                }
            ],
            exit_code=0,
            kind=ResultKind.SUCCESS,
        )
        arts = V2exConnector().normalize(result)
        assert len(arts) == 1
        assert arts[0].author_identity.handle == "bob"

    def test_weixin_url_import(self):
        art = WeixinConnector().normalize_url_import(
            "https://mp.weixin.qq.com/s/abc",
            title="T",
            text="body",
            author="acct",
        )
        assert art.source == Source.WEIXIN
        assert art.capture_method == "url_import"
        assert "T" in art.text

    def test_weixin_search_row(self):
        result = OpenCliResult(
            rows=[
                {
                    "rank": 1,
                    "page": 1,
                    "title": "Agent 实践复盘",
                    "url": "https://weixin.sogou.com/link?url=abc",
                    "summary": "聊聊我们在生产环境落地 Agent 的复盘",
                    "account": "京比特",
                    "publish_time": "2026-09-01",
                }
            ],
            exit_code=0,
            kind=ResultKind.SUCCESS,
        )
        arts = WeixinConnector().normalize(result)
        assert len(arts) == 1
        assert "Agent 实践复盘" in arts[0].text
        assert "复盘" in arts[0].text
        # 搜狗搜索含公众号名 → 用公众号名作为身份
        assert arts[0].author_identity.handle == "京比特"
        assert arts[0].published_at is not None

    def test_weixin_search_row_falls_back_to_url_when_no_account(self):
        result = OpenCliResult(
            rows=[
                {
                    "rank": 1,
                    "page": 1,
                    "title": "Agent 实践复盘",
                    "url": "https://weixin.sogou.com/link?url=abc",
                    "summary": "聊聊我们在生产环境落地 Agent 的复盘",
                    "publish_time": "2026-09-01",
                }
            ],
            exit_code=0,
            kind=ResultKind.SUCCESS,
        )
        arts = WeixinConnector().normalize(result)
        assert len(arts) == 1
        assert arts[0].author_identity.handle == "https://weixin.sogou.com/link?url=abc"

    def test_xiaohongshu_row(self):
        result = OpenCliResult(
            rows=[
                {
                    "id": "n1",
                    "title": "note",
                    "desc": "desc",
                    "url": "https://xiaohongshu.com/explore/n1",
                    "user": {"user_id": "u1", "nickname": "alice"},
                }
            ],
            exit_code=0,
            kind=ResultKind.SUCCESS,
        )
        arts = XiaohongshuConnector().normalize(result)
        assert len(arts) == 1
        assert arts[0].author_identity.external_id == "u1"

    def test_xiaohongshu_search_result_row_falls_back_to_url(self):
        result = OpenCliResult(
            rows=[
                {
                    "title": "AI Agent 复盘",
                    "url": "https://www.xiaohongshu.com/search_result/abc123?xsec_token=x",
                    "author": "alice",
                    "likes": "12",
                    "published_at": "2026-09-10",
                }
            ],
            exit_code=0,
            kind=ResultKind.SUCCESS,
        )
        arts = XiaohongshuConnector().normalize(result)
        assert len(arts) == 1
        assert arts[0].source_id == "abc123"
        assert arts[0].author_identity.handle == "alice"


class TestGitHubNormalizeDepth:
    def test_normalize_creator_rows(self):
        from finch.sources.connectors.github import GitHubConnector

        result = OpenCliResult(
            rows=[
                {
                    "_kind": "user",
                    "login": "alice",
                    "id": 1,
                    "name": "Alice",
                    "bio": "builder",
                    "html_url": "https://github.com/alice",
                },
                {
                    "_kind": "repo",
                    "full_name": "alice/tool",
                    "html_url": "https://github.com/alice/tool",
                    "description": "a tool",
                    "owner_login": "alice",
                    "owner_id": 1,
                },
                {
                    "_kind": "commit",
                    "sha": "abcdef1234567890",
                    "message": "feat: ship",
                    "html_url": "https://github.com/alice/tool/commit/abcdef",
                    "author_date": "2026-09-01T00:00:00+00:00",
                    "repo": "alice/tool",
                    "login": "alice",
                    "id": 1,
                },
                {
                    "_kind": "issue",
                    "id": 99,
                    "number": 3,
                    "title": "bug",
                    "body": "detail",
                    "html_url": "https://github.com/alice/tool/issues/3",
                    "login": "alice",
                    "author_id": 1,
                },
                {
                    "_kind": "release",
                    "id": 7,
                    "tag_name": "v1.0",
                    "name": "v1.0",
                    "body": "first",
                    "html_url": "https://github.com/alice/tool/releases/tag/v1.0",
                    "repo": "alice/tool",
                    "login": "alice",
                    "author_id": 1,
                },
            ],
            exit_code=0,
            kind=ResultKind.SUCCESS,
        )
        arts = GitHubConnector().normalize(result)
        types = {a.source_type for a in arts}
        assert types >= {"user", "repo", "commit", "issue", "release"}
        assert len(arts) >= 5
        assert all(a.author_identity.handle == "alice" for a in arts)


class TestCapabilityDrivenPlan:
    def test_twitter_skips_search_when_command_missing(self):
        from datetime import UTC, datetime

        from finch.sources.connectors import DiscoveryContext
        from finch.sources.models import OpenCliCapabilities

        caps = OpenCliCapabilities(
            snapshot_id="c1",
            captured_at=datetime.now(UTC),
            surfaces={"twitter": ["profile", "whoami"]},
        )
        reqs = TwitterConnector().plan(
            DiscoveryContext(queries=["q"]), capabilities=caps
        )
        assert reqs == []

    def test_twitter_uses_available_search(self):
        from datetime import UTC, datetime

        from finch.sources.connectors import DiscoveryContext
        from finch.sources.models import OpenCliCapabilities

        caps = OpenCliCapabilities(
            snapshot_id="c1",
            captured_at=datetime.now(UTC),
            surfaces={"twitter": ["search", "thread"]},
        )
        reqs = TwitterConnector().plan(
            DiscoveryContext(queries=["q"]), capabilities=caps
        )
        assert len(reqs) == 1
        assert reqs[0].command == "search"

    def test_v2ex_hot_when_no_queries(self):
        from datetime import UTC, datetime

        from finch.sources.connectors import DiscoveryContext
        from finch.sources.models import OpenCliCapabilities

        caps = OpenCliCapabilities(
            snapshot_id="c1",
            captured_at=datetime.now(UTC),
            surfaces={"v2ex": ["hot"]},
        )
        reqs = V2exConnector().plan(DiscoveryContext(mode="hot"), capabilities=caps)
        assert len(reqs) == 1
        assert reqs[0].command == "hot"

    def test_v2ex_query_mode_falls_back_to_hot_when_search_is_missing(self):
        from finch.sources.connectors import DiscoveryContext
        from finch.sources.models import OpenCliCapabilities

        caps = OpenCliCapabilities(
            snapshot_id="c1",
            captured_at=datetime.now(UTC),
            surfaces={"v2ex": ["hot"]},
        )
        reqs = V2exConnector().plan(
            DiscoveryContext(queries=["AI Agent"]), capabilities=caps
        )
        assert len(reqs) == 1
        assert reqs[0].command == "hot"

    def test_v2ex_query_mode_prefers_search_when_available(self):
        from finch.sources.connectors import DiscoveryContext
        from finch.sources.models import OpenCliCapabilities

        caps = OpenCliCapabilities(
            snapshot_id="c1",
            captured_at=datetime.now(UTC),
            surfaces={"v2ex": ["search", "hot"]},
        )
        reqs = V2exConnector().plan(
            DiscoveryContext(queries=["AI Agent"]), capabilities=caps
        )
        assert len(reqs) == 1
        assert reqs[0].command == "search"
        assert reqs[0].args[0] == "AI Agent"

    def test_weixin_query_mode_emits_search(self):
        from finch.sources.connectors import DiscoveryContext
        from finch.sources.models import OpenCliCapabilities

        caps = OpenCliCapabilities(
            snapshot_id="c1",
            captured_at=datetime.now(UTC),
            surfaces={"weixin": ["search"]},
        )
        reqs = WeixinConnector().plan(
            DiscoveryContext(queries=["AI Agent 实践"]), capabilities=caps
        )
        assert len(reqs) == 1
        assert reqs[0].surface == "weixin"
        assert reqs[0].command == "search"
        assert reqs[0].args[0] == "AI Agent 实践"


class TestArtifactIdempotency:
    def test_upsert_dedupes(self, tmp_path: Path):
        ws = Workspace(tmp_path)
        ws.ensure()
        repo = ArtifactRepository(ws)
        arts = TwitterConnector().normalize(_result_from_fixture("twitter-search.json"))
        assert arts
        a1, created1 = repo.upsert(arts[0])
        a2, created2 = repo.upsert(arts[0])
        assert created1 is True
        assert created2 is False
        assert a1.artifact_id == a2.artifact_id


class TestOrchestratorIsolation:
    def test_one_source_failure_does_not_block(self, tmp_path: Path):
        ws = Workspace(tmp_path)
        ws.ensure()

        def fake_run(argv, timeout):
            if argv[:2] == ["opencli", "list"]:
                return {
                    "ok": True,
                    "exit_code": 0,
                    "stdout": json.dumps(
                        [
                            {"site": "twitter", "commands": ["search"]},
                            {"site": "reddit", "commands": ["search"]},
                        ]
                    ),
                    "stderr": "",
                }
            if "twitter" in argv:
                return {
                    "ok": False,
                    "exit_code": 77,
                    "stdout": "",
                    "stderr": "not logged in",
                }
            if "reddit" in argv:
                return {
                    "ok": True,
                    "exit_code": 0,
                    "stdout": (FIXTURES / "reddit-search.json").read_text(),
                    "stderr": "",
                }
            return {"ok": True, "exit_code": 0, "stdout": "[]", "stderr": ""}

        from finch.sources.connectors import DiscoveryContext
        from finch.sources.opencli_gateway import OpenCliGateway

        orch = DiscoveryOrchestrator(ws, gateway=OpenCliGateway(run_fn=fake_run))
        from finch.sources.models import Source as S

        results = orch.sync_all(
            sources=[S.TWITTER, S.REDDIT],
            context_by_source={
                S.TWITTER: DiscoveryContext(queries=["q"]),
                S.REDDIT: DiscoveryContext(queries=["q"]),
            },
        )
        by = {r.source: r for r in results}
        assert by[S.TWITTER].status.value == "AUTH_REQUIRED"
        assert by[S.REDDIT].normalized_count >= 1
