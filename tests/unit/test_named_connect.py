from datetime import UTC, datetime

import pytest

from finch.engagement.named import github_repo_to_post, parse_named_target
from finch.github.models import PublicRepo


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

