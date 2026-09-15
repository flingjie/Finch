from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from finch.codex.runner import CodexRunner
from finch.engagement.models import (
    ConversationScore,
    ExternalPost,
    InteractionAction,
    InteractionProposal,
)
from finch.engagement.named import (
    NamedTarget,
    connect_named,
    github_repo_to_post,
    parse_named_target,
)
from finch.engagement.scoring import ScoredPost
from finch.github.gh_client import GhError
from finch.github.models import PublicRepo
from finch.peers.service import peer_id_for
from finch.settings import Paths, Settings
from finch.storage.repositories import (
    DiscoverySnapshotRepository,
    InteractionRepository,
    OpportunityRepository,
    PeerRepository,
)
from finch.storage.workspace import Workspace
from finch.twitter.models import Tweet


def test_parse_x_handle_and_profile_url():
    bare = parse_named_target("x", "iFurySt")
    assert bare.platform == "x"
    assert bare.handle == "iFurySt"
    assert bare.content_url is None
    assert bare.identity_url == "https://x.com/iFurySt"

    at = parse_named_target("x", "@iFurySt")
    assert at.handle == "iFurySt"

    profile = parse_named_target("x", "https://x.com/iFurySt")
    assert profile.handle == "iFurySt"
    assert profile.content_url is None

    twitter = parse_named_target("x", "https://twitter.com/iFurySt")
    assert twitter.handle == "iFurySt"


def test_parse_x_status_url():
    t = parse_named_target("x", "https://x.com/iFurySt/status/123456")
    assert t.handle == "iFurySt"
    assert t.content_url == "https://x.com/iFurySt/status/123456"


def test_parse_github_handle_user_and_repo():
    user = parse_named_target("github", "iFurySt")
    assert user.platform == "github"
    assert user.handle == "ifuryst"
    assert user.content_url is None
    assert user.identity_url == "https://github.com/ifuryst"

    profile = parse_named_target("github", "https://github.com/iFurySt")
    assert profile.handle == "ifuryst"
    assert profile.content_url is None

    repo = parse_named_target("github", "https://github.com/iFurySt/Finch")
    assert repo.handle == "ifuryst"
    assert repo.content_url == "https://github.com/iFurySt/Finch"


def test_parse_rejects_wrong_host_and_empty():
    with pytest.raises(ValueError):
        parse_named_target("x", "https://github.com/iFurySt")
    with pytest.raises(ValueError):
        parse_named_target("github", "https://x.com/iFurySt")
    with pytest.raises(ValueError):
        parse_named_target("x", "")
    with pytest.raises(ValueError):
        parse_named_target("github", "https://github.com/iFurySt/Finch/issues/1")


def test_github_repo_to_post_skips_missing_time():
    assert github_repo_to_post(
        PublicRepo(
            name_with_owner="a/b",
            url="https://github.com/a/b",
            owner_login="a",
            description="hi",
        )
    ) is None


def test_github_repo_to_post_maps_external_post():
    post = github_repo_to_post(
        PublicRepo(
            name_with_owner="iFurySt/keep",
            url="https://github.com/iFurySt/keep",
            owner_login="iFurySt",
            description="agent eval harness",
            pushed_at=datetime(2026, 9, 4, 6, 5, 12, tzinfo=UTC),
        )
    )
    assert post is not None
    assert post.platform == "github"
    assert post.id == "iFurySt/keep"
    assert post.author_id == "ifuryst"
    assert post.content == "agent eval harness"


# ---- connect_named orchestration -------------------------------------------------


def _settings(tmp_path) -> Settings:
    return Settings(paths=Paths(var_dir=tmp_path))


def _x_post() -> ExternalPost:
    return ExternalPost(
        id="99",
        platform="x",
        url="https://x.com/iFurySt/status/99",
        author_id="iFurySt",
        author_name="iFurySt",
        content="shipping an agent eval harness with replay diffs",
        published_at=datetime.now(UTC),
        matched_topics=["named"],
    )


def _proposal() -> InteractionProposal:
    return InteractionProposal(
        id="x:99:draft_reply",
        post=_x_post(),
        score=ConversationScore(
            relevance=0.8,
            novelty=0.7,
            discussability=0.7,
            practical_evidence=0.6,
            relationship_value=0.4,
            total=0.7,
            reasons=["overlap"],
        ),
        action=InteractionAction.DRAFT_REPLY,
        approval_required=True,
        outline="问 replay 如何验证补偿",
        value_added="对准 eval harness",
        peer_id=peer_id_for("x", "iFurySt"),
        generation_key=f"{peer_id_for('x', 'iFurySt')}:99:draft_reply:2",
    )


