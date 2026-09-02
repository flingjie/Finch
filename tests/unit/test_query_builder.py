"""Unit tests for twitter query builder."""
import pytest

from finch.twitter.query_builder import QueryBuilder, QueryConfig


def test_query_config_builds_argv():
    cfg = QueryConfig(id="q1", text='"agent evals"', filter="live", priority=5)
    argv = cfg.build_argv(per_query_limit=20)
    assert argv[0] == "opencli"
    assert argv[1] == "twitter"
    assert argv[2] == "search"
    assert argv[3] == '"agent evals"'
    assert "--product" in argv
    assert "live" in argv
    assert "--limit" in argv
    assert "20" in argv
    assert "-f" in argv
    assert "json" in argv


def test_query_config_invalid_filter_raises():
    with pytest.raises(ValueError):
        QueryConfig(id="q1", text="x", filter="invalid")


def test_query_builder_loads_configs():
    configs = [
        {"id": "q1", "text": "hello", "filter": "top", "priority": 5},
        {"id": "q2", "text": "world", "filter": "live", "priority": 3},
    ]
    builder = QueryBuilder(configs, per_query_limit=10)
    assert len(builder) == 2
    assert builder.version != ""


def test_query_builder_empty():
    builder = QueryBuilder([], per_query_limit=20)
    assert len(builder) == 0
    assert builder.version != ""  # hash of empty string


def test_query_builder_iterable():
    configs = [
        {"id": "q1", "text": "hello", "filter": "top"},
    ]
    builder = QueryBuilder(configs)
    cfgs = list(builder)
    assert len(cfgs) == 1
    assert cfgs[0].id == "q1"


def test_query_builder_build_all():
    configs = [
        {"id": "q1", "text": "hello", "filter": "top"},
        {"id": "q2", "text": "world", "filter": "live"},
    ]
    builder = QueryBuilder(configs, per_query_limit=15)
    all_queries = builder.build_all()
    assert len(all_queries) == 2
    assert all_queries[0][0].id == "q1"
    assert "--limit" in all_queries[0][1]
    assert "15" in all_queries[0][1]


def test_query_version_is_deterministic():
    configs = [
        {"id": "q1", "text": "hello", "filter": "top"},
    ]
    v1 = QueryBuilder(configs).version
    v2 = QueryBuilder(configs).version
    assert v1 == v2


def test_query_version_changes_with_content():
    v1 = QueryBuilder([{"id": "q1", "text": "hello", "filter": "top"}]).version
    v2 = QueryBuilder([{"id": "q1", "text": "world", "filter": "top"}]).version
    assert v1 != v2
