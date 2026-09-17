"""Unit tests for OpenCliGateway, exit-code map, and read-only policy."""

from __future__ import annotations

import json

import pytest

from finch.sources.capabilities import parse_capability_list
from finch.sources.doctor import run_doctor
from finch.sources.models import (
    OpenCliRequest,
    ResultKind,
    SourceCommandBlocked,
    map_exit_code,
)
from finch.sources.opencli_gateway import OpenCliGateway, redact_text
from finch.sources.policy import check_allowlist


class TestMapExitCode:
    def test_success(self):
        assert map_exit_code(0) == ResultKind.SUCCESS

    def test_empty(self):
        assert map_exit_code(66) == ResultKind.EMPTY

    def test_bridge_down(self):
        assert map_exit_code(69) == ResultKind.BRIDGE_DOWN

    def test_timeout(self):
        assert map_exit_code(75) == ResultKind.TIMEOUT

    def test_auth(self):
        assert map_exit_code(77) == ResultKind.AUTH_REQUIRED

    def test_config(self):
        assert map_exit_code(78) == ResultKind.CONFIG_ERROR

    def test_cancelled(self):
        assert map_exit_code(130) == ResultKind.CANCELLED

    def test_missing_binary(self):
        assert map_exit_code(None, stderr="not found") == ResultKind.UNAVAILABLE

    def test_timeout_stderr(self):
        assert map_exit_code(None, stderr="timeout") == ResultKind.TIMEOUT

    def test_auth_heuristic(self):
        assert map_exit_code(1, stderr="not logged in") == ResultKind.AUTH_REQUIRED


class TestPolicy:
    def test_allows_twitter_search(self):
        check_allowlist(["opencli", "twitter", "search", "q"])

    def test_allows_weixin_search(self):
        check_allowlist(["opencli", "weixin", "search", "AI Agent", "-f", "json"])

    def test_blocks_twitter_reply(self):
        with pytest.raises(SourceCommandBlocked):
            check_allowlist(["opencli", "twitter", "reply", "1", "hi"])

    def test_blocks_reddit_upvote(self):
        with pytest.raises(SourceCommandBlocked):
            check_allowlist(["opencli", "reddit", "upvote", "x"])

    def test_blocks_unknown_surface(self):
        with pytest.raises(SourceCommandBlocked):
            check_allowlist(["opencli", "unknown", "search"])

    def test_blocks_global_write_verb(self):
        with pytest.raises(SourceCommandBlocked):
            check_allowlist(["opencli", "xiaohongshu", "publish", "x"])

    def test_allows_meta_doctor(self):
        check_allowlist(["opencli", "doctor"])

    def test_allows_meta_list(self):
        check_allowlist(["opencli", "list", "-f", "json"])


class TestRedact:
    def test_redacts_cookie(self):
        assert "[REDACTED]" in (redact_text("Cookie: abc123secret") or "")

    def test_truncates(self):
        long = "x" * 1000
        assert len(redact_text(long, max_len=50) or "") <= 50


