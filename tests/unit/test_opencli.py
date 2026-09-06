"""opencli 共享助手单元测试：瞬态错误判定与有界重试。"""

from finch.opencli import is_transient_opencli_error, run_opencli


def _result(ok: bool, stderr: str) -> dict:
    return {"ok": ok, "exit_code": 0 if ok else 1, "stdout": "", "stderr": stderr}


def test_is_transient_opencli_error_matches_navigation_and_detach():
    assert is_transient_opencli_error(
        "Pre-navigation to https://x.com failed: Navigation rejected."
    ) is True
    assert is_transient_opencli_error("Detached while handling command.") is True
    assert is_transient_opencli_error("Error: not logged in to Twitter") is False
    assert is_transient_opencli_error("Browser bridge unavailable") is False


def test_run_opencli_retries_transient_then_succeeds():
    calls: list[str] = []

    def fake_run(argv, timeout):
        calls.append(argv[0])
        if len(calls) == 1:
            return _result(False, "Navigation rejected.")
        return _result(True, "")

    result = run_opencli(
        ["opencli", "twitter", "search", "q"],
        run_fn=fake_run,
        backoff_seconds=0,
    )

    assert result["ok"] is True
    assert calls == ["opencli", "opencli"]


def test_run_opencli_does_not_retry_non_transient():
    calls: list[str] = []

    def fake_run(argv, timeout):
        calls.append("called")
        return _result(False, "not logged in")

    result = run_opencli(["opencli"], run_fn=fake_run, backoff_seconds=0)

    assert result["ok"] is False
    assert calls == ["called"]


def test_run_opencli_stops_after_max_retries():
    calls: list[str] = []

    def fake_run(argv, timeout):
        calls.append("called")
        return _result(False, "Detached while handling command.")

    result = run_opencli(
        ["opencli"],
        run_fn=fake_run,
        max_retries=2,
        backoff_seconds=0,
    )

    assert result["ok"] is False
    assert len(calls) == 3
