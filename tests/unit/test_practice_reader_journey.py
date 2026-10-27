"""reader-diagnosis / contrast-rewrite（扩展 diagnose / feedback 命令）。"""

import pytest

from finch.practice.models import LocalFeedback, ReaderDiagnosis
from finch.practice.service import PracticeService
from finch.storage.repositories import PracticeSessionRepository
from finch.storage.workspace import Workspace


class _Runner:
    def __init__(self, diagnosis=None, feedback=None):
        self.diagnosis = diagnosis or ReaderDiagnosis(
            opening_hook="开头好",
            reader_expectation="预期有反差",
            cognitive_turning_point="第二段",
            weak_sections=["第三段没有证据"],
            ending_effect="留下行动",
            primary_suggestion="先补证据",
        )
        self.feedback = feedback or LocalFeedback(
            keep="保留这句",
            key_location="选定段落",
            alternative_a="写法 A",
            effect_a="更具体",
            risk_a="略啰嗦",
            alternative_b="写法 B",
            effect_b="更有力",
            risk_b="略绝对",
            difference="A 具体 B 有力",
            rewrite_task="自己写一版",
        )
        self.prompts: list[str] = []

    def run(self, prompt, output_model, **kw):
        self.prompts.append(prompt)
        if output_model is ReaderDiagnosis:
            return self.diagnosis
        if output_model is LocalFeedback:
            return self.feedback
        raise AssertionError(output_model)


def _service(tmp_path, runner=None):
    return PracticeService(PracticeSessionRepository(Workspace(tmp_path)), runner or _Runner())


def _session(tmp_path, initial="一版草稿") -> str:
    return _service(tmp_path).start(initial_attempt=initial).id


# —— reader_diagnose ——


def test_reader_diagnose_stores_and_no_turn(tmp_path):
    runner = _Runner()
    svc = _service(tmp_path, runner)
    sid = _session(tmp_path)
    s = svc.reader_diagnose(sid, target_reader="小团队", reader_journey="好奇→怀疑→理解")
    assert s.reader_diagnosis is not None
    assert s.reader_diagnosis.opening_hook == "开头好"
    assert s.turns == []  # 不建 turn
    assert "小团队" in runner.prompts[-1]
    assert "好奇→怀疑→理解" in runner.prompts[-1]


def test_reader_diagnose_works_without_reader_journey(tmp_path):
    svc = _service(tmp_path)
    sid = _session(tmp_path)
    s = svc.reader_diagnose(sid)
    assert s.reader_diagnosis is not None
    assert s.reader_diagnosis.primary_suggestion == "先补证据"


def test_reader_diagnose_requires_text(tmp_path):
    svc = _service(tmp_path)
    s = svc.start(material="只有素材")
    with pytest.raises(ValueError):
        svc.reader_diagnose(s.id)


def test_reader_diagnose_round_trips_yaml(tmp_path):
    svc = _service(tmp_path)
    sid = _session(tmp_path)
    svc.reader_diagnose(sid)
    reloaded = PracticeSessionRepository(Workspace(tmp_path)).get(sid)
    assert reloaded is not None
    assert reloaded.reader_diagnosis is not None
    assert reloaded.reader_diagnosis.cognitive_turning_point == "第二段"


# —— contrast-rewrite（feedback --location） ——


def test_feedback_with_location_fills_effect_risk(tmp_path):
    runner = _Runner()
    svc = _service(tmp_path, runner)
    sid = _session(tmp_path)
    s = svc.feedback(sid, location="选定段落")
    fb = s.feedback_rounds[-1]
    assert fb.key_location == "选定段落"
    assert fb.effect_a == "更具体"
    assert fb.risk_a == "略啰嗦"
    assert fb.effect_b == "更有力"
    assert fb.risk_b == "略绝对"
    assert "选定段落" in runner.prompts[-1]


def test_feedback_without_location_uses_feedback_prompt(tmp_path):
    runner = _Runner()
    svc = _service(tmp_path, runner)
    sid = _session(tmp_path)
    svc.feedback(sid)
    assert "Selected approach" in runner.prompts[-1]
    assert "Selected paragraph" not in runner.prompts[-1]


def test_local_feedback_new_fields_default_empty():
    fb = LocalFeedback(keep="保留")
    assert fb.effect_a == ""
    assert fb.risk_a == ""
    assert fb.effect_b == ""
    assert fb.risk_b == ""


def test_legacy_feedback_yaml_reads_new_fields_empty(tmp_path):
    import yaml

    ws = Workspace(tmp_path)
    ws.ensure()
    path = ws.dir("practice") / "practice_legacy_fb.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "id": "practice_legacy_fb",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "feedback_rounds": [
                    {
                        "keep": "保留",
                        "key_location": "某句",
                        "alternative_a": "A",
                        "alternative_b": "B",
                    }
                ],
            },
            sort_keys=False,
            allow_unicode=True,
        )
    )
    s = PracticeSessionRepository(ws).get("practice_legacy_fb")
    assert s is not None
    fb = s.feedback_rounds[0]
    assert fb.effect_a == ""
    assert fb.risk_a == ""
    assert fb.effect_b == ""
    assert fb.risk_b == ""
