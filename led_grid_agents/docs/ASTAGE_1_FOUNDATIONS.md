# ASTAGE_1_FOUNDATIONS.md — stage 1: foundations and the verification framework

Read `docs/AGENTS_MASTER.md` first. This stage has no LLM calls and changes nothing in `agent/` or `server/`.

## Before you start

Run `.\.venv\Scripts\python.exe -m pytest`. Expect `158 passed`. If `.venv` is missing or the result differs, stop and tell the owner.

## Build, in this order

1. **`.gitignore`**: add the line `!*.md`.
2. **`config.py`**: add the constants of master 7.1, after `SKILL_TIME_CAP_S`. Change nothing else.
3. **`bridge/grid_store.py`**: add `since(seq)` (master 7.2). No other change to the file.
4. **`skills/pending.py`**: `PendingSkillStore` (master 7.3).
5. **`agent/evidence.py`**: `skill_outline`, `build_catalogue`, `frame_digest` (master 7.5).
6. **`tests/helpers.py`**: shared test helpers for all new test files:
   - `ScriptedClient(replies)`: returns prepared replies in order, the last one repeats; an `Exception` in the list is raised; records every request (a copy of the keyword arguments, with `messages` copied) in `.requests`.
   - `text_reply(content)`, `tool_reply(call_id, name, args)`, `shift_reply(call_id, data_hex)`, `plan_reply(**plan)` (a `submit_plan` tool call), `verdict_reply(**verdict)` (a `submit_verdict` tool call).
   - `make_bridge(tmp_path)` and `make_ctx(tmp_path, should_stop=None)` on `FakeTransport`, with the skill store in `tmp_path / "library"`.
   - `frame_hex(rows)`: full-frame hex for 8 row strings, built with `config.MSB_IS_LEFT`.
   Do not change the existing tests to use it.
7. **Verification framework**: `scripts/verify_common.py`, `scripts/verify_stage1.py`, `scripts/verify_all.py` (master section 10).

## Tests (new files)

**`tests/test_grid_store_since.py`**
- Returns only frames with a larger `seq`, oldest first.
- Returns more than 30 frames when more exist (write 45, ask for those after the 5th, get 40).
- A corrupt line and a line without an integer `seq` are skipped.
- A missing log file gives `[]`; `since` of the current `seq` gives `[]`.

**`tests/test_pending.py`**
- `save` writes no file; `get` and `list` show the held skill; the real directory is unchanged.
- An invalid definition raises `SkillError` and holds nothing; so does a bad name.
- Reported version: 1 for a new name, existing + 1 for a name already in the real store; `commit` writes that same version.
- Saving a held name again keeps one entry, the newer, last in `held()` order.
- `list()` for a held skill that replaces a real one shows it once, with the held description.
- `commit` writes every held skill in order, returns their results and empties; a second `commit` writes nothing.
- `discard` writes nothing, returns the names and empties.
- Through `agent.tools.call_tool` with `AgentContext(bridge, PendingSkillStore(real), ...)`: `save_skill` returns `success` with `name` and `version`; `list_skills` and `get_skill` show the held skill; `reuse_skill` plays it on the fake device.
- The held definition is a copy: mutating the dict passed to `save` afterwards does not change what is held.

**`tests/test_evidence.py`**
- Outline of a single-frame skill: `frames_sent 1`, `distinct_frames 1`, `duration_ms 0`.
- Outline of `repeat 6 x [frame A, wait 250, frame B, wait 250]`: `frames_sent 12`, `distinct_frames 2`, `duration_ms 3000`, and the structure text shows `repeat 6`.
- Nested repeats multiply; a count of 1000 nested three deep is computed without unrolling (the test must finish instantly).
- A `$param` count uses its default for the numbers and appears as `$name` in the structure text; `params` is included.
- No outline value contains a run of 8 or more hex digits.
- `build_catalogue`: one entry per skill, sorted as `store.list()`, each with `name`, `description`, `version` and the outline keys; an unreadable file is skipped.
- `frame_digest` of an on/off sequence of 6 cycles: two pictures, 12 sequence entries, counts 6 and 6, `lit` correct, 11 gaps.
- Truncation: 100 frames with `max_frames 80` gives 80 sequence entries, `truncated True`, `total 100`, counts summing to 100.
- Gaps are rounded to 10 ms; an empty frame list gives `total 0` and empty collections.
- Two frames with the same rows but different `display` are different pictures.

## `scripts/verify_stage1.py`

Common checks C1 to C6 with C2 = the three test files above, plus C7:
- `.gitignore` contains a line `!*.md`.
- `config` exposes `AGENT_MODE`, `MAX_ROUNDS == 3`, `RUN_TIME_CAP_S`, `MAX_REPLAY_SKILLS == 8`, `MAX_REPLAY_HOLD_MS`, `MAX_PLAN_STAGES`, `MAX_EVIDENCE_FRAMES`.
- `GridStore` has a callable `since`; `skills.pending.PendingSkillStore` and the three `agent.evidence` functions import.
- `bridge/grid_store.py` differs from `../led_grid/bridge/grid_store.py` only by added lines (no original line removed or changed). `WARN` if not readable.
- `tests/helpers.py` exists and exports the names listed in step 6.

`scripts/verify_all.py` runs every `scripts/verify_stage*.py` that exists, in number order, stops at the first failure, and ends with `VERIFY ALL: PASS` or `VERIFY ALL: FAIL (stage N)`.

## Done when

- `.\.venv\Scripts\python.exe scripts\verify_stage1.py` ends with `STAGE 1 VERIFY: PASS`.
- `docs/PROGRESS.md` and `docs/DEVIATIONS.md` each have an `Agents stage 1` section.

Report as `CLAUDE.md` says.
