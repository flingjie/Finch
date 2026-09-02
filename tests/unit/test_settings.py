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
    # 不存在的配置文件 -> 走默认值；相对路径 var/ 按当前 cwd(=tmp_path) 解析
    s = load_settings(tmp_path / "finch.yaml")
    assert (tmp_path / "var").exists()
    assert (tmp_path / "var" / "outputs").exists()
