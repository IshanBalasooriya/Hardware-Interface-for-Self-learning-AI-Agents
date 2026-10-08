# SURVEY_PROMPT.md — code survey of `led_grid_agents/`

You are in `led_grid_agents/`, a fresh copy of `led_grid/`. A two-agent version will be built here. Before any design is fixed, a planner who cannot see this code needs an exact, evidence-backed description of it. Your job in this session is to write that description. You build nothing.

## Rules

1. **Read-only**, with one exception: create `docs/CODE_SURVEY.md`. Change no other file. Do not touch `../led_grid/`, `../POC/` or `../led_grid_vision/`.
2. Do not open the real serial port, start the server against the board, or call the real LLM. Running the test suite (fake device, scripted LLM) is allowed and wanted.
3. Use `.\.venv\Scripts\python.exe` only. If `.venv` is missing, say so in the report and skip the test run; do not create one.
4. Report only what the code shows. Every claim carries a `path:line` reference. Where a section asks for something **verbatim**, paste the code in a fenced block, unedited.
5. If something asked for does not exist, write `[NOT FOUND]`. If you are inferring, write `[INFERRED]`. Never guess silently.
6. Do not propose designs or fixes, except in section 13 where it is asked for.

## Output

One file, `docs/CODE_SURVEY.md`, with exactly the numbered sections below. Length is not a concern; completeness and accuracy are.

---

### 1. Baseline

- Command used to run the tests, total count, pass/fail/skip counts, run time.
- Number of `.json` files in `skills/library/` and in `skills/library_backup/`.
- Contents of this folder's `.gitignore` (verbatim). Is `!*.md` present?
- Dependency file name and its contents (verbatim).

### 2. `config.py`

Whole file, verbatim.

### 3. `agent/loop.py`

- Whole file, verbatim.
- Then answer, each with a line reference:
  - Exact signature of `run_agent` and what every parameter is.
  - How is the LLM client created or passed in? Can a caller inject a different client (as the tests' scripted LLM must)?
  - Can a caller pass a **different system prompt** without editing this file? How?
  - Can a caller pass a **subset or different set of tools** (schemas and dispatch) without editing this file? How?
  - Where is the 20-turn limit defined, and can a caller override it?
  - Is there any limit on the length of the user prompt inside `run_agent`?
  - How are events emitted (callback, queue, listener)? Exact call shape.
  - How is the stop flag passed and checked?
  - Exact return value shape for each status.

### 4. `agent/tools.py`

- `TOOL_SCHEMAS`, verbatim.
- `AgentContext`, verbatim (all fields).
- `call_tool`, verbatim.
- The implementations of `save_skill`, `reuse_skill`, `list_skills`, `get_skill`, `read_recent_frames`, `read_shift_state`, verbatim.
- Then answer:
  - Exactly which function call writes a skill to disk when the agent calls `save_skill`? What does the tool return to the model?
  - What does `list_skills` return per skill (name only, description, params, steps, version)? Paste one real example of its output if a test shows one.
  - Where is the `read_recent_frames` cap of 30 enforced?
  - How is the optional `intent` argument handled and turned into an event?

### 5. `agent/prompts.py`

`build_system_prompt()` and the full prompt text, verbatim.

### 6. `skills/store.py` and `skills/runner.py`

- Both files, verbatim (if either is over 300 lines, paste all public functions and the validation code, and summarise the rest).
- The skill JSON schema as actually validated: required keys, optional keys, allowed step types, limits.
- How versioning works on save (overwrite, bump, keep old?).
- How `$param` substitution works. **Can a `repeat` step take its count from a `$param`?** Show the code that decides.
- Can `run_skill` run a definition dict that is **not** stored in the library?
- Exact return value of `run_skill`, and how the 30 s cap and `should_stop` behave.
- Paste three real skill files from `skills/library/`, verbatim: `symbol_heart.json`, `anim_heartbeat.json`, and any one that uses params (or state that none does).

### 7. `bridge/` (store and bridge)

- `GridStore` public methods with signatures; the exact format of one line of `logs/shift_frames.jsonl`; what `seq` is and when it increments.
- Is there a way to get **all frames after a given `seq`**, without the cap of 30? If not, what is the closest existing method?
- How listeners are registered and what they receive.
- `Bridge` public methods with signatures and return values.
- How a test builds a `Bridge` on the fake device (paste the shortest real example).

### 8. `server/runs.py` and `server/app.py`

- `RunManager`, verbatim.
- The concurrency model: thread, asyncio task or other? How does a run reach `run_agent`, and how do events travel from the agent to the WebSocket?
- Where exactly the 500-character limit is enforced (confirm it is only at the HTTP endpoint, or show where else).
- How Stop works end to end.
- `create_app` signature and what it lets a test inject.
- One real example payload for **each** WebSocket event (`status`, `shift_state`, `run_started`, `agent_message`, `tool_call`, `tool_result`, `skill_saved`, `run_finished`), from the code or tests.

### 9. Dashboard (`server/static/`)

- List of the files with one line each.
- `api.js`, verbatim.
- Which file renders the agent trace, and how it handles an event type it does not know (ignored, error, shown raw?).
- What would have to change for events to carry a `role` field and for two new events (`round_started`, `audit_verdict`) to appear in the trace. Files and functions only, no code.

### 10. Tests

- Test file list with test counts per file.
- How the scripted LLM client works: its class, where it lives, and the shortest real test that drives `run_agent` with it (verbatim).
- Shared fixtures (`conftest.py`), verbatim.

### 11. Docs

- Every entry of `docs/DEVIATIONS.md`, verbatim.
- `docs/PROGRESS.md`: the final status section only.

### 12. Assumptions to confirm

Answer each **TRUE / FALSE / PARTLY**, with evidence:

1. `run_agent` can be called with a custom system prompt and a custom tool set without editing `agent/loop.py`.
2. A `save_skill` call can be intercepted (held in memory instead of written) by changing only `agent/tools.py` and/or the `AgentContext`, with no change to `agent/loop.py`.
3. `run_skill` accepts a definition dict directly, so a held, unsaved skill can be replayed.
4. Code outside the agent can run a library skill through the runner with no LLM involved.
5. The 500-character prompt limit exists only at `POST /api/prompt`.
6. The frames written during one run can be identified exactly (for example by `seq` range), including when there are more than 30.
7. A `repeat` count can be a `$param`.
8. Skills store enough information (description, params, steps) for a reader to tell what a skill displays and how many times it repeats, without running it.
9. A second LLM call that is not a `run_agent` loop (one request, one tool-call reply) can be made with the same client object the agent uses.
10. Events can gain an extra field (`role`) without breaking the current dashboard.

### 13. Surprises

Anything in the code that a planner working from the stage specs would not expect: dead code, behaviour that differs from the docs, hidden coupling, hard-coded values, fragile tests, places where adding a second agent will be awkward. This is the one section where your judgement is wanted. Keep each item to two or three lines.

---

When the file is written, reply with only: the path of the file, the test result line, and the answers to section 12 as a ten-line list.
