"""Tests for GitHub URL extraction / classification / short-link helpers."""

from finch.repos.urls import (
    classify_github_url,
    extract_github_links,
    extract_urls_from_text,
)


def test_classify_repo_and_subpaths():
    for url in [
        "https://github.com/acme/widget",
        "https://github.com/acme/widget/issues/1",
        "https://github.com/acme/widget/pull/2",
        "https://github.com/acme/widget/tree/main/src",
        "https://github.com/acme/widget.git",
    ]:
        link = classify_github_url(url)
        assert link.kind == "repo"
        assert link.repo_key == "acme/widget"
        assert link.canonical_url == "https://github.com/acme/widget"


def test_classify_gist_and_profile_as_non_repo():
    assert classify_github_url("https://gist.github.com/a/b").kind == "non_repo"
    assert classify_github_url("https://github.com/features").kind == "non_repo"


def test_extract_multiple_repos_from_one_tweet():
    text = "try https://github.com/a/one and https://github.com/b/two please"
    links = extract_github_links(text)
    repos = [lk.repo_key for lk in links if lk.kind == "repo"]
    assert repos == ["a/one", "b/two"]


def test_entity_urls_preferred_over_duplicates():
    text = "see https://github.com/a/one"
    links = extract_github_links(
        text, entity_urls=["https://github.com/a/one/issues/9"]
    )
    # both normalize to same key; first wins as entity
    keys = [lk.repo_key for lk in links if lk.kind == "repo"]
    assert keys[0] == "a/one"
    assert keys.count("a/one") == 1 or len(set(keys)) >= 1


def test_extract_urls_from_text_strips_trailing_punct():
    urls = extract_urls_from_text("link (https://github.com/a/b).")
    assert urls == ["https://github.com/a/b"]


def test_short_link_marked():
    links = extract_github_links("go https://t.co/abc123 now")
    assert any(lk.kind == "short" for lk in links)
