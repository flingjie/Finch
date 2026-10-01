"""Unit tests for the `finch profile` CLI (show / confirm / revoke / add)."""

from typer.testing import CliRunner

from finch import cli
from finch.cli import app
from finch.profile.models import (
    PracticeEvidenceStatus,
    PracticeItem,
    PracticeProfile,
    load_practice_profile,
    save_practice_profile,
)
from finch.settings import Paths, Settings


def _settings(tmp_path) -> Settings:
    return Settings(
        paths=Paths(var_dir=tmp_path, practice_profile_path=tmp_path / "practice.yaml")
    )


def _seed(settings: Settings) -> None:
    save_practice_profile(
        PracticeProfile(
            items=[
                PracticeItem(
                    id="agent-100-days", domain="agent", claim="100 天路径",
                    evidence_refs=["https://github.com/flingjie/Agent-100-Days"],
                    status=PracticeEvidenceStatus.SOURCED, confirmed=False,
                )
            ]
        ),
        settings.paths.practice_profile_path,
    )


def test_profile_show_lists_items_with_confirm_flag(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    _seed(settings)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    r = CliRunner().invoke(app, ["profile", "show"])
    assert r.exit_code == 0, r.output
    assert "agent-100-days" in r.output
    assert "未确认" in r.output


def test_profile_show_empty(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    r = CliRunner().invoke(app, ["profile", "show"])
    assert r.exit_code == 0
    assert "finch profile init" in r.output


def test_profile_confirm_and_revoke(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    _seed(settings)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    r = CliRunner().invoke(app, ["profile", "confirm", "agent-100-days"])
    assert r.exit_code == 0, r.output
    path = settings.paths.practice_profile_path
    assert load_practice_profile(path).get("agent-100-days").confirmed
    r = CliRunner().invoke(app, ["profile", "revoke", "agent-100-days"])
    assert r.exit_code == 0, r.output
    assert not load_practice_profile(path).get("agent-100-days").confirmed


def test_profile_confirm_unknown_id_lists_available(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    _seed(settings)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    r = CliRunner().invoke(app, ["profile", "confirm", "nope"])
    assert r.exit_code == 1
    assert "agent-100-days" in r.output


def test_profile_add_without_ref_is_author_stated_and_unconfirmed(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    r = CliRunner().invoke(
        app,
        ["profile", "add", "--id", "pharmacy", "--domain", "pharmacy",
         "--claim", "药学本科", "--offer", "跨领域类比", "--boundaries", "没做过临床"],
    )
    assert r.exit_code == 0, r.output
    item = load_practice_profile(settings.paths.practice_profile_path).get("pharmacy")
    assert item.status == PracticeEvidenceStatus.AUTHOR_STATED
    assert item.confirmed is False
    assert item.can_offer == ["跨领域类比"]


def test_profile_add_with_ref_is_sourced(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    r = CliRunner().invoke(
        app,
        ["profile", "add", "--id", "book", "--domain", "publishing",
         "--claim", "出版《自学区块链》", "--ref", "https://example.com/book"],
    )
    assert r.exit_code == 0, r.output
    item = load_practice_profile(settings.paths.practice_profile_path).get("book")
    assert item.status == PracticeEvidenceStatus.SOURCED
    assert item.evidence_refs == ["https://example.com/book"]


def test_profile_add_duplicate_id_fails(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    _seed(settings)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    r = CliRunner().invoke(
        app, ["profile", "add", "--id", "agent-100-days", "--domain", "d", "--claim", "c"]
    )
    assert r.exit_code == 1
    assert "already exists" in r.output


from finch.github.gh_client import GhError  # noqa: E402
from finch.profile import bootstrap as bootstrap_mod  # noqa: E402
from finch.profile.models import PracticeItem as _PI  # noqa: E402


def _draft(repo: str) -> _PI:
    slug = repo.split("/")[-1].lower()
    return _PI(
        id=slug, domain="d", claim=f"from {repo}",
        evidence_refs=[f"https://github.com/{repo}"],
        status=PracticeEvidenceStatus.SOURCED, confirmed=False,
    )


def _patch_init_deps(monkeypatch, *, readmes: dict[str, str]):
    class FakeGh:
        def readme(self, repo):
            if repo not in readmes:
                raise GhError("404")
            return readmes[repo]

    monkeypatch.setattr(cli, "GhClient", lambda: FakeGh())
    monkeypatch.setattr(cli, "create_runner", lambda llm, node: object())
    monkeypatch.setattr(
        bootstrap_mod, "draft_items_from_readme",
        lambda runner, *, repo, readme: [_draft(repo)] if readme else [],
    )


def test_profile_init_drafts_from_repositories_and_skips_failures(monkeypatch, tmp_path):
    settings = Settings(
        paths=Paths(var_dir=tmp_path, practice_profile_path=tmp_path / "p.yaml"),
        repositories=["o/Alpha", "o/Beta"],
    )
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    _patch_init_deps(monkeypatch, readmes={"o/Alpha": "# a"})
    r = CliRunner().invoke(app, ["profile", "init"])
    assert r.exit_code == 0, r.output
    p = load_practice_profile(settings.paths.practice_profile_path)
    assert [i.id for i in p.items] == ["alpha"]
    assert p.items[0].confirmed is False
    assert "o/Beta" in r.output and "skipped" in r.output
    assert "finch profile add" in r.output


def test_profile_init_extra_repo_and_idempotent(monkeypatch, tmp_path):
    settings = Settings(
        paths=Paths(var_dir=tmp_path, practice_profile_path=tmp_path / "p.yaml"),
        repositories=["o/Alpha"],
    )
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    _patch_init_deps(monkeypatch, readmes={"o/Alpha": "# a", "o/Gamma": "# g"})
    r1 = CliRunner().invoke(app, ["profile", "init", "--repo", "o/Gamma"])
    assert r1.exit_code == 0, r1.output
    # confirm alpha, re-run: alpha must stay confirmed, nothing duplicated
    CliRunner().invoke(app, ["profile", "confirm", "alpha"])
    r2 = CliRunner().invoke(app, ["profile", "init", "--repo", "o/Gamma"])
    assert r2.exit_code == 0, r2.output
    p = load_practice_profile(settings.paths.practice_profile_path)
    assert sorted(i.id for i in p.items) == ["alpha", "gamma"]
    assert p.get("alpha").confirmed is True


def test_profile_init_all_failed_writes_nothing(monkeypatch, tmp_path):
    settings = Settings(
        paths=Paths(var_dir=tmp_path, practice_profile_path=tmp_path / "p.yaml"),
        repositories=["o/Alpha"],
    )
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    _patch_init_deps(monkeypatch, readmes={})
    r = CliRunner().invoke(app, ["profile", "init"])
    assert r.exit_code == 1
    assert not settings.paths.practice_profile_path.exists()