class TestGateway:
    def test_run_success(self):
        def fake_run(argv, timeout):
            return {
                "ok": True,
                "exit_code": 0,
                "stdout": json.dumps([{"id": "1", "text": "hi"}]),
                "stderr": "",
            }

        gw = OpenCliGateway(run_fn=fake_run)
        result = gw.run(
            OpenCliRequest(surface="twitter", command="search", args=("q", "-f", "json"))
        )
        assert result.kind == ResultKind.SUCCESS
        assert len(result.rows) == 1
        assert result.rows[0]["id"] == "1"

    def test_write_blocked_before_spawn(self):
        calls: list[list[str]] = []

        def fake_run(argv, timeout):
            calls.append(argv)
            return {"ok": True, "exit_code": 0, "stdout": "[]", "stderr": ""}

        gw = OpenCliGateway(run_fn=fake_run)
        with pytest.raises(SourceCommandBlocked):
            gw.run(OpenCliRequest(surface="twitter", command="reply", args=("1", "hi")))
        assert calls == []

    def test_empty_exit_code(self):
        def fake_run(argv, timeout):
            return {"ok": False, "exit_code": 66, "stdout": "", "stderr": ""}

        gw = OpenCliGateway(run_fn=fake_run)
        result = gw.run(
            OpenCliRequest(surface="twitter", command="search", args=("q", "-f", "json"))
        )
        assert result.kind == ResultKind.EMPTY

    def test_auth_required(self):
        def fake_run(argv, timeout):
            return {
                "ok": False,
                "exit_code": 77,
                "stdout": "",
                "stderr": "not logged in",
            }

        gw = OpenCliGateway(run_fn=fake_run)
        result = gw.run(
            OpenCliRequest(surface="twitter", command="whoami", args=("-f", "json"))
        )
        assert result.kind == ResultKind.AUTH_REQUIRED
        assert result.stderr_summary

    def test_profile_injected_into_argv(self):
        captured: list[list[str]] = []

        def fake_run(argv, timeout):
            captured.append(list(argv))
            return {
                "ok": True,
                "exit_code": 0,
                "stdout": "[]",
                "stderr": "",
            }

        gw = OpenCliGateway(run_fn=fake_run, profile="work")
        gw.run(
            OpenCliRequest(surface="twitter", command="search", args=("q", "-f", "json"))
        )
        assert captured
        assert "--profile" in captured[0]
        assert captured[0][captured[0].index("--profile") + 1] == "work"

    def test_profile_not_duplicated(self):
        captured: list[list[str]] = []

        def fake_run(argv, timeout):
            captured.append(list(argv))
            return {"ok": True, "exit_code": 0, "stdout": "[]", "stderr": ""}

        gw = OpenCliGateway(run_fn=fake_run, profile="work")
        gw.run(
            OpenCliRequest(
                surface="twitter",
                command="search",
                args=("q", "--profile", "other", "-f", "json"),
            )
        )
        assert captured[0].count("--profile") == 1
        assert "other" in captured[0]


class TestCapabilities:
    def test_parse_list(self):
        data = [
            {"site": "twitter", "commands": ["search", "profile"]},
            {"site": "v2ex", "commands": [{"name": "hot"}]},
        ]
        surfaces = parse_capability_list(data)
        assert surfaces["twitter"] == ["search", "profile"]
        assert surfaces["v2ex"] == ["hot"]

    def test_parse_opencli_single_command_row_uses_name(self):
        surfaces = parse_capability_list(
            [{"site": "weixin", "name": "search", "command": "weixin/search"}]
        )
        assert surfaces["weixin"] == ["search"]

    def test_fetch_capabilities_profile_before_subcommand(self):
        from finch.sources.capabilities import fetch_capabilities

        captured: list[list[str]] = []

        def fake_run(argv, timeout):
            captured.append(list(argv))
            return {"ok": True, "exit_code": 0, "stdout": "[]", "stderr": ""}

        fetch_capabilities(run_fn=fake_run, profile="work")
        assert captured[0][:4] == ["opencli", "--profile", "work", "list"]


class TestDoctor:
    def test_doctor_with_fakes(self):
        def fake_run(argv, timeout):
            if argv[:2] == ["opencli", "doctor"]:
                return {"ok": True, "exit_code": 0, "stdout": "ok", "stderr": ""}
            if argv[:2] == ["opencli", "list"]:
                return {
                    "ok": True,
                    "exit_code": 0,
                    "stdout": json.dumps(
                        [
                            {"site": "twitter", "commands": ["search", "whoami"]},
                            {"site": "reddit", "commands": ["search"]},
                        ]
                    ),
                    "stderr": "",
                }
            if argv[:1] == ["gh"]:
                if "auth" in argv:
                    return {"ok": True, "exit_code": 0, "stdout": "Logged in", "stderr": ""}
                return {
                    "ok": True,
                    "exit_code": 0,
                    "stdout": "gh version 2.0.0",
                    "stderr": "",
                }
            # smoke queries
            return {
                "ok": True,
                "exit_code": 0,
                "stdout": json.dumps([{"id": "1"}]),
                "stderr": "",
            }

        report = run_doctor(run_fn=fake_run, run_smoke=True)
        assert report.opencli_ok
        assert report.capability_snapshot_id
        by_src = {s.source.value: s for s in report.sources}
        assert by_src["github"].status.value == "READY"
        assert by_src["twitter"].status.value == "READY"
