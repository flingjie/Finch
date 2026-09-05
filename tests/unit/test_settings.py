# tests/unit/test_settings.py
from pathlib import Path

import pytest
from pydantic import ValidationError

from finch.settings import LLMNodeSettings, LLMSettings, QualityGates, load_settings


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


def test_for_node_returns_default_model_when_no_node():
    llm = LLMSettings(model="deepseek-v4-flash")
    cfg = llm.for_node("define_jobs")
    assert cfg.model == "deepseek-v4-flash"
    assert cfg.timeout_seconds == 90.0
    assert cfg.max_output_tokens is None
    assert cfg.max_concurrency == 1


def test_for_node_merges_node_over_default():
    llm = LLMSettings(
        model="deepseek-v4-flash",
        nodes={"critique": LLMNodeSettings(
            model="deepseek-v4-pro", timeout_seconds=300.0, max_output_tokens=4096,
        )},
    )
    cfg = llm.for_node("critique")
    assert cfg.model == "deepseek-v4-pro"
    assert cfg.timeout_seconds == 300.0
    assert cfg.max_output_tokens == 4096


def test_for_node_inherits_default_model_when_node_model_blank():
    llm = LLMSettings(
        model="deepseek-v4-flash",
        nodes={"plan_topics": LLMNodeSettings(timeout_seconds=120.0, max_output_tokens=2000)},
    )
    cfg = llm.for_node("plan_topics")
    assert cfg.model == "deepseek-v4-flash"
    assert cfg.timeout_seconds == 120.0
    assert cfg.max_output_tokens == 2000


def test_llm_node_settings_rejects_nonpositive_concurrency():
    """max_concurrency 必须 >=1，否则 ThreadPoolExecutor(max_workers=0) 会抛 ValueError。"""
    with pytest.raises(ValidationError):
        LLMNodeSettings(max_concurrency=0)
    with pytest.raises(ValidationError):
        LLMNodeSettings(max_concurrency=-1)
