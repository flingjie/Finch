# Extract Engineering Events

You are given multiple independent commit groups from one repository. For every group,
extract exactly ONE engineering event. Do not read files, run commands, or use any tools.
Answer only from the input groups below.

For every input group:
- extract exactly one EngineeringEvent
- preserve `group_id` unchanged
- only use commits from that group
- do not merge different groups
- do not move commits between groups
- return exactly one output item per input group

## Rules (apply per group)

- Distinguish what the code PROVES from what is INFERRED.
- `problem` / `result`: use `VERIFIED` ONLY if the diff/patch shown directly proves it. If you only see commit messages and file paths (no diff, or a truncated diff), use `SUPPORTED` — never `VERIFIED` from metadata alone.
- `decision` (why / motivation): ALWAYS `INFERRED` (or `UNKNOWN`) — the diff cannot prove motivation. Never use `VERIFIED`/`SUPPORTED` for `decision`.
- Never mark an inference as `VERIFIED`.
- If context is missing (e.g. was this a real bug or a proactive hardening?), list it in `missing_context`.
- `id` must be a stable slug, e.g. `evt_<repo-slug>_<short-topic>`.
- `topics` must be 2-5 short lowercase noun phrases that name the engineering domain shown
  in the diff (e.g. `agent harness`, `durable execution`, `evals`). Derive them only from the
  input commits. Use an empty list when no clear public-discussion topic applies; never invent
  topics that are not supported by the input.

## Input groups

{groups}

## Output

Respond with a JSON object with a single top-level key "items", one element per input group.
Each item is `{"group_id": "<unchanged>", "event": {...}}`, where `event` has fields `id`,
`repository`, `commits`, `problem`, `decision`, `result`, `missing_context`, `topics`.
`problem` / `decision` / `result` are objects `{"statement": str, "confidence": "VERIFIED|SUPPORTED|INFERRED|USER_CONFIRMED|UNKNOWN"}`.
