"""Unit tests for finch ideas commit CLI command（Skill 架构 Step 2 Task 3）。"""

import json

from typer.testing import CliRunner

from finch import cli
from finch.cli import app
from finch.content.jobs import (
    AuthorPosition,
    ContentJob,
    ContentJobStatus,
)
from finch.content.models import RecommendedFormat
from finch.conversations.models import ConversationThread
from finch.ideas.models import (
    IdeaBoundaries,
    IdeaCandidate,
    IdeaGenerator,
    SourceRef,
)
from finch.ideas.service import IdeaService
from finch.settings import Paths, Settings
from finch.storage.repositories import ContentJobRepository
from finch.storage.workspace import Workspace

CORE_POINT = "make the orchestrator a deterministic graph"
COMMIT_URL = "https://github.com/acme/proj/commit/" + "a" * 40


def _settings(tmp_path, repositories):
    return Settings(paths=Paths(var_dir=tmp_path), repositories=repositories)


def _candidate(core_point: str = CORE_POINT) -> IdeaCandidate:
    return IdeaCandidate(
        id="idea_abc12345",
        origin="practice",
        core_point=core_point,
        reader_problem="orchestrator was hard to rerun",
        why_worth_saying="failures can now be replayed",
        author_position=AuthorPosition(
            claim="failures can now be replayed",
            decision=core_point,
            tradeoff="orchestrator was hard to rerun",
        ),
        source_refs=[
            SourceRef(type="commit", ref=COMMIT_URL, summary="feat: node-ize orchestrator")
        ],
        boundaries=IdeaBoundaries(),
        recommended_format=RecommendedFormat.SHORT_POST,
        generator=IdeaGenerator(skill="commit-to-idea", version="1.0.0"),
    )


class _FakeCommitReader:
    def __init__(self, gh, repo):
        self.gh = gh
        self.repo = repo


class _FakeExtractor:
    def __init__(self, runner, *, settings=None, cache_path=None):
        self.runner = runner


class _FakeCommitService:
    def __init__(self, reader, extractor):
        self.reader = reader
        self.extractor = extractor

    def to_ideas(self, commits, *, repo, repo_is_private=False):
        return [_candidate()]


def _patch_cli(monkeypatch, settings, record):
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "GhClient", lambda: object())
    monkeypatch.setattr(cli, "CommitReader", _FakeCommitReader)
    monkeypatch.setattr(cli, "Extractor", _FakeExtractor)
    monkeypatch.setattr(cli, "CommitService", _FakeCommitService)

    def _load_commit_details(repo, gh, *, local_dirs, since=None, workers=6):
        record.append((repo, since))
        return []

    monkeypatch.setattr(cli, "load_commit_details", _load_commit_details)


def test_ideas_commit_non_json_output(monkeypatch, tmp_path):
    settings = _settings(tmp_path, ["acme/proj"])
    _patch_cli(monkeypatch, settings, [])

    r = CliRunner().invoke(app, ["ideas", "commit"])
    assert r.exit_code == 0, r.output
    assert "可写观点: make the orchestrator a deterministic graph" in r.output
    assert "为什么值得写: failures can now be replayed" in r.output
    assert "适合形式: 短帖" in r.output
    assert "核心主张:" not in r.output
    assert "读者问题:" not in r.output
    assert "全部 proposed" not in r.output
    assert "立场:" not in r.output
    assert "推荐体裁:" not in r.output
    assert "来源:" not in r.output
    assert "id\tstatus" not in r.output
    assert "其余：" not in r.output
    assert "<id>" not in r.output
    assert "下一步:" not in r.output
    idea_id = ContentJobRepository(Workspace(settings.paths.var_dir)).list_jobs()[0].id
    assert f"uv run finch ideas confirm {idea_id}" in r.output
    assert f"uv run finch ideas skip {idea_id} --reason ..." in r.output