def _gh_post() -> ExternalPost:
    return ExternalPost(
        id="ifuryst/keep",
        platform="github",
        url="https://github.com/iFurySt/keep",
        author_id="ifuryst",
        author_name="iFurySt",
        content="agent eval harness",
        published_at=datetime.now(UTC),
        matched_topics=["named"],
    )


def _gh_proposal() -> InteractionProposal:
    return InteractionProposal(
        id="github:ifuryst_keep:draft_reply",
        post=_gh_post(),
        score=ConversationScore(
            relevance=0.8,
            novelty=0.7,
            discussability=0.7,
            practical_evidence=0.6,
            relationship_value=0.4,
            total=0.7,
            reasons=["overlap"],
        ),
        action=InteractionAction.DRAFT_REPLY,
        approval_required=True,
        outline="讨论 keep 的 eval harness 如何回放",
        value_added="对准公开仓库的评测方法",
        peer_id=peer_id_for("github", "ifuryst"),
        generation_key=f"{peer_id_for('github', 'ifuryst')}:ifuryst/keep:draft_reply:2",
    )


class _FakeOpenCli:
    def __init__(self, *, profile=True, tweets=None, search=None, thread=None):
        self._profile = profile
        self._tweets = tweets if tweets is not None else [
            Tweet(
                id="99",
                author="iFurySt",
                text="shipping an agent eval harness with replay diffs",
                created_at="Wed Sep 02 06:05:25 +0000 2026",
                url="https://x.com/iFurySt/status/99",
            )
        ]
        self._search = search if search is not None else []
        self._thread = thread

    def profile(self, username: str):
        if not self._profile:
            return None
        return Tweet(id="u", author=username, text="", url=f"https://x.com/{username}")

    def tweets(self, username: str, *, limit: int = 20):
        return list(self._tweets)

    def search(self, query: str, *, product: str = "top", limit: int = 20):
        return list(self._search)

    def thread(self, url: str, *, limit: int = 50):
        return list(self._thread or [])


