import subprocess

from finch.dev.registry import FeatureSpec
from finch.dev.runner import run_test


def test_run_test_timeout_returns_nonzero(monkeypatch):
    def _boom(argv, capture_output, text, timeout, check):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=timeout)

    monkeypatch.setattr("finch.dev.runner.subprocess.run", _boom)
    spec = FeatureSpec(name="x", kind="function", description="d", test_targets=("t",))
    assert run_test(spec) == 124
