"""PracticeService：start / diagnose / save_revision / finish。"""

from finch.practice.models import PracticeDiagnosis, PracticeLesson
from finch.practice.service import PracticeService
from finch.storage.database import Store
from finch.storage.repositories import PracticeSessionRepository


class FakeRunner:
    def __init__(self, diagnosis, lesson):
        self.diagnosis = diagnosis
        self.lesson = lesson

    def run(self, prompt, output_model, **kw):
        if output_model is PracticeDiagnosis:
            return self.diagnosis
        if output_model is PracticeLesson:
            return self.lesson
        raise AssertionError(output_model)


def _service(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    return PracticeService(
        PracticeSessionRepository(store),
        FakeRunner(
            PracticeDiagnosis(diagnosis="最大问题是空泛", question="具体发生在哪一步？"),
            PracticeLesson(lesson="先讲具体场景再下判断"),
        ),
    )


def test_full_session(tmp_path):
    svc = _service(tmp_path)
    s = svc.start(idea_id="idea_1", initial_attempt="初稿")
    assert s.status == "started"
    s = svc.diagnose(s.id, context="idea context")
    assert s.questions_asked == ["具体发生在哪一步？"]
    s = svc.save_revision(s.id, "修订1")
    assert s.revisions == ["修订1"]
    s = svc.finish(s.id, "最终版")
    assert s.status == "finished"
    assert s.final_expression == "最终版"
    assert s.lesson == "先讲具体场景再下判断"


def test_diagnose_missing_session_raises(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    svc = PracticeService(
        PracticeSessionRepository(store),
        FakeRunner(
            PracticeDiagnosis(diagnosis="d", question="q"),
            PracticeLesson(lesson="l"),
        ),
    )
    try:
        svc.diagnose("nope")
    except KeyError:
        return
    raise AssertionError("expected KeyError")
