# ASTAGE_3_PIPELINE.md — stage 3: the Controller

Read `docs/AGENTS_MASTER.md` first (sections 4, 5, 7.6 and 7.7 are this stage). Nothing in `server/` changes.

## Before you start

Run `.\.venv\Scripts\python.exe scripts\verify_stage2.py`. It must end with `STAGE 2 VERIFY: PASS`. If not, stop and report.

## Build: `agent/pipeline.py`

`run_pipeline(prompt, ctx, on_event, client=None, model=None)` exactly as master 7.6, with the events of master 7.7.

Points that are easy to get wrong:

1. **`agent/loop.py` is not edited.** The Drawer is `run_agent(brief, drawer_ctx, wrapped_on_event, client=client, model=model)`. The brief is the user message; there is no length limit below the HTTP endpoint.
2. **The Drawer's context** is `AgentContext(ctx.bridge, pending, stop_or_deadline)`. `stop_or_deadline()` is true when `ctx.should_stop()` is true or the overall deadline has passed. When a call returns because of it, decide the status afterwards: Stop requested gives `stopped`; otherwise `error` with `"time_cap"`.
3. **Event wrapping.** Every Drawer event gets `role "drawer"` and the current `round`. A Drawer `skill_saved` is not forwarded; emit `skill_pending` and the `[Orchestrator] Skill X held for audit.` message instead. A listener exception must never escape (same pattern as `agent/loop.py::_emit`, written locally).
4. **Frame ranges.** `seq0` is read before the round's execution; `seq1` is read after the clear and before the replay of the held skill. `drawn` = frames in `(seq0, clear]` for a draw round (the frames of the Drawer run, excluding the clear frame); `replayed` = frames after `seq1`. For the replay route, `drawn` is `None` and `replayed` = frames after `seq0`.
5. **Clearing** uses `ctx.bridge.shift_out(DATA_PIN, CLOCK_PIN, LATCH_PIN, GROUP_SIZE, CLEAR_HEX)`. If it fails, that is a code-detected problem.
6. **Code-detected problems** produce a `revise` verdict with `source "code"`, plain-language `expected`, `problem` and `fix`, and no model call. Cases: a replay-route skill missing, failing, or timing out; the Drawer ending with `max_turns`; the clear failing; the held skill's replay failing, timing out or returning `bad_params`.
7. **Nothing is saved** on `stopped`, `error`, `not_approved`, `revise`, or the `answer` route: always `pending.discard()` before returning in those cases.
8. **Controller replays are visible**: emit a `tool_call` and `tool_result` pair around each `run_skill` the Controller performs, with `tool "reuse_skill"`, `args {"skill_name": ..., "params": ...}`, `role "orchestrator"`, a synthetic `call_id` such as `orch_r1_2`, and `duration_ms`.
9. **Result summary.** `completed`: the verdict's `summary`, or the Drawer's if empty, followed by the names of skills saved. `not_approved`: one sentence stating the last problem and that nothing was saved.
10. The function never raises: any unexpected exception becomes `status "error"` with the cut-down message, after discarding held skills.

## Tests: `tests/test_pipeline.py` (fake device, scripted client, `tmp_path` library)

Seed the temporary library through `SkillStore.save` with small skills built in the test (a single-frame skill, three digit-like single-frame skills, and a `repeat 5` blink). Never read `skills/library/`.

Routes
- **Replay, approved**: one skill; no Drawer request is made (the scripted client received exactly 2 requests: plan and audit); the fake device received the skill's frame; status `completed`.
- **Replay list**: three skills with `hold_ms`; frames arrive in order; three `reuse_skill` event pairs with role `orchestrator`.
- **Replay with params**: `params` reach `run_skill` (a `$count` skill replayed with 4 gives 4 cycles on the device).
- **Answer**: the Drawer receives the original prompt unchanged; no audit request; a `save_skill` during it leaves the library unchanged; the Drawer's result is returned as is.

