"""从实验记录生成表达候选输入（复用 idea-to-draft / VoiceProfile 路径）。"""

from __future__ import annotations

from finch.collisions.models import MicroExperiment


def experiment_to_idea_seed(experiment: MicroExperiment) -> dict:
    """把实验结局拆成「实验事实 / 个人判断 / 开放问题」三块，供草稿引用。

    不直接写 AuthorIdea；由用户确认后走 ``finch ideas create`` / drafts。
    """
    fact = experiment.outcome.strip() or experiment.observation_plan
    judgment = ""
    if experiment.result_kind == "method":
        judgment = f"可迁移方法：在相似不确定性下采用「{experiment.minimal_action}」"
    elif experiment.result_kind == "failure_review":
        judgment = f"失败边界：在「{experiment.stop_condition}」条件下假设未成立"
    elif experiment.result_kind == "artifact":
        judgment = f"产出物来自假设「{experiment.hypothesis}」的验证过程"
    open_q = f"该机制是否适用于假设之外的场景？原假设：{experiment.hypothesis}"
    return {
        "experiment_id": experiment.experiment_id,
        "collision_id": experiment.collision_id,
        "experiment_fact": fact,
        "personal_judgment": judgment,
        "open_question": open_q,
        "evidence_notes": list(experiment.evidence_notes),
        "source_refs": [experiment.experiment_id, experiment.collision_id],
    }
