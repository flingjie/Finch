"""Unit tests for source run reporting helpers."""

from __future__ import annotations

from finch.sources.models import OpenCliRequest, ResultKind, Source, SourceStatus
from finch.sources.orchestrator import (
    QueryOutcome,
    SyncResult,
    _request_label,
    aggregate_state,
    sync_result_to_dict,
)


def test_aggregate_state_states():
    assert (
        aggregate_state(SyncResult(source=Source.TWITTER, status=SourceStatus.READY, raw_count=1))
        == "success"
    )
    assert (
        aggregate_state(SyncResult(source=Source.TWITTER, status=SourceStatus.READY, raw_count=0))
        == "empty"
    )
    assert (
        aggregate_state(
            SyncResult(source=Source.TWITTER, status=SourceStatus.DEGRADED, kind=ResultKind.TIMEOUT)
        )
        == "failed"
    )
    assert (
        aggregate_state(
            SyncResult(
                source=Source.TWITTER,
                status=SourceStatus.DEGRADED,
                detail="timeline unavailable",
            )
        )
        == "failed"
    )


def test_aggregate_timeline_success_not_failed():
    assert (
        aggregate_state(
            SyncResult(
                source=Source.TWITTER,
                status=SourceStatus.DEGRADED,
                kind=ResultKind.SUCCESS,
                raw_count=3,
            )
        )
        == "success"
    )


def test_aggregate_partial_success_when_some_queries_failed():
    r = SyncResult(
        source=Source.REDDIT,
        status=SourceStatus.READY,
        raw_count=5,
        per_query=[
            QueryOutcome(query="a", outcome="failed", kind=ResultKind.TIMEOUT),
            QueryOutcome(query="b", outcome="ran", raw_count=5, kind=ResultKind.SUCCESS),
        ],
    )
    assert aggregate_state(r) == "partial_success"


def test_aggregate_failed_when_all_queries_failed():
    r = SyncResult(
        source=Source.REDDIT,
        status=SourceStatus.READY,
        raw_count=0,
        per_query=[QueryOutcome(query="a", outcome="failed", kind=ResultKind.TIMEOUT)],
    )
    assert aggregate_state(r) == "failed"


def test_aggregate_partial_flag_counts_as_partial_success():
    r = SyncResult(
        source=Source.GITHUB,
        status=SourceStatus.READY,
        raw_count=3,
        per_query=[
            QueryOutcome(
                query="alice",
                outcome="ran",
                raw_count=3,
                kind=ResultKind.SUCCESS,
                partial=True,
                detail="repos: timeout",
            )
        ],
    )
    assert aggregate_state(r) == "partial_success"


def test_request_label():
    assert (
        _request_label(OpenCliRequest(surface="twitter", command="timeline"), "timeline")
        == "timeline"
    )
    assert _request_label(OpenCliRequest(surface="v2ex", command="hot"), "hot") == "hot"
    assert (
        _request_label(
            OpenCliRequest(surface="reddit", command="search", args=("agent stuck",)),
            "query",
        )
        == "agent stuck"
    )
    assert (
        _request_label(
            OpenCliRequest(
                surface="twitter", command="timeline", args=("--limit", "20")
            ),
            "timeline",
        )
        == "timeline"
    )


def test_sync_result_to_dict_serializes_enums_and_per_query():
    r = SyncResult(
        source=Source.REDDIT,
        status=SourceStatus.READY,
        raw_count=1,
        kind=ResultKind.SUCCESS,
        mode="query",
        ran_queries=["agent stuck"],
        per_query=[
            QueryOutcome(
                query="agent stuck",
                outcome="ran",
                raw_count=1,
                kind=ResultKind.SUCCESS,
            )
        ],
        started_at="2026-10-30T10:00:00+00:00",
    )
    d = sync_result_to_dict(r)
    assert d["source"] == "reddit"
    assert d["status"] == "READY"
    assert d["kind"] == "SUCCESS"
    assert d["per_query"][0]["query"] == "agent stuck"
    assert d["per_query"][0]["kind"] == "SUCCESS"
    assert d["per_query"][0]["partial"] is False
    assert "artifacts" not in d