Draw and save
- **Approved, committed**: the Drawer draws and calls `save_skill`; the file does not exist when the audit request is made (assert inside the scripted client by checking the directory when the audit request arrives); it exists after `approve`; `skill_pending` precedes `audit_verdict`, and `skill_saved` follows it.
- **Held skill is replayed**: after the Drawer run the device receives `CLEAR_HEX` and then the skill's frames; the audit request's evidence has a `replayed` digest matching the skill and a `drawn` digest matching the Drawer's frames.
- **No skill held**: a draw round without `save_skill` sends no clear, has `replayed` null, and can be approved; nothing is saved.
- **Two skills held**: the last is the one replayed; both are committed on approval.

Revision
- **Revise then approve**: the round-1 skill never reaches disk; the round-2 brief contains the `expected`, `problem` and `fix` text and the round number.
- **The blink case**: the plan's criteria ask for 6 repetitions; in round 1 the Drawer calls `reuse_skill` on the `repeat 5` skill; the audit request's digest shows 5 lit frames; scripted `revise`; round 2 saves a 6-repetition skill; approved; the committed skill is the round-2 one.
- **Never approved**: three rounds, status `not_approved`, library byte-identical to before, exactly `MAX_ROUNDS` Drawer runs, exactly `MAX_ROUNDS` audits.
- **Replay route revised**: the next round is a draw round and its brief includes the feedback.

Code-detected problems (no audit model call in that round)
- Replay route with bad params; replay route naming a skill deleted after planning.
- Drawer ends with `max_turns` (use a client that always returns a tool call, and a small `max_turns` if the function allows it, otherwise 20 scripted turns).
- Held skill whose replay returns `bad_params` (a `$param` with a wrong-typed default).

Failures
- **Plan fails twice**: the fallback plan is used (`fallback True` in `plan_ready`), the run continues as a draw round.
- **Audit fails twice**: status `not_approved`, `error "audit_failed"`, nothing saved.
- **Drawer model error**: status `error`, nothing saved, no audit.
- **Client construction is not attempted** when a client is passed.

Stop and time
- Stop set before the call: `stopped`, no request made.
- Stop set during the Drawer round (set it from the scripted client on its Nth request): `stopped`, nothing saved.
- Stop set during a Controller replay with a long wait: `stopped` within a second, nothing saved.
- Tiny time cap (monkeypatch `RUN_TIME_CAP_S` in the module): `error` with `"time_cap"`, nothing saved.

Events and brief
- Every emitted event has `role` and `round`; rounds count 1, 2, 3; `plan_ready` has round 0.
- No event of type `skill_saved` with role `drawer` is ever emitted.
- The plan, each held skill and each verdict also appear as `agent_message` events with the `[Orchestrator]` prefix.
- A listener that raises does not break the run.
- The brief contains: the request; each stage numbered; the criteria; each reference skill name; the instruction not to call `list_skills`; the `params` instruction; the instruction not to play the skill after saving; the suggested skill name when the plan gave one.
- The brief for a fallback plan (no stages) is still well formed.

Result shape
- Every return value has exactly the keys `status`, `summary`, `error`.

## `scripts/verify_stage3.py`

All stage 1 and 2 checks, plus C2 = `tests/test_pipeline.py`, plus C7:
- `agent/pipeline.py` does not import `server`.
- `agent/pipeline.py` contains no call to `SkillStore.save` or `.skills.save(` other than through `PendingSkillStore.commit` (static text check: the only occurrence of `.save(` in the file, if any, is inside a comment; `commit(` appears).
- **Offline scenario run**: the script itself runs three scripted pipeline runs on the fake device in a temporary directory (replay approved; draw + save approved; never approved) and checks the status and the resulting library contents of each. This is independent of pytest, and prints one line per scenario with the event types in order, so the owner can see the flow.

## Done when

- `.\.venv\Scripts\python.exe scripts\verify_stage3.py` ends with `STAGE 3 VERIFY: PASS`.
- `docs/PROGRESS.md` and `docs/DEVIATIONS.md` each have an `Agents stage 3` section. PROGRESS includes one complete example brief as the Drawer receives it.

Report as `CLAUDE.md` says.
