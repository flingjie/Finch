import subprocess

import pytest

from finch.github.local_repo import (
    LocalRepoClient,
    find_local_clone,
    normalize_remote,
)


def _git(root, *args):
    proc = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr)
    return proc


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "demo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "a@example.com")
    _git(root, "config", "user.name", "A")

    (root / "a.txt").write_text("one\n")
    _git(root, "add", "a.txt")
    _git(root, "commit", "-q", "-m", "first")

    (root / "a.txt").write_text("one\ntwo\n")
    _git(root, "add", "a.txt")
    _git(root, "commit", "-q", "-m", "second")

    (root / "b.txt").write_text("b\n")
    _git(root, "add", "b.txt")
    _git(root, "rm", "-q", "a.txt")
    _git(root, "commit", "-q", "-m", "add b, delete a")

    _git(root, "mv", "b.txt", "c.txt")
    _git(root, "commit", "-q", "-m", "rename b to c")

    _git(root, "remote", "add", "origin", "git@github.com:flingjie/FDE-Gym.git")
    return root


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("git@github.com:flingjie/FDE-Gym.git", "flingjie/FDE-Gym"),
        ("https://github.com/flingjie/FDE-Gym.git", "flingjie/FDE-Gym"),
        ("ssh://git@github.com/flingjie/FDE-Gym.git", "flingjie/FDE-Gym"),
    ],
)
def test_normalize_remote(url, expected):
    assert normalize_remote(url) == expected


def test_find_local_clone(repo, tmp_path):
    assert find_local_clone("flingjie/FDE-Gym", [tmp_path]) == repo
    assert find_local_clone("other/name", [tmp_path]) is None


def test_list_commits_newest_first(repo):
    client = LocalRepoClient("flingjie/FDE-Gym", repo)
    messages = [c.message for c in client.list_commits("flingjie/FDE-Gym")]
    assert messages == ["rename b to c", "add b, delete a", "second", "first"]


def test_commit_detail_add_delete_rename(repo):
    client = LocalRepoClient("flingjie/FDE-Gym", repo)
    by_message = {c.message: c for c in client.list_commits("flingjie/FDE-Gym")}

    added_deleted = client.commit_detail(
        "flingjie/FDE-Gym", by_message["add b, delete a"].sha
    )
    statuses = {f.filename: f.status for f in added_deleted.files}
    assert statuses["b.txt"] == "added"
    assert statuses["a.txt"] == "removed"

    renamed = client.commit_detail("flingjie/FDE-Gym", by_message["rename b to c"].sha)
    assert renamed.files[0].status == "renamed"
    assert renamed.files[0].filename == "c.txt"
