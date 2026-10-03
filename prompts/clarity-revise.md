Apply the Finch ASD-STE100-inspired clarity rules below.
For Chinese content, use the project's Chinese adaptations.
Do not claim strict ASD-STE100 compliance.

Keep the author's claims, evidence, numbers, conditions,
negation, uncertainty, and source attribution unchanged.
Prefer local edits. Do not invent actors, examples, or measurements.
Preserve useful narrative and metaphors in the default preset.
Use explicit actions and prerequisites in the technical preset.
Explain at most three high-value edits with their rule IDs.
If clarity requires missing facts, identify the gap instead of filling it.
Check the final wording for changes in meaning.

Active preset (chosen by code; echo it in clarity_review.preset): {preset}

## Shared rules
{rules}

## User instruction
{instruction}

{job_context}## Original draft
{body}

## Evidence cards
{cards}

Return JSON matching ClarityEditOutput: body (revised full text) and clarity_review
(preset, rules_version, changes[≤3] with rule_id/before/after/reason,
meaning_check passed|needs_review, missing_information).