def test_ideas_commit_caps_cards_and_points_to_list(monkeypatch, tmp_path):
    import re

    class _ManyCommitService:
        def __init__(self, reader, extractor):
            self.reader = reader
            self.extractor = extractor

        def to_ideas(self, commits, *, repo, repo_is_private=False):
            return [_candidate(f"point {i}") for i in range(8)]

    settings = _settings(tmp_path, ["acme/proj"])
    _patch_cli(monkeypatch, settings, [])
    monkeypatch.setattr(cli, "CommitService", _ManyCommitService)

    r = CliRunner().invoke(app, ["ideas", "commit"])
    assert r.exit_code == 0, r.output
    assert r.output.count("可写观点:") == 6
    assert "全部 proposed" not in r.output
    assert "point 0" in r.output
    assert "point 5" in r.output
    assert "point 6" not in r.output
    assert "共 8 个候选，以上 6 个。" in r.output
    assert "uv run finch ideas list" in r.output
    assert "<id>" not in r.output
    assert "下一步:" not in r.output
    confirms = re.findall(r"uv run finch ideas confirm (idea_\w+)", r.output)
    assert len(confirms) == 6
    assert len(set(confirms)) == 6
    assert f"uv run finch ideas skip {confirms[0]} --reason ..." in r.output
    assert r.output.count("uv run finch ideas skip") == 1


def test_ideas_commit_persists_and_outputs_json(monkeypatch, tmp_path):
    settings = _settings(tmp_path, ["acme/proj"])
    ws = Workspace(settings.paths.var_dir)
    _patch_cli(monkeypatch, settings, [])

    r = CliRunner().invoke(app, ["ideas", "commit", "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert isinstance(payload, list)
    assert len(payload) == 1
    assert payload[0]["id"].startswith("idea_")
    assert payload[0]["origin"] == "practice"
    assert payload[0]["core_point"] == CORE_POINT
    assert payload[0]["reader_problem"] == "orchestrator was hard to rerun"
    assert payload[0]["why_now"] == "failures can now be replayed"
    assert payload[0]["recommended_format"] == "short_post"
    assert payload[0]["status"] == "proposed"
    assert payload[0]["generation_key"]

    jobs = ContentJobRepository(ws).list_jobs()
    assert len(jobs) == 1
    assert jobs[0].origin == "practice"
    assert jobs[0].status.value == "proposed"
    assert jobs[0].core_message == CORE_POINT


def test_ideas_commit_defaults_repo_from_settings(monkeypatch, tmp_path):
    settings = _settings(tmp_path, ["acme/proj"])
    record = []
    _patch_cli(monkeypatch, settings, record)
    monkeypatch.chdir(tmp_path)

    r = CliRunner().invoke(app, ["ideas", "commit", "--json"])
    assert r.exit_code == 0, r.output
    assert len(record) == 1
    assert record[0][0] == "acme/proj"
    assert record[0][1] is not None  # since 被 _since_iso 转成 ISO 时间


def _git_checkout(root, origin="git@github.com:flingjie/Finch.git"):
    import subprocess

    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "remote", "add", "origin", origin], cwd=root, check=True)
    return root


def test_ideas_commit_prefers_cwd_origin_over_settings(monkeypatch, tmp_path):
    settings = _settings(tmp_path, ["acme/proj"])
    record = []
    _patch_cli(monkeypatch, settings, record)
    checkout = _git_checkout(tmp_path / "checkout")
    monkeypatch.chdir(checkout)

    r = CliRunner().invoke(app, ["ideas", "commit", "--json"])
    assert r.exit_code == 0, r.output
    assert record[0][0] == "flingjie/Finch"


def test_ideas_commit_explicit_repo_wins_over_cwd(monkeypatch, tmp_path):
    settings = _settings(tmp_path, ["acme/proj"])
    record = []
    _patch_cli(monkeypatch, settings, record)
    monkeypatch.chdir(_git_checkout(tmp_path / "checkout"))

    r = CliRunner().invoke(app, ["ideas", "commit", "--repo", "acme/other", "--json"])
    assert r.exit_code == 0, r.output
    assert record[0][0] == "acme/other"


def test_ideas_commit_requires_repo_when_unconfigured(monkeypatch, tmp_path):
    settings = _settings(tmp_path, [])
    ws = Workspace(settings.paths.var_dir)
    _patch_cli(monkeypatch, settings, [])
    monkeypatch.chdir(tmp_path)

    r = CliRunner().invoke(app, ["ideas", "commit", "--json"])
    assert r.exit_code == 1
    assert "--repo" in r.output
    assert ContentJobRepository(ws).list_jobs() == []


# ---- finch ideas list / show / confirm / revise-position / skip ----

CORE_POINT_2 = "another idea worth writing"


