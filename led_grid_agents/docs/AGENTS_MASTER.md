# AGENTS_MASTER.md — two-agent build in `led_grid_agents/`

Master plan for every build session. Read this file fully, then the one stage file you were given. `docs/CODE_SURVEY.md` describes the existing code with line references; use it instead of re-reading everything.

Order of authority: the owner's instructions in chat, this file, the stage file, the existing code's conventions.

---

## 1. What is being built

The existing system has one LLM agent that draws on an 8x8 LED matrix through generic primitives and saves working results as JSON skills. It does not reliably check its own work. This build adds a second LLM agent and a fixed, code-enforced procedure around both.

| Piece | Kind | Does |
|---|---|---|
| **Orchestrator** | LLM, two single calls per round | PLAN: turns the request into a route, stages and acceptance criteria. AUDIT: judges the result and returns a verdict |
| **Drawer** | LLM, the existing `run_agent` loop, unchanged | Drives the hardware through the existing eight tools, proposes skills |
| **Controller** | Plain Python (`agent/pipeline.py`), not an LLM | Runs the fixed sequence, holds saves as pending, replays, enforces limits, commits or discards |

The Orchestrator never touches hardware and never chooses the next step. The Controller's sequence is always the same.

## 2. Framing rules (unchanged from the project)

1. This is a general primitive-and-skill architecture. The LED grid is the demonstration vehicle.
2. Primitives are generic (`shift_out`, `wait`). No display-named tools, fonts or glyph tables in code or prompts.
3. Skills are data, never code. Never write or edit skill JSON by hand.
4. The LED map is **commanded** state confirmed by the device, not a measurement. The audit judges commanded state. Never describe it as verifying the physical display.
5. Never claim more than was tested.

## 3. Hard rules for every session

1. Work only inside `led_grid_agents/`. Never modify `../led_grid/`, `../POC/`, `../led_grid_vision/`.
2. **Do not change** `agent/loop.py`, `agent/tools.py`, `agent/prompts.py`, `skills/store.py`, `skills/runner.py`, `bridge/bridge.py`, `bridge/transport.py`, `bridge/fake_device.py`, `bridge/grid_model.py`, `firmware/`. If you believe one must change, stop and ask.
3. Do not edit existing test files, except the single line named in stage 4. New tests go in new files.
4. Use `.\.venv\Scripts\python.exe`. Run the full suite before reporting a stage done. All previously passing tests must still pass.
5. Never open the real serial port, never start the server against the board, never call the real LLM. Fake device and scripted LLM only. The owner runs every hardware step from exact numbered steps you write.
6. Never claim a hardware check passed.
7. Record every departure from this plan in `docs/DEVIATIONS.md` under a heading `Agents stage N`, and finished work in `docs/PROGRESS.md` under the same heading.
8. The evaluation is tomorrow. Prefer the smallest change that works. No refactoring of existing code, no new dependencies.

## 4. The flow (fixed in code)

```
request
  PLAN    Orchestrator call 1 -> route: replay | draw | answer
  -- answer: run the Drawer with the original request; no audit; nothing saved; done
  ROUND r (1..MAX_ROUNDS)
    EXECUTE
      replay route (round 1 only): Controller runs the listed skills through the runner
      draw route: Drawer runs (run_agent) with a brief; save_skill calls are HELD, not written
                  if a skill is held: Controller clears the display and replays the last held skill
    AUDIT   code checks first; if they pass, Orchestrator call 2 -> approve | revise
      approve: commit all held skills, finish "completed"
      revise : discard held skills, route becomes draw, next round with feedback
  after MAX_ROUNDS without approval: finish "not_approved", nothing saved
```

## 5. Guarantees enforced by code, not by prompts

1. A skill reaches the library only through `PendingSkillStore.commit()`, and `commit()` is called only after an `approve` verdict in the same run.
2. At most `MAX_ROUNDS` (3) execute rounds per run.
3. The Stop flag and an overall time cap (`RUN_TIME_CAP_S`) apply to every model call boundary, every tool call, every wait and every replay.
4. Plans, criteria and feedback containing hex strings or bit rows are rejected in code (section 7.4).
5. If the audit call fails or returns nothing usable, nothing is saved.
6. `AGENT_MODE=single` runs the original single-agent path with no behaviour change.

