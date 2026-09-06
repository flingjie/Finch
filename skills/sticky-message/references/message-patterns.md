# Message Patterns

The reusable transformation moves behind the pipeline. The running example is
"Agent Engineering" — adapt it to whatever idea you are given.

## Diagnosis first

Most "copywriting" goes straight from source to rewrite:

```
原文 → LLM rewrite → 优化文案
```

That is wrong. The right path:

```
原始表达 → Message Diagnosis → 问题定位 → 重新发现核心 → Message Architecture → 传播版本
```

Diagnose the defects before fixing anything. Common defects:
- It is a classification, not a point ("Agent Engineering 包括 Harness、Loop、Graph").
- No user problem is named.
- Every concept is an abstract noun.
- No cognitive conflict.
- No reason the user needs to understand it.

## Gap templates

- Everyone thinks X. The real problem is Y.
- X is not the problem. Y is.
- The opposite of what you expect is true.
- The problem is not A. It is B.

## Concretization

Abstract concept → observable situation. Prefer a concrete failure over a
capability noun.

Example — "Harness Engineering 负责给 Agent 提供工具、环境、权限、上下文和工作空间":

```
Agent 读取错误日志
→ 修改代码
→ 忘记当前工作目录
→ 修改了错误的文件
→ 重新运行
→ 上下文丢失
→ 重复修改
→ 最终失败
```

This is not the model failing to write code. This is the agent having no reliable
working environment.

## Credibility mechanisms

1. Demonstration (strongest) — "don't believe me, try this and see."
2. Testable claim
3. Personal experience
4. Anti-authority detail (a detail that hurts the case but proves honesty)
5. Data
6. Authority

## Story skeleton

Character → Goal → Obstacle → Failure / Conflict → Discovery → Resolution.

Do not invent a narrative. Find the natural story already contained in the idea.

## Message Pyramid

A sticky message is not a slogan — it is a hierarchy:

```
            ┌───────────────┐
            │    BIG IDEA   │
            │  一句话核心观点 │
            └───────┬───────┘
                    │
      ┌─────────────┼─────────────┐
      ▼             ▼             ▼
    HOOK        EXPLANATION     STORY
    钩子          解释           场景
      │             │             │
      └─────────────┼─────────────┘
                    ▼
                 SUPPORT
             数据 / Demo / 案例
```

So the output is:

Big Idea → Hook → Explanation → Example / Story → Proof.

This suits a product homepage, a technical article, a talk, an agent demo, an
open-source README, a post, or a course.

## Worked example (Agent Engineering)

Input: "Harness Engineering 是 Agent Engineering 的重要部分。它负责给 Agent 提供
工具、环境、权限、上下文和工作空间。"

Diagnosis: too many terms, no conflict, no scene, no reason to care.

Core: 一个 Agent 再聪明，如果工作环境混乱，也无法稳定完成任务。

Current belief: Agent 做不好，是因为模型不够聪明。

Gap: 很多 Agent 的失败，其实和模型无关。

Concrete: the failure chain above.

Result:

- Core — 聪明的 Agent，也需要一个不会让它迷路的工作环境。
- Hook — 很多 Agent 的失败，不是因为它不够聪明。
- One-liner — Harness Engineering 决定 Agent 能否稳定地工作。
- Shareable — 模型负责思考，Harness 决定它怎么工作。
- Story — 一个 Agent 可以写出正确的代码，但如果它不知道自己改的是哪个文件、
  运行在哪个环境、测试结果来自哪里，它仍然会失败。
