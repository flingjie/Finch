"""ASD-STE100-inspired clarity models and preset parsing."""

from finch.content.clarity import (
    RULES_VERSION,
    ClarityChange,
    ClarityEditOutput,
    ClarityReview,
    parse_clarity_preset,
)
from finch.content.models import Draft, DraftKind
from finch.content.writer import rewrite_with_instruction
from finch.storage.repositories import DraftRepository
from finch.storage.workspace import Workspace


def test_parse_clarity_preset_default_inspired():
    assert parse_clarity_preset("make it shorter") == "asd-ste100-inspired"
    assert parse_clarity_preset("用 ASD-STE100 的原则优化") == "asd-ste100-inspired"
    assert parse_clarity_preset("asd-ste100-inspired check") == "asd-ste100-inspired"


def test_parse_clarity_preset_technical():
    assert parse_clarity_preset("asd-ste100-technical") == "asd-ste100-technical"
    assert parse_clarity_preset("ASD-STE100-TECHNICAL please") == "asd-ste100-technical"
    assert parse_clarity_preset("改成清晰的技术操作说明") == "asd-ste100-technical"
    assert parse_clarity_preset("写成技术操作说明") == "asd-ste100-technical"


def test_clarity_review_max_three_changes():
    changes = [
        ClarityChange(rule_id=f"CL0{i}", before="a", after="b", reason="r")
        for i in range(1, 4)
    ]
    review = ClarityReview(
        preset="asd-ste100-inspired",
        rules_version=RULES_VERSION,
        changes=changes,
        meaning_check="passed",
    )
    assert len(review.changes) == 3


def test_clarity_edit_output_shape():
    out = ClarityEditOutput(
        body="改后正文",
        clarity_review=ClarityReview(
            preset="asd-ste100-inspired",
            rules_version=RULES_VERSION,
            changes=[],
            meaning_check="passed",
            missing_information=["缺少耗时指标"],
        ),
    )
    assert out.body.startswith("改")
    assert out.clarity_review.missing_information == ["缺少耗时指标"]


def test_draft_clarity_review_frontmatter_roundtrip(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    review = ClarityReview(
        preset="asd-ste100-technical",
        rules_version=RULES_VERSION,
        changes=[
            ClarityChange(
                rule_id="CL02",
                before="处理后更好",
                after="脚本读取 input.csv 并写出 output.csv",
                reason="主体与对象明确",
            )
        ],
        meaning_check="needs_review",
        missing_information=[],
    )
    draft = Draft(
        id="draft_clarity1",
        kind=DraftKind.ORIGINAL,
        language="zh",
        body="正文",
        claims=[],
        clarity_review=review,
    )
    DraftRepository(ws).upsert_draft(draft)
    loaded = DraftRepository(ws).get_draft("draft_clarity1")
    assert loaded is not None
    assert loaded.clarity_review is not None
    assert loaded.clarity_review.preset == "asd-ste100-technical"
    assert loaded.clarity_review.changes[0].rule_id == "CL02"


def test_draft_without_clarity_review_still_loads(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    draft = Draft(
        id="draft_old1",
        kind=DraftKind.ORIGINAL,
        language="zh",
        body="旧稿",
        claims=[],
    )
    DraftRepository(ws).upsert_draft(draft)
    loaded = DraftRepository(ws).get_draft("draft_old1")
    assert loaded is not None
    assert loaded.clarity_review is None


class _FakeRunner:
    def __init__(self, output: ClarityEditOutput):
        self.output = output
        self.prompts: list[str] = []

    def run(self, prompt: str, model):
        self.prompts.append(prompt)
        assert model is ClarityEditOutput
        return self.output


def test_rewrite_with_instruction_applies_clarity_and_overwrites_preset():
    draft = Draft(
        id="draft_x",
        kind=DraftKind.ORIGINAL,
        language="zh",
        body="通过优化显著提升性能。",
        claims=[],
        content_job_id=None,
        run_id="idea",
    )
    llm_out = ClarityEditOutput(
        body="请补充具体改动与指标后再写性能结论。",
        clarity_review=ClarityReview(
            preset="asd-ste100-technical",  # model lies — code must overwrite
            rules_version="wrong",
            changes=[],
            meaning_check="passed",
            missing_information=["缺少改动方式与耗时/准确率指标"],
        ),
    )
    runner = _FakeRunner(llm_out)
    revised = rewrite_with_instruction(
        runner, draft, "用 ASD-STE100 的原则优化，保留语气", {}, None
    )
    assert revised.id == "draft_x"
    assert revised.run_id == "idea"
    assert revised.body == llm_out.body
    assert revised.clarity_review is not None
    assert revised.clarity_review.preset == "asd-ste100-inspired"
    assert revised.clarity_review.rules_version == RULES_VERSION
    assert "ASD-STE100" in runner.prompts[0] or "clarity" in runner.prompts[0].casefold()
    assert "CL01" in runner.prompts[0] or "asd-ste100-inspired" in runner.prompts[0]