## 6. New and changed files

| File | Status | Contents |
|---|---|---|
| `skills/pending.py` | new | `PendingSkillStore` |
| `agent/evidence.py` | new | skill catalogue, skill outline, frame digest |
| `agent/orchestrator.py` | new | plan and audit calls, tool schemas, prompts, parsing, validation |
| `agent/pipeline.py` | new | `run_pipeline` (the Controller) |
| `bridge/grid_store.py` | one added method | `GridStore.since(seq)` |
| `config.py` | added constants | section 7.1 |
| `server/runs.py`, `server/app.py` | stage 4 | mode switch |
| `.gitignore` | add a line | `!*.md` |
| `tests/helpers.py` | new | shared scripted LLM client and reply builders for the new tests |
| `scripts/verify_common.py`, `verify_stage1..4.py`, `verify_all.py` | new | automated verification, one command per stage (section 10) |
| `scripts/live_check.py` | new, stage 4 | automated hardware check, run by the owner only |

Import direction stays `server -> agent -> skills -> bridge`. `skills/pending.py` imports only from `skills/`. `agent/evidence.py` may import `skills` and `bridge`.

## 7. Interfaces (exact)

### 7.1 `config.py` additions

```python
AGENT_MODE = os.getenv("AGENT_MODE", "multi")   # "multi" | "single"
MAX_ROUNDS = 3
RUN_TIME_CAP_S = 300
MAX_REPLAY_SKILLS = 8
MAX_REPLAY_HOLD_MS = 5000
MAX_PLAN_STAGES = 8
MAX_EVIDENCE_FRAMES = 80
```

### 7.2 `GridStore.since(seq: int) -> list[dict]`

All frames in the log with `seq` greater than the argument, oldest first, no cap. Reads the whole log file under the store's lock, skips lines that are not JSON objects or lack an integer `seq`. Read-only.

### 7.3 `skills/pending.py`

```python
class PendingSkillStore:
    def __init__(self, real: SkillStore) -> None
    def list(self) -> list[dict]      # real.list(), with held skills added or replacing the same name; same dict shape
    def get(self, name: str) -> dict  # the held definition if one is held under that name, else real.get(name)
    def save(self, name: str, definition: dict) -> dict
        # checks the name and validates exactly as SkillStore.save does (raise SkillError on failure),
        # holds a deep copy in memory, writes NOTHING to disk,
        # returns {"name": name, "version": v} where v is the version commit would produce
    def held(self) -> list[tuple[str, dict]]   # in save order; saving a held name again replaces it and moves it last
    def last(self) -> tuple[str, dict] | None
    def commit(self) -> list[dict]    # real.save(name, definition) for each held skill in order; returns their results; then empties
    def discard(self) -> list[str]    # empties; returns the names dropped
```

It is passed as `AgentContext.skills`, so `list_skills`, `get_skill`, `save_skill` and `reuse_skill` work on it with no change to `agent/tools.py`. `reuse_skill` of a held skill must work (the Drawer may play what it just saved).

### 7.4 `agent/orchestrator.py`

```python
class OrchestratorError(Exception)

def make_plan(client, model: str, request: str, catalogue: list[dict]) -> dict
def audit(client, model: str, request: str, plan: dict, evidence: dict) -> dict
def has_raw_bits(text: str) -> bool   # True if text contains 8+ consecutive hex digits or 8 consecutive 0/1 characters
```