def _paths_settings(tmp_path):
    return Settings(paths=Paths(var_dir=tmp_path))


def _seed_candidate(ws, core_point=CORE_POINT) -> ContentJob:
    return IdeaService(ContentJobRepository(ws)).create_candidate(
        _make_candidate(core_point)
    )


def _make_candidate(core_point=CORE_POINT) -> IdeaCandidate:
    return IdeaCandidate(
        id="idea_abc12345",
        origin="practice",
        core_point=core_point,
        reader_problem="orchestrator was hard to rerun",
        why_worth_saying="failures can now be replayed",
        author_position=AuthorPosition(
            claim="failures can now be replayed",
            decision=core_point,
            tradeoff="orchestrator was hard to rerun",
        ),
        source_refs=[
            SourceRef(type="commit", ref=COMMIT_URL, summary="feat: node-ize orchestrator")
        ],
        boundaries=IdeaBoundaries(),
        recommended_format=RecommendedFormat.SHORT_POST,
        generator=IdeaGenerator(skill="commit-to-idea", version="1.0.0"),
    )


def _manual_job(idea_id: str = "idea_manual000", status=ContentJobStatus.PROPOSED) -> ContentJob:
    return ContentJob(
        id=idea_id,
        source_card_ids=[],
        reader_problem="orchestrator was hard to rerun",
        author_position=AuthorPosition(claim="claim", decision="decision", tradeoff="tradeoff"),
        recommended_format=RecommendedFormat.SHORT_POST,
        status=status,
        core_message=CORE_POINT,
        why_now="failures can now be replayed",
        origin="practice",
    )


def _patch_settings(monkeypatch, settings):
    monkeypatch.setattr(cli, "load_settings", lambda: settings)


