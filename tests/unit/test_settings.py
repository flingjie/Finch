# tests/unit/test_settings.py
from pathlib import Path

from finch.settings import QualityGates, load_settings


def test_load_settings_defaults():
    s = load_settings(Path("finch.yaml"))
    assert s.repositories == []
    assert s.repository_discovery.enabled is True
    assert s.repository_discovery.lookback_hours == 24
    assert s.repository_discovery.max_repos == 10
    assert s.twitter.daily_limit == 100
    assert s.paths.var_dir == Path("var")


def test_load_settings_creates_var_dirs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # 不存在的配置文件 -> 走默认值；相对路径 var/ 按当前 cwd(=tmp_path) 解析
    load_settings(tmp_path / "finch.yaml")
    assert (tmp_path / "var").exists()
    assert (tmp_path / "var" / "outputs").exists()


def test_quality_gates_defaults_from_yaml():
    s = load_settings(Path("finch.yaml"))
    g = s.quality_gates
    assert isinstance(g, QualityGates)
    assert g.max_daily_replies == 5
    assert g.min_candidate_score == 0.65
    assert g.min_evidence_score == 0.75
    assert g.min_quality_score == 0.75
    assert g.min_discussability == 0.50
    assert g.max_rewrite_rounds == 2
    assert g.match_top_k == 10
    assert g.timing_default == 0.3
    assert s.twitter.high_value_authors == []
    assert s.twitter.blocked_authors == []


def test_quality_gates_defaults_without_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = load_settings(tmp_path / "missing.yaml")
    assert s.quality_gates.match_top_k == 10
    assert s.twitter.blocked_authors == []