Each is **one** request: `client.chat.completions.create(model=model, messages=[system, user], tools=[THE_ONE_TOOL])`. Do not pass `tool_choice` (the proxy's support is unknown). The reply is read as: the first tool call with the expected name, its arguments parsed as a JSON object; otherwise the message text parsed as a JSON object (strip a surrounding code fence). If neither yields a valid result, send **one** retry with the problem appended as a user message. If that fails too, raise `OrchestratorError`.

**Plan tool `submit_plan`** arguments:

| Key | Type | Rule |
|---|---|---|
| `route` | `"replay"`, `"draw"`, `"answer"` | required |
| `criteria` | string | required and non-empty for `replay` and `draw`; what must be true of the result, in words |
| `replay` | list of `{skill_name, params?, hold_ms?}` | `replay` route: 1..`MAX_REPLAY_SKILLS` entries; every `skill_name` must exist in the catalogue; `params` an object; `hold_ms` clamped to 0..`MAX_REPLAY_HOLD_MS`, default 0 |
| `stages` | list of strings | `draw` route: 1..`MAX_PLAN_STAGES` non-empty strings, each one full picture described geometrically |
| `reference_skills` | list of names | optional; existing skills whose frames the Drawer should read; unknown names are dropped |
| `skill_name` | string | optional suggested name for a new skill, `^[a-z0-9_]{1,40}$` |
| `note` | string | optional, one sentence for the user |

Validation is in code. `criteria` and every stage are rejected if `has_raw_bits` is true. An invalid plan counts as an unusable reply (retry once, then `OrchestratorError`). Return the normalised dict.

**Verdict tool `submit_verdict`** arguments: `verdict` (`"approve"` or `"revise"`, required), `expected`, `problem`, `fix` (strings; all three required and non-empty for `revise`), `summary` (one sentence for the user). `expected`, `problem`, `fix` are rejected if `has_raw_bits` is true.

**Plan prompt** must tell the Orchestrator:
- It plans for a separate Drawer agent that controls an 8x8 grid of LEDs (rows 0..7 top to bottom, columns 0..7 left to right) through generic hardware tools. It cannot act on hardware itself.
- Choose `replay` only when existing skills fulfil the request **exactly**, including every count, duration and order; fixed repeat counts are shown in the catalogue; a count held in a parameter can be set through `params`. Several skills in order are allowed (for example a countdown from digit skills, with `hold_ms` between them).
- Otherwise choose `draw`: break the goal into stages, each a complete picture, described with lines, positions, symmetry and counts in row and column terms. For an animation, describe each distinct picture once and state the order, the number of repetitions and the timing in `criteria`.
- Choose `answer` for a question that needs no change on the display.
- Never write hex, bytes or rows of 0 and 1.
- Answer only by calling `submit_plan`.

The user message holds the request and the catalogue as compact JSON.

**Audit prompt** must tell the Orchestrator:
- It is the auditor. It sees device-confirmed commanded state, not a camera.
- Judge only against the request and the criteria. Check shape, position, symmetry, counts, order and timing. Count repetitions from the `sequence` list, not by estimate.
- When a skill was held, judge the `replayed` evidence (what the skill really produces); otherwise judge `drawn`.
- `approve` only if every criterion holds. Otherwise `revise` with `expected`, `problem` and `fix` in rows, columns and shapes, never hex or bit rows.
- Answer only by calling `submit_verdict`.

### 7.5 `agent/evidence.py`

```python
def skill_outline(definition: dict) -> dict
def build_catalogue(store) -> list[dict]
def frame_digest(frames: list[dict], max_frames: int = MAX_EVIDENCE_FRAMES) -> dict
```

`skill_outline` (computed arithmetically, never by unrolling; a `$param` count or duration uses its default from `params`):
`{"params": {...}, "frames_sent": int, "distinct_frames": int, "duration_ms": int, "structure": str}` where `structure` is a compact text such as `repeat 6 x [frame A, wait 250, frame B, wait 250]` (frames labelled by distinct `data_hex`; a `$param` shown as `$name`). No hex in the output.

`build_catalogue`: for every skill in `store.list()`: `name`, `description`, `version`, plus the outline keys. Unreadable skills are skipped.

`frame_digest`:
```json
{"total": 12, "truncated": false,
 "pictures": {"P1": {"rows": ["...8 strings..."], "lit": 20, "display": "on"}, "P2": {"rows": ["..."], "lit": 0, "display": "on"}},
 "sequence": ["P1","P2","P1","P2"],
 "gaps_ms": [300, 300, 300],
 "counts": {"P1": 6, "P2": 6}}
```
A picture's identity is its `rows` plus `display`. `sequence` and `gaps_ms` keep the first `max_frames` entries and set `truncated`; `total` and `counts` always cover every frame. `gaps_ms[i]` is the time between frame `i` and `i+1`, rounded to 10 ms.

### 7.6 `agent/pipeline.py`

```python
def run_pipeline(prompt: str, ctx: AgentContext, on_event: Callable[[dict], None],
                 client=None, model: str | None = None) -> dict
# returns {"status": "completed" | "stopped" | "error" | "max_turns" | "not_approved", "summary": str, "error": str | None}
```

Behaviour:

1. Build the client exactly as `run_agent` does when `client is None`; a failure returns `status "error"`. The same client is used for the Orchestrator calls and passed to `run_agent`.
2. `pending = PendingSkillStore(ctx.skills)`. The Drawer's context is `AgentContext(ctx.bridge, pending, stop_or_deadline)`.
3. PLAN with `build_catalogue(ctx.skills)`. On `OrchestratorError`, use a fallback plan: `route "draw"`, no stages, `criteria` = "The display shows what the request asks for.", flagged `fallback: True`.
4. `answer` route: `run_agent(prompt, drawer_ctx, ...)`, then `pending.discard()`, return the Drawer's result unchanged.
5. Each round:
   - Stop requested: return `stopped`. Deadline passed: return `status "error"`, `error "time_cap"`. Held skills are discarded in both cases.
   - Record `seq0`.
   - **replay** (round 1 only): for each entry run `run_skill(ctx.skills.get(name), ctx.bridge, params, stop_or_deadline)`, then `ctx.bridge.wait(hold_ms, stop_or_deadline)`. A result with `success False` or `timed_out True`, or a missing skill, is a code-detected problem.
   - **draw**: `run_agent(brief, drawer_ctx, wrapped_on_event, client=client, model=model)`. Drawer status `stopped` -> return `stopped`. `error` -> return it. `max_turns` -> code-detected problem. Then, if a skill is held: send `CLEAR_HEX` with `ctx.bridge.shift_out` on the config pins, record `seq1`, run the **last** held skill with `run_skill` (default params); failure or timeout is a code-detected problem.
   - Evidence: `{"final": {"display", "rows"}, "drawn": frame_digest(frames of the Drawer run) or None, "replayed": frame_digest(frames after seq1, or of the replay route) or None, "held_skills": [{"name", "description", ...outline}], "drawer_summary": str}`.
   - A code-detected problem gives a `revise` verdict with `source "code"` and no model call. Otherwise call `audit`. `OrchestratorError` -> discard, return `status "not_approved"`, `error "audit_failed"`.
   - `approve`: `pending.commit()`, emit one `skill_saved` per committed skill, return `completed` with the verdict's `summary` (fall back to the Drawer's).
   - `revise`: `pending.discard()`, route becomes `draw`, keep the verdict as feedback.
