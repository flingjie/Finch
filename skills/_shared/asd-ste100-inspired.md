# Finch ASD-STE100-inspired clarity rules

Project adaptations inspired by ASD-STE100 (Simplified Technical English).
Official FAQ: https://www.asd-ste100.org/STE_faq.html
Do **not** claim strict ASD-STE100 compliance.

Rules version: `finch-clarity-v1`

## Presets

| Preset | When | Behaviour |
|---|---|---|
| `asd-ste100-inspired` (default) | Chinese drafts, social posts, explanations | Local edits; keep story, tone, useful metaphors; fix ambiguity, abstraction, term drift |
| `asd-ste100-technical` | Procedures, how-tos, technical steps | Explicit actor, action, prerequisites; prefer one instruction per sentence; these requirements are not relaxed by user style instructions |

Conflict priority: **facts and original meaning > clarity > personal voice > neat copy**.

Default preset: user instruction may change *method* (full rewrite, keep a metaphor).
Technical preset: actor / action / prerequisites are not relaxed by instruction.
Never invent actors, examples, measurements, or numbers.

## Chinese adaptations

- Do not apply English word-count caps or unverified Chinese character hard limits.
- Prefer concrete verbs and consistent terms; keep necessary narrative.

## Rules (CL01–CL08)

| ID | Rule | Edit behaviour |
|---|---|---|
| CL01 | One main idea per sentence when practical | Split independent judgments; keep needed causality |
| CL02 | Actor, action, object clear | State who did what to what; never invent unknown actors |
| CL03 | Same concept, same name | Unify synonyms; keep real conceptual differences |
| CL04 | Abstract claims need concrete support | Name the missing fact; do not invent metrics |
| CL05 | Prerequisites near the advice | Condition before action or conclusion |
| CL06 | Steps must be executable | In technical preset, split commands; name object and checks |
| CL07 | One topic per paragraph | Drop repeated setup; fix sudden topic jumps |
| CL08 | Simplify without changing meaning | Keep negation, numbers, scope, probability, conditions, attribution, uncertainty |

## Output contract for editors

- Prefer local edits; at most three high-value explained changes with rule IDs.
- If clarity needs missing facts, list gaps instead of filling them.
- Check final wording for meaning drift (`passed` / `needs_review`).
- Keywords ASD-STE100 may trigger editing; do not force them into published prose.

## Safe example

Bad abstract: 「通过优化证据处理机制，显著提升系统性能。」
Feedback: missing concrete change and metric — ask which of latency, cost, or accuracy changed.
After author supplies parallelization and 60s→25s on one run:
「我把证据请求从串行改成并行。这次运行的耗时从 60 秒降到 25 秒。」
Do not expand to “always” or all runs.
