"""CLI smoke：finch problem-discovery list / show / add（无真实网络 / LLM）。"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from finch import cli
from finch.cli import app
from finch.problem_discovery.models import (
    Admission,
    Problem,
    ProblemEvidence,
    ProblemScenario,
)
from finch.problem_discovery.repository import ProblemRepository
from finch.settings import Paths, Settings
from finch.storage.workspace import Workspace


def _settings(tmp_path) -> Settings:
    return Settings(paths=Paths(var_dir=tmp_path))


def _seed_problem(ws: Workspace) -> str:
    repo = ProblemRepository(ws)
    repo.upsert(
        Problem(
            problem_id="prob_x",
            title="Agent 失败后难以复现",
            user_type="Agent 应用开发者",
            scenario=ProblemScenario(task="调试 Agent 失败"),
            next_action="interview",
            admission=Admission.SOURCE_CHECKED,
        )
    )
    return "prob_x"


def test_problem_discovery_list_json(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    _seed_problem(ws)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(
        app,
        ["problem-discovery", "list", "--admission", "source_checked,accepted", "--json"],
    )
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert [p["problem_id"] for p in payload] == ["prob_x"]


def test_problem_discovery_list_compact(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    _seed_problem(ws)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(
        app, ["problem-discovery", "list", "--admission", "source_checked,accepted"]
    )
    assert r.exit_code == 0, r.output
    assert "Agent 失败后难以复现" in r.output
    assert "查看：uv run finch problem-discovery show prob_x" in r.output
    assert "场景：任务=调试 Agent 失败" not in r.output


def test_problem_discovery_list_full(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    _seed_problem(ws)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(
        app, ["problem-discovery", "list", "--admission", "source_checked,accepted", "--full"]
    )
    assert r.exit_code == 0, r.output
    assert "场景：任务=调试 Agent 失败" in r.output


def test_problem_discovery_show_json(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    _seed_problem(ws)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["problem-discovery", "show", "prob_x", "--json"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["title"] == "Agent 失败后难以复现"


def test_problem_discovery_show_missing_exits_1(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    r = CliRunner().invoke(app, ["problem-discovery", "show", "prob_missing", "--json"])
    assert r.exit_code == 1


def test_problem_discovery_add_url(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    r = CliRunner().invoke(app, ["problem-discovery", "add", "--url", "https://x.com/a/1"])
    assert r.exit_code == 0, r.output
    assert "disc_" in r.output


def test_problem_discovery_summary_json(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    _seed_problem(ws)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(
        app,
        ["problem-discovery", "summary", "--admission", "source_checked,accepted", "--json"],
    )
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert payload["total_cards"] == 1
    assert payload["cards_without_evidence"] == ["prob_x"]
    assert payload["groups_available"] is False


def test_problem_discovery_present_and_last_presented(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    _seed_problem(ws)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["problem-discovery", "present", "prob_x"])
    assert r.exit_code == 0, r.output
    assert "pd_" in r.output

    r2 = CliRunner().invoke(app, ["problem-discovery", "last-presented", "--json"])
    assert r2.exit_code == 0, r2.output
    assert json.loads(r2.output)["problem_ids"] == ["prob_x"]


def test_problem_discovery_list_hides_quarantined_by_default(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    ProblemRepository(ws).upsert(
        Problem(problem_id="prob_q", title="隔离卡", admission=Admission.QUARANTINED)
    )
    ProblemRepository(ws).upsert(
        Problem(problem_id="prob_ok", title="可见卡", admission=Admission.ACCEPTED)
    )
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    hidden = CliRunner().invoke(app, ["problem-discovery", "list", "--json"])
    assert hidden.exit_code == 0, hidden.output
    assert [p["problem_id"] for p in json.loads(hidden.output)] == ["prob_ok"]

    shown = CliRunner().invoke(
        app, ["problem-discovery", "list", "--admission", "all", "--json"]
    )
    assert shown.exit_code == 0, shown.output
    assert {p["problem_id"] for p in json.loads(shown.output)} == {"prob_q", "prob_ok"}


def test_problem_discovery_admit_sets_accepted(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    ProblemRepository(ws).upsert(
        Problem(problem_id="prob_q", title="隔离卡", admission=Admission.QUARANTINED)
    )
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    missing = CliRunner().invoke(app, ["problem-discovery", "admit", "prob_q"])
    assert missing.exit_code != 0

    ok = CliRunner().invoke(app, ["problem-discovery", "admit", "prob_q", "--accepted"])
    assert ok.exit_code == 0, ok.output
    assert "prob_q" in ok.output
    assert "accepted" in ok.output
    stored = ProblemRepository(ws).get("prob_q")
    assert stored is not None
    assert stored.admission == Admission.ACCEPTED
    assert stored.admission_reason == "human_accepted"

    stored.admission_reason = "kept"
    ProblemRepository(ws).upsert(stored)
    noop = CliRunner().invoke(app, ["problem-discovery", "admit", "prob_q", "--accepted"])
    assert noop.exit_code == 0, noop.output
    stored = ProblemRepository(ws).get("prob_q")
    assert stored is not None
    assert stored.admission == Admission.ACCEPTED
    assert stored.admission_reason == "kept"

    again = CliRunner().invoke(
        app, ["problem-discovery", "admit", "prob_q", "--accepted", "--force"]
    )
    assert again.exit_code == 0, again.output
    stored = ProblemRepository(ws).get("prob_q")
    assert stored is not None
    assert stored.admission_reason == "kept"


def test_problem_discovery_admit_shadow(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    ProblemRepository(ws, shadow=True).upsert(
        Problem(problem_id="prob_s", title="影子", admission=Admission.DRAFT)
    )
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(
        app, ["problem-discovery", "admit", "prob_s", "--accepted", "--shadow"]
    )
    assert r.exit_code == 0, r.output
    stored = ProblemRepository(ws, shadow=True).get("prob_s")
    assert stored is not None
    assert stored.admission == Admission.ACCEPTED
    assert stored.admission_reason == "human_accepted"
    assert ProblemRepository(ws).get("prob_s") is None


def test_show_accepted_without_evidence_displays_gap(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    ProblemRepository(ws).upsert(
        Problem(
            problem_id="prob_noev",
            title="无证据已接受",
            admission=Admission.ACCEPTED,
            evidence=[],
        )
    )
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["problem-discovery", "show", "prob_noev"])
    assert r.exit_code == 0, r.output
    assert "已接受" in r.output
    assert "证据（0 条）" in r.output


def test_show_source_checked_wording_and_host_annotations(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    ProblemRepository(ws).upsert(
        Problem(
            problem_id="prob_sc",
            title="已核对",
            admission=Admission.SOURCE_CHECKED,
            evidence=[
                ProblemEvidence(
                    source_ref="s",
                    quote="q",
                    claim_support="supported",
                    claim_support_reason="原文一致",
                    author="alice",
                )
            ],
        )
    )
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["problem-discovery", "show", "prob_sc"])
    assert r.exit_code == 0, r.output
    assert "引用已核对" in r.output
    assert "主张核对：支持（原文一致）" in r.output
    assert "作者：alice" in r.output


def test_list_separates_conflicted_cards(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    _seed_problem(ws)
    ProblemRepository(ws).upsert(
        Problem(
            problem_id="prob_conf",
            title="冲突卡",
            admission=Admission.SOURCE_CHECKED,
            evidence=[
                ProblemEvidence(
                    source_ref="s",
                    quote="q",
                    claim_support="conflicting",
                    claim_support_reason="r",
                )
            ],
        )
    )
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["problem-discovery", "list"])
    assert r.exit_code == 0, r.output
    assert "问题（1）" in r.output
    assert "待复查（主张与摘录冲突，不计入重点）：1 张" in r.output
    assert "冲突卡  [prob_conf]" in r.output
