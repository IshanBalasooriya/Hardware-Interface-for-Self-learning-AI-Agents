# ASTAGE_2_ORCHESTRATOR.md — stage 2: the Orchestrator's two calls

Read `docs/AGENTS_MASTER.md` first (section 7.4 is this stage). Nothing in `server/` changes, and `agent/pipeline.py` is not built yet.

## Before you start

Run `.\.venv\Scripts\python.exe scripts\verify_stage1.py`. It must end with `STAGE 1 VERIFY: PASS`. If not, stop and report.

## Build: `agent/orchestrator.py`

Everything in master 7.4:

1. `OrchestratorError`.
2. `has_raw_bits(text)`: true for 8 or more consecutive hex digits, or 8 consecutive characters that are each `0` or `1`. Ordinary words and numbers such as "row 3", "250 ms" or "6 times" must not trigger it.
3. `PLAN_TOOL` (`submit_plan`) and `VERDICT_TOOL` (`submit_verdict`) in the same schema style as `agent/tools.py::_schema` (write a local helper; do not import the private one).
4. `build_plan_prompt()` and `build_audit_prompt()` returning the two system prompts. Each must cover every point master 7.4 lists for it. Build grid facts from `config` where the existing prompt does; no hex examples, no fonts, no glyph tables.
5. One private function that performs a single request and reads the reply (tool call first, then JSON text, with an optional surrounding code fence), used by both calls.
6. Plan validation and normalisation in code (the rules in the master's `submit_plan` table). Verdict validation likewise.
7. `make_plan(client, model, request, catalogue)` and `audit(client, model, request, plan, evidence)`: one request, one retry with the problem stated in an added user message, then `OrchestratorError`. A client exception (network, proxy) is not retried: it raises `OrchestratorError` with the cut-down message.

The user message of the plan call is the request plus the catalogue as compact JSON. The user message of the audit call is the request, the plan's `criteria` and route, and the evidence as compact JSON.

Normalised plan returned by `make_plan` always has every key: `route`, `criteria`, `replay` (list), `stages` (list), `reference_skills` (list), `skill_name` (str or None), `note` (str), `fallback` (False).

Normalised verdict always has: `verdict`, `expected`, `problem`, `fix`, `summary`, `source` (`"auditor"`).

## Tests: `tests/test_orchestrator.py` (scripted client from `tests/helpers.py`, no hardware)

`has_raw_bits`
- True for a full-frame hex string, for `0C01` padded to 8 hex digits, for `01100110`; false for normal geometric sentences, for "rows 0 to 7", for "repeat 1000 times", for the word "deadbeef"-free English text including words like "faded" and "added".

Plan
- Accepted from a `submit_plan` tool call (each route once).
- Accepted from JSON in the message text; accepted from JSON inside a code fence.
- Each of these is rejected, a retry is sent, and a good second reply is accepted: unknown route; `replay` route with an empty list; a `skill_name` not in the catalogue; more than `MAX_REPLAY_SKILLS` entries; `draw` route with no stages; more than `MAX_PLAN_STAGES` stages; empty `criteria` on `replay` and on `draw`; a stage containing hex; `criteria` containing a bit row; `params` that is not an object; text that is not JSON; a tool call with a different name.
- The retry request contains the first reply and a user message naming the problem.
- Two unusable replies raise `OrchestratorError`, and exactly two requests were made.
- A client exception raises `OrchestratorError` after exactly one request.
- `hold_ms` above the cap is clamped, missing `hold_ms` becomes 0, negative becomes 0.
- Unknown `reference_skills` are dropped; an invalid `skill_name` suggestion becomes `None` without failing the plan.
- `answer` route needs no criteria.
- The returned plan has every normalised key.

Audit
- `approve` accepted with only `verdict` and `summary`.
- `revise` without `expected`, `problem` or `fix` is rejected and retried.
- Feedback containing hex or a bit row is rejected and retried.
- Unknown verdict value is rejected; two unusable replies raise `OrchestratorError`.
- The audit request's user message contains the request text, the criteria and the evidence.

Requests
- Every request passes exactly one tool, the given model, exactly two messages on the first attempt (system, user), and no `tool_choice` key.

Prompts
- The plan prompt mentions all three routes and `submit_plan`; the audit prompt mentions commanded state, `sequence` and `submit_verdict`. Neither prompt satisfies `has_raw_bits`.

## `scripts/verify_stage2.py`

All stage 1 checks, plus C2 = `tests/test_orchestrator.py`, plus C7:
- `agent/orchestrator.py` does not contain the text `tool_choice`.
- `agent/orchestrator.py` imports neither `agent.loop` nor `server`.
- Neither prompt returned by `build_plan_prompt()` / `build_audit_prompt()` satisfies `has_raw_bits`.
- The words `draw_text` and `set_pixel` appear nowhere in `agent/`.

## Done when

- `.\.venv\Scripts\python.exe scripts\verify_stage2.py` ends with `STAGE 2 VERIFY: PASS`.
- `docs/PROGRESS.md` and `docs/DEVIATIONS.md` each have an `Agents stage 2` section. PROGRESS includes both prompts' full rendered text, so the owner can read what the Orchestrator is told.

Report as `CLAUDE.md` says.