def test_ideas_list_json_output(monkeypatch, tmp_path):
    settings = _paths_settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    _patch_settings(monkeypatch, settings)
    j1 = _seed_candidate(ws, CORE_POINT)
    j2 = _seed_candidate(ws, CORE_POINT_2)

    r = CliRunner().invoke(app, ["ideas", "list", "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert isinstance(payload, list)
    assert [p["id"] for p in payload] == sorted([j1.id, j2.id])
    assert all(p["status"] == "proposed" for p in payload)
    assert all(p["core_point"] for p in payload)


def test_ideas_list_non_json_output(monkeypatch, tmp_path):
    settings = _paths_settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    _patch_settings(monkeypatch, settings)
    job = _seed_candidate(ws)

    r = CliRunner().invoke(app, ["ideas", "list"])
    assert r.exit_code == 0, r.output
    lines = [ln for ln in r.output.strip().splitlines() if ln]
    assert lines[0].split("\t") == ["id", "status", "origin", "intent", "core_message"]
    idea_id, status, origin, intent, core = lines[1].split("\t")
    assert idea_id == job.id
    assert status == "proposed"
    assert origin == "practice"
    assert intent == "stance"
    assert core == CORE_POINT
    assert f"uv run finch ideas confirm {job.id}" in r.output


def test_ideas_show_json_dumps_candidate(monkeypatch, tmp_path):
    settings = _paths_settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    _patch_settings(monkeypatch, settings)
    job = _seed_candidate(ws)

    r = CliRunner().invoke(app, ["ideas", "show", job.id, "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert payload["core_message"] == CORE_POINT
    assert payload["author_position"]["decision"] == CORE_POINT


def test_ideas_show_json_dumps_job_when_no_candidate(monkeypatch, tmp_path):
    settings = _paths_settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    _patch_settings(monkeypatch, settings)
    job = _manual_job()
    ContentJobRepository(ws).upsert_job(job)

    r = CliRunner().invoke(app, ["ideas", "show", job.id, "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert payload["id"] == job.id
    assert payload["status"] == "proposed"
    assert payload["core_message"] == CORE_POINT


def test_ideas_show_non_json_output(monkeypatch, tmp_path):
    settings = _paths_settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    _patch_settings(monkeypatch, settings)
    job = _seed_candidate(ws)

    r = CliRunner().invoke(app, ["ideas", "show", job.id])
    assert r.exit_code == 0, r.output
    assert f"id: {job.id}" in r.output
    assert "status: proposed" in r.output
    assert CORE_POINT in r.output
    assert "下一步:" in r.output
    assert f"uv run finch ideas confirm {job.id}" in r.output
    assert f"uv run finch ideas skip {job.id} --reason" in r.output


def test_ideas_show_unknown_exits(monkeypatch, tmp_path):
    settings = _paths_settings(tmp_path)
    _patch_settings(monkeypatch, settings)

    r = CliRunner().invoke(app, ["ideas", "show", "idea_nope", "--json"])
    assert r.exit_code == 1
    assert "not found" in r.output


def test_ideas_confirm_transitions(monkeypatch, tmp_path):
    settings = _paths_settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    _patch_settings(monkeypatch, settings)
    job = _seed_candidate(ws)

    r = CliRunner().invoke(app, ["ideas", "confirm", job.id, "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert payload == {"id": job.id, "status": "confirmed"}
    assert ContentJobRepository(ws).get_job(job.id).status == ContentJobStatus.CONFIRMED


def test_ideas_confirm_non_json_next_step_is_command(monkeypatch, tmp_path):
    settings = _paths_settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    _patch_settings(monkeypatch, settings)
    job = _seed_candidate(ws)

    r = CliRunner().invoke(app, ["ideas", "confirm", job.id])
    assert r.exit_code == 0, r.output
    assert f"{job.id} -> confirmed" in r.output
    assert f"uv run finch drafts create {job.id}" in r.output


def test_ideas_confirm_illegal_transition_exits(monkeypatch, tmp_path):
    settings = _paths_settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    _patch_settings(monkeypatch, settings)
    job = _manual_job(status=ContentJobStatus.SKIPPED)
    ContentJobRepository(ws).upsert_job(job)

    r = CliRunner().invoke(app, ["ideas", "confirm", job.id, "--json"])
    assert r.exit_code == 1
    assert "illegal transition" in r.output


def test_ideas_revise_position_updates(monkeypatch, tmp_path):
    settings = _paths_settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    _patch_settings(monkeypatch, settings)
    job = _seed_candidate(ws)
    position_file = tmp_path / "position.yaml"
    position_file.write_text(
        "claim: new claim\ndecision: new decision\ntradeoff: new tradeoff\nstatus: proposed\n"
    )

    r = CliRunner().invoke(
        app, ["ideas", "revise-position", job.id, "--file", str(position_file), "--json"]
    )
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert payload == {"id": job.id, "status": "proposed"}
    updated = ContentJobRepository(ws).get_job(job.id)
    assert updated.author_position.claim == "new claim"
    assert updated.author_position.decision == "new decision"
    assert updated.author_position.tradeoff == "new tradeoff"


def test_ideas_revise_position_missing_file_exits(monkeypatch, tmp_path):
    settings = _paths_settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    _patch_settings(monkeypatch, settings)
    job = _seed_candidate(ws)

    r = CliRunner().invoke(
        app, ["ideas", "revise-position", job.id, "--file", str(tmp_path / "nope.yaml")]
    )
    assert r.exit_code == 1
    assert "invalid position file" in r.output


def test_ideas_skip_records_reason(monkeypatch, tmp_path):
    settings = _paths_settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    _patch_settings(monkeypatch, settings)
    job = _seed_candidate(ws)

    r = CliRunner().invoke(app, ["ideas", "skip", job.id, "--reason", "not_now", "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert payload["id"] == job.id
    assert payload["status"] == "skipped"
    assert payload["reason"] == "not_now"
    updated = ContentJobRepository(ws).get_job(job.id)
    assert updated.status == ContentJobStatus.SKIPPED
    assert updated.reject_reason == "not_now"


def test_ideas_list_surfaces_legacy_row_warning(monkeypatch, tmp_path):
    settings = _paths_settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    _patch_settings(monkeypatch, settings)
    _seed_candidate(ws)
    # 模拟旧版行：payload 含不在枚举里的 status，无法解析为当前 ContentJob。
    legacy_path = ws.dir("ideas") / "job_tp1.yaml"
    legacy_path.write_text("id: job_tp1\nstatus: ready\n", encoding="utf-8")

    r = CliRunner().invoke(app, ["ideas", "list"])
    assert r.exit_code == 0, r.output
    assert CORE_POINT in r.output          # 正常行仍在。
    assert "系统警告" in r.output
    assert "job_tp1" in r.output


def test_ideas_skip_illegal_transition_exits(monkeypatch, tmp_path):
    settings = _paths_settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    _patch_settings(monkeypatch, settings)
    job = _manual_job(status=ContentJobStatus.DRAFTED)
    ContentJobRepository(ws).upsert_job(job)

    r = CliRunner().invoke(app, ["ideas", "skip", job.id, "--reason", "not_now"])
    assert r.exit_code == 1
    assert "illegal transition" in r.output


# ---- finch ideas create ----

class _FakeFragmentService:
    def __init__(self, runner):
        self.runner = runner

    def from_text(self, text):
        return _candidate()

    def from_conversation(self, evidence):
        return _candidate()

    def from_thread(self, thread, *, interactions=None):
        return _candidate()

    def from_signals(self, *, peers=None, threads=None):
        return None


def _patch_create_cli(monkeypatch, settings):
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "FragmentService", _FakeFragmentService)


def test_ideas_create_requires_exactly_one_source(monkeypatch, tmp_path):
    settings = _settings(tmp_path, [])
    _patch_create_cli(monkeypatch, settings)
    r = CliRunner().invoke(app, ["ideas", "create"])
    assert r.exit_code == 1
    assert "exactly one of" in r.output


def test_ideas_create_text_persists(monkeypatch, tmp_path):
    settings = _settings(tmp_path, [])
    ws = Workspace(settings.paths.var_dir)
    _patch_create_cli(monkeypatch, settings)
    r = CliRunner().invoke(app, ["ideas", "create", "--text", "hello", "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert payload["status"] == "proposed"
    # _FakeFragmentService 复用 _candidate()（origin="practice"），仅验证落库链路。
    assert ContentJobRepository(ws).list_jobs()[0].core_message == CORE_POINT


class _FakeConversationThreadRepo:
    def __init__(self, ws):
        self.ws = ws

    def get(self, thread_id):
        return ConversationThread(id=thread_id, peer_id="peer_abc", topic="agent evals")


class _MissingThreadRepo:
    def __init__(self, ws):
        self.ws = ws

    def get(self, thread_id):
        return None


def test_ideas_create_conversation_persists(monkeypatch, tmp_path):
    settings = _settings(tmp_path, [])
    ws = Workspace(settings.paths.var_dir)
    _patch_create_cli(monkeypatch, settings)
    monkeypatch.setattr(cli, "ConversationThreadRepository", _FakeConversationThreadRepo)
    r = CliRunner().invoke(app, ["ideas", "create", "--conversation", "thread_1", "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert payload["status"] == "proposed"
    assert ContentJobRepository(ws).list_jobs()[0].core_message == CORE_POINT


def test_ideas_create_conversation_missing_rejected(monkeypatch, tmp_path):
    settings = _settings(tmp_path, [])
    ws = Workspace(settings.paths.var_dir)
    _patch_create_cli(monkeypatch, settings)
    monkeypatch.setattr(cli, "ConversationThreadRepository", _MissingThreadRepo)
    r = CliRunner().invoke(app, ["ideas", "create", "--conversation", "nope", "--json"])
    assert r.exit_code == 1
    assert "conversation not found" in r.output
    assert ContentJobRepository(ws).list_jobs() == []


def test_ideas_signals_no_signal_exits_zero(monkeypatch, tmp_path):
    settings = _settings(tmp_path, [])
    ws = Workspace(settings.paths.var_dir)
    _patch_create_cli(monkeypatch, settings)
    r = CliRunner().invoke(app, ["ideas", "signals"])
    assert r.exit_code == 0, r.output
    assert "no community signal" in r.output
    assert ContentJobRepository(ws).list_jobs() == []


class _SignalIdeaFragmentService:
    def __init__(self, runner):
        self.runner = runner

    def from_signals(self, *, peers=None, threads=None):
        return _candidate().model_copy(update={"origin": "synthesis"})


def test_ideas_signals_persists_candidate(monkeypatch, tmp_path):
    settings = _settings(tmp_path, [])
    ws = Workspace(settings.paths.var_dir)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "FragmentService", _SignalIdeaFragmentService)
    r = CliRunner().invoke(app, ["ideas", "signals", "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert payload["origin"] == "synthesis"
    assert payload["status"] == "proposed"
    jobs = ContentJobRepository(ws).list_jobs()
    assert len(jobs) == 1
    assert jobs[0].origin == "synthesis"
