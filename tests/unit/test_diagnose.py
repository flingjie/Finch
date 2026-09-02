from finch.github.gh_client import _run
from finch.twitter.opencli_client import OpenCliClient


def test_run_returns_structured_result():
    r = _run(["echo", "hello"], timeout=5.0)
    assert r["ok"] is True
    assert r["exit_code"] == 0
    assert "hello" in r["stdout"]


def test_run_captures_failure():
    r = _run(["sh", "-c", "echo boom >&2; exit 3"], timeout=5.0)
    assert r["ok"] is False
    assert r["exit_code"] == 3
    assert "boom" in r["stderr"]


def test_opencli_client_returns_diagnostic_dict(monkeypatch):
    def fake_run(argv, timeout):
        return {"ok": True, "exit_code": 0, "stdout": "ok\n", "stderr": ""}

    monkeypatch.setattr("finch.twitter.opencli_client._run", fake_run)
    c = OpenCliClient()
    r = c.doctor()
    assert r == {"ok": True, "exit_code": 0, "detail": "ok"}


def test_run_missing_binary_returns_not_found():
    r = _run(["definitely_not_a_real_binary_xyz"], timeout=5.0)
    assert r["ok"] is False
    assert r["stderr"] == "not found"
