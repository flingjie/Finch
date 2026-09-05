from datetime import UTC, datetime, timedelta

from finch.content.checkers.base import CheckResult
from finch.content.jobs import (
    AuthorPosition,
    ContentJob,
    ContentJobStatus,
    IntendedEffect,
    SuccessCriterion,
)
from finch.content.models import ClaimRef, Draft, DraftKind
from finch.evidence.models import ClaimConfidence
from finch.review.models import Feedback, OutcomeAssessment, ReviewAction, ReviewDecision
from finch.review.weekly import (
    NextWeekPlan,
    WeeklyReport,
    _build_narrative,
    render_weekly,
    weekly_analysis,
)
from finch.storage.database import Store
from finch.storage.repositories import (
    ContentJobRepository,
    CriticReportRepository,
    DraftRepository,
    FeedbackRepository,
    ReviewRepository,
)


def _draft(id: str, *, content_job_id: str | None = None, candidate_id: str | None = "t1") -> Draft:
    return Draft(
        id=id,
        kind=DraftKind.REPLY,
        candidate_id=candidate_id,
        body="hi",
        content_job_id=content_job_id,
        claims=[ClaimRef(statement="x", evidence_card_id="ev_1",
                         confidence=ClaimConfidence.VERIFIED)],
    )


def _decision(draft_id: str, action: ReviewAction, reason: str | None = None,
              decided_at: datetime | None = None) -> ReviewDecision:
    return ReviewDecision(id=f"rev_{draft_id}", draft_id=draft_id, action=action,
                          reason=reason, decided_at=decided_at or datetime(2026, 1, 1))


def _job(job_id: str = "job1", *, decision: str = "", tradeoff: str = "",
         status: ContentJobStatus = ContentJobStatus.READY) -> ContentJob:
    return ContentJob(
        id=job_id,
        source_card_ids=["ev1"],
        reader_problem="readers don't know how to rate limit",
        audience="backend engineers",
        intended_effect=IntendedEffect(understand="token bucket rate limiting"),
        author_position=AuthorPosition(claim="use token buckets", decision=decision,
                                       tradeoff=tradeoff),
        success_criteria=[SuccessCriterion(id="c1", description="critic passes",
                                           measurement="critic")],
        recommended_format=DraftKind.REPLY,
        status=status,
    )


def _repos(store: Store):
    return (
        DraftRepository(store),
        ReviewRepository(store),
        FeedbackRepository(store),
        ContentJobRepository(store),
        CriticReportRepository(store),
    )