6. After `MAX_ROUNDS`: return `not_approved` with a summary that states the last problem.

**The Drawer's brief** (the `prompt` argument of `run_agent`; plain text, built in code) contains, in this order: the original request; a statement that a planner already checked the skill library, that no existing skill fulfils the request exactly, and that `list_skills` should not be called; the numbered stages, each to be sent as one full frame with its own `shift_out`; the acceptance criteria and the fact that an auditor will check them; the reference skills to read with `get_skill`; saving instructions (suggested name if any; put every repetition count in `params` with the requested value as default and reference it as `$name`; do **not** play the skill after saving because the system replays and audits it; it is kept only if approved); from round 2, the auditor's `expected`, `problem`, `fix` and the round number.

### 7.7 Events

Every event the pipeline emits carries `role` (`"orchestrator"` or `"drawer"`) and `round` (0 before the first round). `RunManager` adds `run_id` as today.

| Event | Fields | When |
|---|---|---|
| `plan_ready` | `route`, `criteria`, `stages`, `replay`, `fallback` | after PLAN |
| `round_started` | `route` | start of each round |
| `skill_pending` | `name`, `version` | replaces the Drawer's `skill_saved` for a held save |
| `audit_verdict` | `verdict`, `expected`, `problem`, `fix`, `summary`, `source` (`"auditor"` or `"code"`) | after AUDIT |
| `skill_saved` | `name`, `version` | only on commit |

Drawer events pass through with `role "drawer"` added; a Drawer `skill_saved` is turned into `skill_pending`. The Controller's own replays are emitted as ordinary `tool_call` / `tool_result` pairs with `tool "reuse_skill"`, `role "orchestrator"` and a synthetic `call_id`, so the current dashboard shows them.

