# tests/unit/test_settings.py
from pathlib import Path

from finch.settings import load_settings


def test_load_settings_defaults():
    s = load_settings(Path("finch.yaml"))
    assert s.repositories == ["flingjie/FDE-Gym"]
    assert s.twitter.daily_limit == 100
    assert s.paths.var_dir == Path("var")


def test_load_settings_creates_var_dirs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = load_settings(Path("finch.yaml"))  # reads repo-root finch.yaml; paths resolve under tmp_path
    assert s.paths.var_dir.exists()
    assert s.paths.outputs_dir.exists()
