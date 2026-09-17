"""``finch sources doctor``：环境自检，不写 Cookie/Token。"""

from __future__ import annotations

from collections.abc import Callable

from finch.github.gh_client import _run
from finch.sources.capabilities import fetch_capabilities
from finch.sources.models import (
    DoctorReport,
    DoctorSourceReport,
    OpenCliRequest,
    ResultKind,
    Source,
    SourceStatus,
)
from finch.sources.opencli_gateway import OpenCliGateway
from finch.sources.policy import allowed_commands_for

# Minimal smoke queries (read-only). Skipped when surface absent from capabilities.
_SMOKE: dict[Source, OpenCliRequest | None] = {
    Source.TWITTER: OpenCliRequest(
        surface="twitter",
        command="whoami",
        args=("-f", "json"),
        timeout_seconds=30,
    ),
    Source.REDDIT: OpenCliRequest(
        surface="reddit",
        command="search",
        args=("finch", "--limit", "1", "-f", "json"),
        timeout_seconds=45,
    ),
    Source.V2EX: OpenCliRequest(
        surface="v2ex",
        command="hot",
        args=("-f", "json"),
        timeout_seconds=45,
    ),
    Source.WEIXIN: OpenCliRequest(
        surface="weixin",
        command="search",
        args=("AI Agent", "--limit", "1", "-f", "json"),
        timeout_seconds=45,
    ),
    Source.XIAOHONGSHU: OpenCliRequest(
        surface="xiaohongshu",
        command="search",
        args=("test", "--limit", "1", "-f", "json"),
        timeout_seconds=45,
    ),
    Source.GITHUB: None,  # checked via gh binary, not opencli
}


def _gh_status(run_fn: Callable[[list[str], float], dict]) -> DoctorSourceReport:
    ver = run_fn(["gh", "--version"], 10.0)
    auth = run_fn(["gh", "auth", "status"], 15.0)
    if not ver.get("ok"):
        return DoctorSourceReport(
            source=Source.GITHUB,
            status=SourceStatus.UNAVAILABLE,
            detail="gh binary unavailable",
        )
    if not auth.get("ok"):
        return DoctorSourceReport(
            source=Source.GITHUB,
            status=SourceStatus.AUTH_REQUIRED,
            detail="gh auth required",
            commands_seen=["gh"],
        )
    return DoctorSourceReport(
        source=Source.GITHUB,
        status=SourceStatus.READY,
        detail=(ver.get("stdout") or "").splitlines()[0] if ver.get("stdout") else "ok",
        commands_seen=["gh"],
    )


def _kind_to_status(kind: ResultKind) -> SourceStatus:
    if kind in {ResultKind.SUCCESS, ResultKind.EMPTY}:
        return SourceStatus.READY
    if kind == ResultKind.AUTH_REQUIRED:
        return SourceStatus.AUTH_REQUIRED
    if kind in {ResultKind.BRIDGE_DOWN, ResultKind.TIMEOUT, ResultKind.ERROR}:
        return SourceStatus.DEGRADED
    return SourceStatus.UNAVAILABLE


def run_doctor(
    *,
    gateway: OpenCliGateway | None = None,
    run_fn: Callable[[list[str], float], dict] | None = None,
    profile: str | None = None,
    sources: list[Source] | None = None,
    run_smoke: bool = True,
) -> DoctorReport:
    """执行自检：opencli doctor + list + 各源 smoke（可选）。"""
    runner = run_fn or _run
    gw = gateway or OpenCliGateway(run_fn=runner, profile=profile)
    doctor_raw = runner(["opencli", "doctor"], 30.0)
    opencli_ok = bool(doctor_raw.get("ok"))
    opencli_detail = (doctor_raw.get("stderr") or doctor_raw.get("stdout") or "").strip()[
        :500
    ]

    caps = fetch_capabilities(run_fn=runner, profile=profile)
    gw.capability_snapshot_id = caps.snapshot_id

    target = sources or list(Source)
    reports: list[DoctorSourceReport] = []

    for src in target:
        if src == Source.GITHUB:
            reports.append(_gh_status(runner))
            continue

        surface = src.value
        seen = list(caps.surfaces.get(surface, []))
        # Also accept policy allowlist as "known" when list is empty (offline tests).
        if not seen:
            seen = sorted(c.split(" ", 1)[-1] for c in allowed_commands_for(surface))

        if surface not in caps.surfaces and not opencli_ok:
            reports.append(
                DoctorSourceReport(
                    source=src,
                    status=SourceStatus.UNAVAILABLE,
                    detail="opencli unavailable; cannot probe",
                    commands_seen=seen,
                )
            )
            continue

        if surface not in caps.surfaces and caps.surfaces:
            # Capability list loaded but surface missing → UNAVAILABLE
            reports.append(
                DoctorSourceReport(
                    source=src,
                    status=SourceStatus.UNAVAILABLE,
                    detail=f"surface {surface!r} not in opencli list",
                    commands_seen=seen,
                )
            )
            continue

        smoke = _SMOKE.get(src)
        if not run_smoke or smoke is None:
            status = SourceStatus.READY if opencli_ok else SourceStatus.DEGRADED
            detail = "capability present; smoke skipped"
            reports.append(
                DoctorSourceReport(
                    source=src, status=status, detail=detail, commands_seen=seen
                )
            )
            continue

        result = gw.run(smoke)
        status = _kind_to_status(result.kind)
        detail = result.stderr_summary or result.kind.value
        reports.append(
            DoctorSourceReport(
                source=src, status=status, detail=detail, commands_seen=seen
            )
        )

    return DoctorReport(
        opencli_ok=opencli_ok,
        opencli_detail=opencli_detail,
        capability_snapshot_id=caps.snapshot_id,
        profile=profile,
        sources=reports,
    )


def format_doctor_report(report: DoctorReport) -> str:
    """人类可读 doctor 输出。"""
    lines = [
        f"opencli: {'ok' if report.opencli_ok else 'FAIL'}",
        f"  detail: {report.opencli_detail or '(none)'}",
        f"  capability_snapshot: {report.capability_snapshot_id or '(none)'}",
        f"  profile: {report.profile or '(default)'}",
        "sources:",
    ]
    for s in report.sources:
        lines.append(f"  {s.source.value}: {s.status.value} — {s.detail}")
    return "\n".join(lines)