So that the unchanged dashboard shows the new steps, the pipeline **also** emits an `agent_message` (role `orchestrator`) for: the plan (`[Orchestrator] Plan: ...`), each held skill (`[Orchestrator] Skill X held for audit.`), and each verdict (`[Orchestrator] Approved: ...` or `[Orchestrator] Revise: <problem> Fix: <fix>`).

## 8. Stages

One fresh Claude Code session per stage. A stage starts only when the previous stage's verify script prints `PASS`.

| Stage | File | Scope | Owner runs afterwards |
|---|---|---|---|
| 1 | `ASTAGE_1_FOUNDATIONS.md` | Config, `GridStore.since`, `PendingSkillStore`, evidence helpers, shared test helpers, verification framework | `scripts\verify_stage1.py` |
| 2 | `ASTAGE_2_ORCHESTRATOR.md` | Plan and audit calls (`agent/orchestrator.py`) | `scripts\verify_stage2.py` |
| 3 | `ASTAGE_3_PIPELINE.md` | The Controller (`agent/pipeline.py`) | `scripts\verify_stage3.py` |
| 4 | `ASTAGE_4_SERVER.md` | Server mode switch, dashboard check, `live_check.py`, runbook | `scripts\verify_stage4.py`, then `scripts\live_check.py` on the board |
| later | not planned here | Dashboard styling for roles and verdicts; vision feed (`observed_state`, `hardware_fault`) | |

## 9. Known limits (state them, do not hide them)

- The audit judges commanded state. A dead LED or a disconnected grid is invisible to it until the vision feed is connected.
- When several skills are held in one round, only the last is replayed for the audit; all are committed on approval. The audit does see every frame drawn in the round.
- The Drawer's system prompt still says to list and reuse skills first; the brief overrides this in the user message. Whether the model always follows the brief is unverified until the hardware check.
- A Stop does not interrupt a model request already in flight.

## 10. Automated verification (built by Claude Code, run by the owner)

All testing is automated. The owner never checks anything by reading code; the owner runs one command per stage and reads the last line.

```powershell
.\.venv\Scripts\python.exe scripts\verify_stage1.py     # likewise 2, 3, 4
.\.venv\Scripts\python.exe scripts\verify_all.py        # every verify_stageN that exists, in order
```

Rules for every verify script:

1. Standard library only, plus running `pytest` as a subprocess with `sys.executable`. Works from any current directory (it locates the project root from its own path).
2. Offline only: never opens a serial port, never contacts the LLM proxy, never starts a server on a network port.
3. Prints one row per check: `PASS`, `FAIL` or `WARN`, the check name, and a one-line detail. On `FAIL` it prints enough output to diagnose (for pytest, the failing test names and the last 40 lines).
4. Ends with exactly one line `STAGE N VERIFY: PASS` or `STAGE N VERIFY: FAIL (k checks failed)`, exit code 0 or 1. `WARN` does not fail.
5. Also writes the same output to `logs/verify_stageN.txt`.
6. `verify_stageN` includes every check of the earlier stages, so running the latest one is enough.

Checks in `scripts/verify_common.py`, used by every stage:

| ID | Check | Fails when |
|---|---|---|
| C1 | Full test suite | Any test fails or errors, or fewer than 158 tests pass |
| C2 | The stage's own test files | Any listed file is missing, has no tests, or fails |
| C3 | Protected files unchanged | Any file of section 3 rule 2 differs byte-for-byte from its original in `../led_grid/` (read-only). `WARN` if `../led_grid/` is not readable |
| C4 | Existing tests unchanged | Any test file that exists in `../led_grid/tests/` differs here (from stage 4, `tests/test_server.py` may differ only in lines containing `mode=`). `WARN` if not readable |
| C5 | Import direction | A file in `bridge/` imports `skills`, `agent` or `server`; a file in `skills/` imports `agent` or `server`; a file in `agent/` imports `server` |
| C6 | Skill library intact | `skills/library/` does not hold exactly 32 `.json` files, or any differs from `skills/library_backup/` |
| C7 | Stage-specific static checks | Listed in each stage file |

`scripts/live_check.py` (stage 4) is the automated hardware check. It talks to the already running server over HTTP, so only the owner runs it. Claude Code tests its evaluation logic with recorded events and never runs it against a board.