def test_connect_named_x_persists_peer_and_proposal(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    candidate = _proposal()
    monkeypatch.setattr(
        "finch.engagement.named.score_posts",
        lambda *a, **k: [ScoredPost(post=_x_post(), score=candidate.score)],
    )
    monkeypatch.setattr(
        "finch.engagement.named.rank_candidates",
        lambda scored, **k: scored,
    )
    monkeypatch.setattr(
        "finch.engagement.named.generate_proposals",
        lambda *a, **k: [candidate],
    )
    target = NamedTarget(
        platform="x",
        handle="iFurySt",
        identity_url="https://x.com/iFurySt",
    )
    result = connect_named(
        target=target,
        ws=ws,
        settings=settings,
        runner=CodexRunner(),
        opencli=_FakeOpenCli(),
    )
    assert result.status == "ok"
    peer = PeerRepository(ws).get(peer_id_for("x", "iFurySt"))
    assert peer is not None
    assert InteractionRepository(ws).get(candidate.id) is not None
    assert OpportunityRepository(ws).get(result.opportunity.id) is not None
    assert DiscoverySnapshotRepository(ws).latest() is None


def test_connect_named_profile_missing_does_not_persist(tmp_path):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    target = NamedTarget(platform="x", handle="nobody", identity_url="https://x.com/nobody")
    result = connect_named(
        target=target,
        ws=ws,
        settings=settings,
        runner=CodexRunner(),
        opencli=_FakeOpenCli(profile=False, tweets=[]),
    )
    assert result.status == "failed"
    assert PeerRepository(ws).list_all() == []


def test_connect_named_no_posts_persists_peer_without_proposal(tmp_path):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    target = NamedTarget(platform="x", handle="iFurySt", identity_url="https://x.com/iFurySt")
    result = connect_named(
        target=target,
        ws=ws,
        settings=settings,
        runner=CodexRunner(),
        opencli=_FakeOpenCli(tweets=[], search=[]),
    )
    assert result.status == "no_reply"
    assert "暂不回复" in result.message
    peer = PeerRepository(ws).get(peer_id_for("x", "iFurySt"))
    assert peer is not None
    assert peer.evidence_status.value == "pending_review"
    assert InteractionRepository(ws).list_all() == []
    assert OpportunityRepository(ws).list_all() == []


def test_connect_named_github_persists_github_proposal(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    candidate = _gh_proposal()
    monkeypatch.setattr(
        "finch.engagement.named.score_posts",
        lambda *a, **k: [ScoredPost(post=_gh_post(), score=candidate.score)],
    )
    monkeypatch.setattr(
        "finch.engagement.named.rank_candidates",
        lambda scored, **k: scored,
    )
    monkeypatch.setattr(
        "finch.engagement.named.generate_proposals",
        lambda *a, **k: [candidate],
    )
    gh = SimpleNamespace(
        user=lambda login: {"login": login, "html_url": f"https://github.com/{login}"},
        list_public_repos=lambda login, limit=20: [
            PublicRepo(
                name_with_owner="iFurySt/keep",
                url="https://github.com/iFurySt/keep",
                owner_login="iFurySt",
                description="agent eval harness",
                pushed_at=datetime(2026, 9, 4, 6, 5, 12, tzinfo=UTC),
            )
        ],
    )
    target = NamedTarget(
        platform="github",
        handle="ifuryst",
        identity_url="https://github.com/ifuryst",
    )
    result = connect_named(
        target=target,
        ws=ws,
        settings=settings,
        runner=CodexRunner(),
        gh=gh,
    )
    assert result.status == "ok"
    peer = PeerRepository(ws).get(peer_id_for("github", "ifuryst"))
    assert peer is not None
    saved = InteractionRepository(ws).get(candidate.id)
    assert saved is not None
    assert saved.post.platform == "github"


def test_connect_named_x_peer_id_derived_from_post_author(tmp_path, monkeypatch):
    """handle 大小写与帖子作者不一致时，peer id 以抓到的帖子作者为准（不产生孤儿档案）。"""
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    candidate = _proposal()  # peer_id / post 均基于 "iFurySt"
    monkeypatch.setattr(
        "finch.engagement.named.score_posts",
        lambda *a, **k: [ScoredPost(post=_x_post(), score=candidate.score)],
    )
    monkeypatch.setattr(
        "finch.engagement.named.rank_candidates",
        lambda scored, **k: scored,
    )
    monkeypatch.setattr(
        "finch.engagement.named.generate_proposals",
        lambda *a, **k: [candidate],
    )
    # 用户输入小写 handle，但 profile 确认存在、帖子作者是 "iFurySt"。
    target = NamedTarget(
        platform="x",
        handle="ifuryst",
        identity_url="https://x.com/ifuryst",
    )
    result = connect_named(
        target=target,
        ws=ws,
        settings=settings,
        runner=CodexRunner(),
        opencli=_FakeOpenCli(),  # tweets author == "iFurySt"
    )
    assert result.status == "ok"
    author_id = peer_id_for("x", "iFurySt")
    handle_id = peer_id_for("x", "ifuryst")
    assert result.peer is not None
    assert result.peer.id == author_id
    assert PeerRepository(ws).get(author_id) is not None
    # 不应为用户输入的小写 handle 留下孤儿档案。
    if handle_id != author_id:
        assert PeerRepository(ws).get(handle_id) is None
    assert [p.id for p in PeerRepository(ws).list_all()] == [author_id]


def test_connect_named_github_repo_listing_error_persists_peer(tmp_path):
    """list_public_repos 抛 GhError 时落 peer 并返回暂不回复，不抛栈。"""
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()

    def _raise_repos(login, limit=20):
        raise GhError("boom")

    gh = SimpleNamespace(
        user=lambda login: {"login": login, "html_url": f"https://github.com/{login}"},
        list_public_repos=_raise_repos,
    )
    target = NamedTarget(
        platform="github",
        handle="ifuryst",
        identity_url="https://github.com/ifuryst",
    )
    result = connect_named(
        target=target,
        ws=ws,
        settings=settings,
        runner=CodexRunner(),
        gh=gh,
    )
    assert result.status == "no_reply"
    assert "暂不回复" in result.message
    peer = PeerRepository(ws).get(peer_id_for("github", "ifuryst"))
    assert peer is not None
    assert peer.evidence_status.value == "pending_review"
    assert InteractionRepository(ws).list_all() == []


def test_connect_named_github_user_missing_does_not_persist(tmp_path):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()

    def _raise(login):
        raise GhError("not found")

    gh = SimpleNamespace(user=_raise, list_public_repos=lambda login, limit=20: [])
    target = NamedTarget(
        platform="github",
        handle="nobody",
        identity_url="https://github.com/nobody",
    )
    result = connect_named(
        target=target,
        ws=ws,
        settings=settings,
        runner=CodexRunner(),
        gh=gh,
    )
    assert result.status == "failed"
    assert PeerRepository(ws).list_all() == []