def test_weekly_analysis_counts(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    drafts, reviews, feedbacks, jobs, critic = _repos(store)

    drafts.upsert_draft(_draft("d1"))
    drafts.upsert_draft(_draft("d2"))
    drafts.upsert_draft(_draft("d3"))
    reviews.save_review(_decision("d1", ReviewAction.APPROVE))
    reviews.save_review(_decision("d2", ReviewAction.SKIP, "not_now"))
    reviews.save_review(_decision("d3", ReviewAction.SKIP, "evidence_insufficient"))
    feedbacks.save_feedback(Feedback(draft_id="d1", published_url="https://x.com/u/status/1",
                                     recorded_at=datetime(2026, 1, 2)))

    report = weekly_analysis(drafts, reviews, feedbacks, jobs, critic)
    assert report.reviewed_drafts == 3
    assert report.approved == 1
    assert report.skipped == 2
    assert report.approval_rate == 1 / 3
    assert report.skip_reasons == {"not_now": 1, "evidence_insufficient": 1}
    assert report.published_draft_ids == ["d1"]
    assert report.published_candidate_ids == ["t1"]


def test_weekly_approval_rate_excludes_pending(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    drafts, reviews, feedbacks, jobs, critic = _repos(store)
    drafts.upsert_draft(_draft("d1"))
    drafts.upsert_draft(_draft("d2"))  # 待审，无决策
    reviews.save_review(_decision("d1", ReviewAction.APPROVE))

    report = weekly_analysis(drafts, reviews, feedbacks, jobs, critic)
    assert report.reviewed_drafts == 1
    assert report.approval_rate == 1.0  # 1/1，而非 1/2


def test_weekly_since_filters(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    drafts, reviews, feedbacks, jobs, critic = _repos(store)
    old = datetime(2026, 1, 1, tzinfo=UTC)
    new = old + timedelta(days=10)
    drafts.upsert_draft(_draft("d1"))
    drafts.upsert_draft(_draft("d2"))
    reviews.save_review(_decision("d1", ReviewAction.APPROVE, decided_at=old))
    reviews.save_review(_decision("d2", ReviewAction.SKIP, "not_now", decided_at=new))

    report = weekly_analysis(drafts, reviews, feedbacks, jobs, critic,
                             since=old + timedelta(days=7))
    assert report.reviewed_drafts == 1  # 只有 d2 在窗口内
    assert report.skipped == 1


def test_weekly_revised_preserved_across_approve(tmp_path):
    from finch.review.service import ReviewService

    store = Store(tmp_path / "db.sqlite")
    store.init()
    drafts, reviews, feedbacks, jobs, critic = _repos(store)
    drafts.upsert_draft(_draft("d1", content_job_id="job1"))

    svc = ReviewService(drafts, reviews)
    svc.revise("d1", "fixed body")
    svc.approve("d1")

    report = weekly_analysis(drafts, reviews, feedbacks, jobs, critic)
    assert report.reviewed_drafts == 1
    assert report.approved == 1
    assert report.revised == 1  # 修改次数保留，未被 approve 覆盖


def test_weekly_analysis_empty(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    drafts, reviews, feedbacks, jobs, critic = _repos(store)
    report = weekly_analysis(drafts, reviews, feedbacks, jobs, critic)
    assert report.reviewed_drafts == 0
    assert report.approval_rate == 0.0
    assert report.skip_reasons == {}
    assert report.evidence_coverage is None
    assert report.job_completion_rate is None


def test_render_weekly(tmp_path):
    r = WeeklyReport(reviewed_drafts=2, approved=1, skipped=1, approval_rate=0.5,
                     skip_reasons={"not_now": 1}, published_draft_ids=["d1"],
                     published_candidate_ids=["t1"])
    out = render_weekly(r)
    assert "已审核草稿: 2" in out
    assert "批准率: 50%" in out
    assert "not_now: 1" in out
    assert "已发布候选: t1" in out


def test_evidence_coverage(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    drafts, reviews, feedbacks, jobs, critic = _repos(store)
    drafts.upsert_draft(_draft("d1", content_job_id="job1"))
    drafts.upsert_draft(_draft("d2", content_job_id="job2"))
    critic.upsert_report("d1", 0, [CheckResult(checker="evidence", passed=True,
                                                severity="low")], "pass")
    critic.upsert_report("d2", 0, [CheckResult(checker="evidence", passed=False,
                                                severity="hard_fail")], "reject")

    report = weekly_analysis(drafts, reviews, feedbacks, jobs, critic)
    assert report.evidence_coverage == 0.5


def test_decision_density(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    drafts, reviews, feedbacks, jobs, critic = _repos(store)
    drafts.upsert_draft(_draft("d1", content_job_id="job1"))
    drafts.upsert_draft(_draft("d2", content_job_id="job2"))
    jobs.upsert_job(_job("job1", decision="use token buckets", tradeoff="lose burst"))
    jobs.upsert_job(_job("job2", decision="", tradeoff=""))
    feedbacks.save_feedback(Feedback(draft_id="d1", published_url="https://x.com/1",
                                     recorded_at=datetime(2026, 1, 2)))
    feedbacks.save_feedback(Feedback(draft_id="d2", published_url="https://x.com/2",
                                     recorded_at=datetime(2026, 1, 2)))

    report = weekly_analysis(drafts, reviews, feedbacks, jobs, critic)
    assert report.decision_density == 0.5


def test_generic_sentence_rate_proxy(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    drafts, reviews, feedbacks, jobs, critic = _repos(store)
    drafts.upsert_draft(_draft("d1", content_job_id="job1"))
    drafts.upsert_draft(_draft("d2", content_job_id="job2"))
    critic.upsert_report("d1", 0, [CheckResult(checker="portability", passed=False,
                                                severity="high")], "rewrite")
    critic.upsert_report("d2", 0, [CheckResult(checker="specificity", passed=True,
                                                severity="low")], "pass")

    report = weekly_analysis(drafts, reviews, feedbacks, jobs, critic)
    assert report.generic_sentence_rate == 0.5


def test_human_correction_rate(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    drafts, reviews, feedbacks, jobs, critic = _repos(store)
    drafts.upsert_draft(_draft("d1", content_job_id="job1"))
    drafts.upsert_draft(_draft("d2", content_job_id="job2"))
    drafts.upsert_draft(_draft("d3", content_job_id="job3"))
    reviews.save_review(_decision("d1", ReviewAction.SKIP, "fact_error"))
    reviews.save_review(_decision("d2", ReviewAction.SKIP, "no_clear_position"))
    reviews.save_review(_decision("d3", ReviewAction.APPROVE))

    report = weekly_analysis(drafts, reviews, feedbacks, jobs, critic)
    assert report.human_correction_rate == 2 / 3


def test_job_completion_rate(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    drafts, reviews, feedbacks, jobs, critic = _repos(store)
    drafts.upsert_draft(_draft("d1", content_job_id="job1"))
    drafts.upsert_draft(_draft("d2", content_job_id="job2"))
    drafts.upsert_draft(_draft("d3", content_job_id="job3"))
    feedbacks.save_feedback(Feedback(
        draft_id="d1", recorded_at=datetime(2026, 1, 2),
        outcome=OutcomeAssessment(job_completed="yes")))
    feedbacks.save_feedback(Feedback(
        draft_id="d2", recorded_at=datetime(2026, 1, 2),
        outcome=OutcomeAssessment(job_completed="partly")))
    feedbacks.save_feedback(Feedback(
        draft_id="d3", recorded_at=datetime(2026, 1, 2),
        outcome=OutcomeAssessment(job_completed="no")))

    report = weekly_analysis(drafts, reviews, feedbacks, jobs, critic)
    assert report.job_completion_rate == 2 / 3


def test_useful_reply_rate(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    drafts, reviews, feedbacks, jobs, critic = _repos(store)
    drafts.upsert_draft(_draft("d1", content_job_id="job1", candidate_id="c1"))
    drafts.upsert_draft(_draft("d2", content_job_id="job2", candidate_id="c2"))
    feedbacks.save_feedback(Feedback(
        draft_id="d1", recorded_at=datetime(2026, 1, 2),
        outcome=OutcomeAssessment(job_completed="yes", useful_reply_count=2)))
    feedbacks.save_feedback(Feedback(
        draft_id="d2", recorded_at=datetime(2026, 1, 2),
        outcome=OutcomeAssessment(job_completed="yes", useful_reply_count=0)))

    report = weekly_analysis(drafts, reviews, feedbacks, jobs, critic)
    assert report.useful_reply_rate == 0.5


def test_do_not_write_rate(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    drafts, reviews, feedbacks, jobs, critic = _repos(store)
    jobs.upsert_job(_job("job1", status=ContentJobStatus.DO_NOT_WRITE))
    jobs.upsert_job(_job("job2", status=ContentJobStatus.READY))
    jobs.upsert_job(_job("job3", status=ContentJobStatus.READY))

    report = weekly_analysis(drafts, reviews, feedbacks, jobs, critic)
    assert report.do_not_write_rate == 1 / 3


def test_rewrite_rounds(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    drafts, reviews, feedbacks, jobs, critic = _repos(store)
    drafts.upsert_draft(_draft("d1", content_job_id="job1"))
    drafts.upsert_draft(_draft("d2", content_job_id="job2"))
    drafts.upsert_draft(_draft("d3", content_job_id="job3"))
    # 遗留草稿不参与新指标，即使有 rewrite 报告。
    drafts.upsert_draft(_draft("legacy", content_job_id=None))
    critic.upsert_report(
        "d1", 0, [CheckResult(checker="specificity", passed=False, severity="high")],
        "rewrite",
    )
    critic.upsert_report(
        "d1", 1, [CheckResult(checker="specificity", passed=True, severity="low")], "pass"
    )
    critic.upsert_report(
        "d2", 0, [CheckResult(checker="evidence", passed=True, severity="low")], "pass"
    )
    critic.upsert_report(
        "d3", 0, [CheckResult(checker="portability", passed=False, severity="high")],
        "rewrite",
    )
    critic.upsert_report(
        "d3", 1, [CheckResult(checker="portability", passed=False, severity="high")],
        "rewrite",
    )
    critic.upsert_report(
        "d3", 2, [CheckResult(checker="evidence", passed=False, severity="hard_fail")],
        "reject",
    )
    critic.upsert_report(
        "legacy", 0, [CheckResult(checker="specificity", passed=False, severity="high")],
        "rewrite",
    )

    report = weekly_analysis(drafts, reviews, feedbacks, jobs, critic)
    assert report.rewrite_rounds == {"d1": 1, "d2": 0, "d3": 2}
    assert report.rewritten_drafts == 2



def test_legacy_drafts_excluded_from_new_metrics(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    drafts, reviews, feedbacks, jobs, critic = _repos(store)
    # 遗留草稿：无 content_job_id，但有发布与结果评估。
    drafts.upsert_draft(_draft("legacy", content_job_id=None))
    feedbacks.save_feedback(Feedback(
        draft_id="legacy", published_url="https://x.com/legacy",
        recorded_at=datetime(2026, 1, 2),
        outcome=OutcomeAssessment(job_completed="no")))
    jobs.upsert_job(_job("job1", decision="d", tradeoff="t"))

    report = weekly_analysis(drafts, reviews, feedbacks, jobs, critic)
    assert report.decision_density is None  # 遗留草稿不计入分母
    assert report.job_completion_rate is None  # 遗留草稿的 outcome 不计入


def test_render_weekly_includes_recommendations(tmp_path):
    r = WeeklyReport(reviewed_drafts=1, approved=1, approval_rate=1.0,
                     job_completion_rate=0.2, do_not_write_rate=0.1)
    out = render_weekly(r)
    assert "建议" in out
    assert "任务完成率" in out
    assert "不写率" in out
    assert "不视为失败" in out


def _backdate_report(store, record_id, ts):
    """直接改写某条 CriticReportRecord.updated_at，用于时间窗过滤测试。"""
    from sqlmodel import Session

    from finch.storage.repositories import CriticReportRecord

    with Session(store.engine) as session:
        record = session.get(CriticReportRecord, record_id)
        assert record is not None
        record.updated_at = ts
        session.commit()


def test_weekly_since_filters_evidence_coverage(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    drafts, reviews, feedbacks, jobs, critic = _repos(store)
    drafts.upsert_draft(_draft("d1", content_job_id="job1"))
    drafts.upsert_draft(_draft("d2", content_job_id="job2"))
    critic.upsert_report(
        "d1", 0, [CheckResult(checker="evidence", passed=True, severity="low")], "pass"
    )
    critic.upsert_report(
        "d2", 0, [CheckResult(checker="evidence", passed=False, severity="hard_fail")], "reject"
    )
    now = datetime(2026, 1, 10, tzinfo=UTC)
    _backdate_report(store, "d1:0", now - timedelta(days=10))
    _backdate_report(store, "d2:0", now)

    all_time = weekly_analysis(drafts, reviews, feedbacks, jobs, critic)
    assert all_time.evidence_coverage == 0.5

    windowed = weekly_analysis(
        drafts, reviews, feedbacks, jobs, critic, since=now - timedelta(days=1)
    )
    # 窗口内只有 d2（evidence 失败），窗口外的 d1 不计入。
    assert windowed.evidence_coverage == 0.0


def test_weekly_since_filters_generic_sentence_rate(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    drafts, reviews, feedbacks, jobs, critic = _repos(store)
    drafts.upsert_draft(_draft("d1", content_job_id="job1"))
    drafts.upsert_draft(_draft("d2", content_job_id="job2"))
    critic.upsert_report(
        "d1", 0, [CheckResult(checker="portability", passed=False, severity="high")], "rewrite"
    )
    critic.upsert_report(
        "d2", 0, [CheckResult(checker="specificity", passed=True, severity="low")], "pass"
    )
    now = datetime(2026, 1, 10, tzinfo=UTC)
    _backdate_report(store, "d1:0", now - timedelta(days=10))
    _backdate_report(store, "d2:0", now)

    all_time = weekly_analysis(drafts, reviews, feedbacks, jobs, critic)
    assert all_time.generic_sentence_rate == 0.5

    windowed = weekly_analysis(
        drafts, reviews, feedbacks, jobs, critic, since=now - timedelta(days=1)
    )
    assert windowed.generic_sentence_rate == 0.0


def test_render_weekly_empty_metrics_show_no_data(tmp_path):
    r = WeeklyReport()
    out = render_weekly(r)
    assert "无数据" in out
    assert "证据覆盖: 无数据" in out
    # 无分母时不应输出误导性的「继续/停止」判定。
    assert "→ 停止" not in out
    assert "→ 继续" not in out


def test_weekly_narrative_evidence_insufficient(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    drafts, reviews, feedbacks, jobs, critic = _repos(store)
    report = weekly_analysis(drafts, reviews, feedbacks, jobs, critic)
    assert report.weekly_insight == "evidence insufficient"
    assert report.next_week.one_thing == "evidence insufficient"
    assert report.next_week.one_experiment == "evidence insufficient"
    assert report.next_week.stop_doing == "evidence insufficient"


def test_build_narrative_ties_judgments_to_data():
    insight, plan = _build_narrative({
        "evidence_coverage": 0.9,
        "decision_density": 0.5,
        "generic_sentence_rate": 0.2,
        "human_correction_rate": 0.6,
        "job_completion_rate": 0.1,
        "useful_reply_rate": None,
    })
    # 最极端偏差是 job_completion_rate（健康分 0.1，偏离 0.7 最远）。
    assert "任务完成率" in insight
    assert "10%" in insight
    # 最强指标 → 继续强化。
    assert "证据覆盖" in plan.one_thing
    assert "90%" in plan.one_thing
    # 最弱指标 → 一个假设 + 一个停止项，均引用指标数值。
    assert "任务完成率" in plan.one_experiment
    assert "10%" in plan.one_experiment
    assert "任务完成率" in plan.stop_doing
    assert "10%" in plan.stop_doing


def test_build_narrative_no_stop_when_all_healthy():
    insight, plan = _build_narrative({
        "evidence_coverage": 0.9,
        "decision_density": 0.8,
        "generic_sentence_rate": 0.1,
        "human_correction_rate": 0.2,
        "job_completion_rate": 0.9,
        "useful_reply_rate": 0.8,
    })
    assert "无需停止" in plan.stop_doing


def test_render_weekly_includes_narrative():
    r = WeeklyReport(
        weekly_insight="任务完成率 是本周最大短板（10%）",
        next_week=NextWeekPlan(
            one_thing="继续强化 证据覆盖（当前 90%）",
            one_experiment=(
                "验证假设：确定性地选出一个 primary job 可提高任务完成率"
                "（依据：任务完成率 10%）"
            ),
            stop_doing="停止：生成读者无法完成的宽泛任务（依据：任务完成率 10%）",
        ),
    )
    out = render_weekly(r)
    assert "## 本周洞察" in out
    assert "## 下周计划" in out
    assert "任务完成率 是本周最大短板（10%）" in out
    assert "继续强化 证据覆盖（当前 90%）" in out
    assert "验证假设：确定性地选出一个 primary job 可提高任务完成率" in out
    assert "停止：生成读者无法完成的宽泛任务（依据：任务完成率 10%）" in out
