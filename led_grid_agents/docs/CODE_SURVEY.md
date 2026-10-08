# CODE_SURVEY.md — survey of `led_grid_agents/`

Survey date: 2026-10-08. Read-only survey per `SURVEY_PROMPT.md`. All `path:line` references are relative to `led_grid_agents/`. Verbatim blocks are copied from the files unedited; each is headed by its `path:first-last` line range.

Markers: `[NOT FOUND]` = asked for but does not exist in the code. `[INFERRED]` = deduction, not directly shown by a line.

---

## 1. Baseline

### Test run

- **`.venv` is missing.** `led_grid_agents/.venv/` does not exist (checked with `ls led_grid_agents/.venv` → "No such file or directory"). Virtual environments exist only in the sibling folders `../led_grid/.venv`, `../led_grid_vision/.venv` and `../POC/.venv`, which this survey may not use. Per `SURVEY_PROMPT.md` rule 3, and confirmed by the user, **the test suite was not run** and no venv was created.
- Command that would have been used: `.\.venv\Scripts\python.exe -m pytest` (config: `pytest.ini:1-3`, `pythonpath = .`, `testpaths = tests`).
- Pass/fail/skip counts and run time: **not available** (not run).
- Static count of collected tests (from `def test_` and the size of each `@pytest.mark.parametrize` list; see section 10): **158**. This matches the last recorded run in `docs/PROGRESS.md:320`: "`.venv/Scripts/python.exe -m pytest` with Python 3.11.9: 158 passed." That line is a record from the original `led_grid/` copy, not a run made in this session.

### Skill library

- `skills/library/`: **32** `.json` files (plus a `.gitkeep`).
- `skills/library_backup/`: **32** `.json` files.
- `diff -rq skills/library skills/library_backup` reports only `Only in skills/library: .gitkeep`, so the 32 JSON files are byte-identical in both folders.

### `.gitignore` (verbatim)

`.gitignore:1-6`

```text
.env
logs/
.pio/
__pycache__/
.pytest_cache/
.venv/
```

`!*.md` is **not present** [NOT FOUND]. (There is also no rule that would ignore `.md` files, so none is needed for markdown to be tracked.)

Note: `git status` in the parent repository shows `?? led_grid_agents/`, i.e. the whole folder is currently untracked.

### Dependency file: `requirements.txt` (verbatim)

`requirements.txt:1-6`

```text
pyserial
python-dotenv
openai
fastapi
uvicorn[standard]
pytest
```

No versions are pinned (`requirements.txt:1-6`).

---

## 2. `config.py`

`config.py:1-39`

```python
"""All constants for the LED grid system (master section 6.1)."""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

DATA_PIN = 25
CLOCK_PIN = 26
LATCH_PIN = 27
GROUP_SIZE = 2
MSB_IS_LEFT = True  # calibrated in stage 3, see docs/WIRING.md

BAUD = 115200
SERIAL_TIMEOUT_S = 2.0
SERIAL_PORT = os.getenv("SERIAL_PORT")

DEFAULT_INTENSITY = 2
MAX_SHIFT_BYTES = 64
MAX_WAIT_MS = 10000
MAX_HISTORY = 30
MAX_TURNS = 20
SKILL_TIME_CAP_S = 30

LOG_DIR = BASE_DIR / "logs"
STATE_FILE = LOG_DIR / "shift_state.json"
FRAMES_LOG = LOG_DIR / "shift_frames.jsonl"
EVENTS_LOG = LOG_DIR / "sample_events.jsonl"
SKILLS_DIR = BASE_DIR / "skills" / "library"

OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "http://127.0.0.1:18080/v1")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "dummy")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-5.5")

WAKE_HEX = "0F0009000B070A020C01"
CLEAR_HEX = "01000200030004000500060007000800"
```

---

## 3. `agent/loop.py`

`agent/loop.py:1-99`

```python
"""Conversation loop: model turns, tool calls through the registry, events for every step."""

import json
import logging
import time
from typing import Callable

from openai import OpenAI

from agent.prompts import build_system_prompt
from agent.tools import TOOL_SCHEMAS, AgentContext, call_tool, split_intent
from config import LLM_MODEL, MAX_TURNS, OPENAI_API_KEY, OPENAI_BASE_URL

MAX_ERROR_LEN = 200

logger = logging.getLogger(__name__)


def _emit(on_event: Callable[[dict], None], event: dict) -> None:
    try:
        on_event(event)
    except Exception:
        logger.exception("on_event failed")


def _parse_args(raw: str | None) -> dict | None:
    try:
        args = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return None
    return args if isinstance(args, dict) else None


def run_agent(prompt: str, ctx: AgentContext, on_event: Callable[[dict], None],
              client=None, model: str | None = None, max_turns: int = MAX_TURNS) -> dict:
    """Returns {"status": "completed" | "stopped" | "error" | "max_turns", "summary": str, "error": str | None}"""
    messages: list[dict] = [{"role": "system", "content": build_system_prompt()},
                            {"role": "user", "content": prompt}]
    summary = ""

    def stopped() -> dict:
        return {"status": "stopped", "summary": summary, "error": None}

    try:
        client = client if client is not None else OpenAI(base_url=OPENAI_BASE_URL, api_key=OPENAI_API_KEY)
    except Exception as e:
        return {"status": "error", "summary": "", "error": f"{type(e).__name__}: {e}"[:MAX_ERROR_LEN]}
    model = model or LLM_MODEL

    for turn in range(1, max_turns + 1):
        if ctx.should_stop():
            return stopped()
        try:
            reply = client.chat.completions.create(model=model, messages=messages, tools=TOOL_SCHEMAS)
            message = reply.choices[0].message
        except Exception as e:
            return {"status": "error", "summary": summary, "error": f"{type(e).__name__}: {e}"[:MAX_ERROR_LEN]}

        text = message.content or ""
        tool_calls = message.tool_calls or []
        logger.info("reply %d: text=%s tool_calls=%d", turn, "yes" if text else "no", len(tool_calls))
        if text:
            summary = text
            _emit(on_event, {"type": "agent_message", "text": text})

        assistant: dict = {"role": "assistant", "content": message.content}
        if tool_calls:
            assistant["tool_calls"] = [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in tool_calls]
        messages.append(assistant)
        if not tool_calls:
            return {"status": "completed", "summary": text, "error": None}

        for tc in tool_calls:
            if ctx.should_stop():
                return stopped()
            name = tc.function.name
            args = _parse_args(tc.function.arguments)
            if args is not None:
                intent, args = split_intent(name, args)
                if intent:
                    _emit(on_event, {"type": "agent_message", "text": intent})
            _emit(on_event, {"type": "tool_call", "call_id": tc.id, "tool": name,
                             "args": args if args is not None else tc.function.arguments})
            started = time.monotonic()
            if args is None:
                result = {"success": False, "error": "bad_args: arguments are not a valid JSON object"}
            else:
                result = call_tool(name, args, ctx)
            duration_ms = round((time.monotonic() - started) * 1000)
            _emit(on_event, {"type": "tool_result", "call_id": tc.id, "tool": name,
                             "result": result, "duration_ms": duration_ms})
            if name == "save_skill" and result.get("success"):
                _emit(on_event, {"type": "skill_saved", "name": result["name"], "version": result["version"]})
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result)})

    return {"status": "max_turns", "summary": summary, "error": None}
```

### Answers

- **Signature of `run_agent`** (`agent/loop.py:34-35`):
  `run_agent(prompt: str, ctx: AgentContext, on_event: Callable[[dict], None], client=None, model: str | None = None, max_turns: int = MAX_TURNS) -> dict`
  - `prompt`: the user's text, sent as the single `user` message (`agent/loop.py:38`).
  - `ctx`: `AgentContext` (bridge, skill store, stop callable; `agent/tools.py:18-22`). Passed through to every `call_tool` (`agent/loop.py:91`) and its `should_stop` is checked by the loop (`agent/loop.py:51,77`).
  - `on_event`: callback receiving one event dict at a time (`agent/loop.py:19-23`).
  - `client`: an OpenAI-compatible client object; `None` means build one from config (`agent/loop.py:45`).
  - `model`: model name; `None` → `config.LLM_MODEL` (`agent/loop.py:48`, `config.py:36`).
  - `max_turns`: maximum number of model requests; default `config.MAX_TURNS` = 20 (`agent/loop.py:35,50`, `config.py:25`).
- **LLM client creation / injection**: `client = client if client is not None else OpenAI(base_url=OPENAI_BASE_URL, api_key=OPENAI_API_KEY)` (`agent/loop.py:45`), inside a `try` so a construction error returns `status: "error"` (`agent/loop.py:44-47`). A caller **can** inject any object with `.chat.completions.create(model=..., messages=..., tools=...)` returning an object with `.choices[0].message.content` / `.tool_calls` (`agent/loop.py:54-55,59-60`). The tests do exactly this with `ScriptedClient` (`tests/test_loop.py:32-45`). The client used is not returned or exposed to the caller.
- **Custom system prompt without editing the file**: **No.** The system message is hard-wired: `{"role": "system", "content": build_system_prompt()}` (`agent/loop.py:37`), with `build_system_prompt` imported at module level (`agent/loop.py:10`). There is no parameter for it. The only way without editing would be monkeypatching `agent.loop.build_system_prompt` [INFERRED].
- **Subset / different tools without editing the file**: **No.** The request always passes `tools=TOOL_SCHEMAS` (`agent/loop.py:54`) and dispatch always goes through `call_tool` (`agent/loop.py:91`), both imported at module level (`agent/loop.py:11`). No parameter exists. A different `AgentContext` changes what the existing tools act on, not which tools exist. Again only monkeypatching `agent.loop.TOOL_SCHEMAS` / `agent.loop.call_tool` would work [INFERRED].
- **20-turn limit**: defined as `MAX_TURNS = 20` (`config.py:25`), used as the default of `max_turns` (`agent/loop.py:35`), loop `for turn in range(1, max_turns + 1)` (`agent/loop.py:50`). A caller **can** override it via `max_turns=` (test: `tests/test_loop.py:139`). A "turn" is one model request; all tool calls of one reply run in the same turn (`agent/loop.py:76-97`). `RunManager` does not pass it (`server/runs.py:51-52`), so the server always uses 20.
- **Limit on the user prompt length inside `run_agent`**: **none** [NOT FOUND]. `prompt` is placed in the messages unchanged (`agent/loop.py:38`).
- **Event emission**: plain synchronous callback. `_emit(on_event, event)` calls `on_event(event)` and logs and swallows any exception (`agent/loop.py:19-23`). Call shapes:
  - `{"type": "agent_message", "text": text}` (`agent/loop.py:64`) for model text, and `{"type": "agent_message", "text": intent}` (`agent/loop.py:84`) for a tool call's `intent`.
  - `{"type": "tool_call", "call_id": tc.id, "tool": name, "args": args if args is not None else tc.function.arguments}` (`agent/loop.py:85-86`).
  - `{"type": "tool_result", "call_id": tc.id, "tool": name, "result": result, "duration_ms": duration_ms}` (`agent/loop.py:93-94`).
  - `{"type": "skill_saved", "name": result["name"], "version": result["version"]}` when `name == "save_skill" and result.get("success")` (`agent/loop.py:95-96`).
  - The loop emits no `run_started`/`run_finished`/`status`; those come from `server/runs.py`.
- **Stop flag**: passed inside `ctx` as `ctx.should_stop: Callable[[], bool]` (`agent/tools.py:22`). Checked before every model request (`agent/loop.py:51-52`) and before every tool call (`agent/loop.py:77-78`); also passed down into `Bridge.wait` (`agent/tools.py:126`) and `run_skill` (`agent/tools.py:138`). An in-flight `client.chat.completions.create` call is not interrupted.
- **Return values** (`agent/loop.py:36`):
  - completed: `{"status": "completed", "summary": text, "error": None}` — reply without tool calls (`agent/loop.py:73-74`).
  - stopped: `{"status": "stopped", "summary": summary, "error": None}` (`agent/loop.py:41-42`); `summary` = last text the model sent.
  - error (client construction): `{"status": "error", "summary": "", "error": "<Type>: <msg>"[:200]}` (`agent/loop.py:46-47`).
  - error (model call): `{"status": "error", "summary": summary, "error": "<Type>: <msg>"[:200]}` (`agent/loop.py:56-57`).
  - max_turns: `{"status": "max_turns", "summary": summary, "error": None}` (`agent/loop.py:99`).

---

## 4. `agent/tools.py`

### `TOOL_SCHEMAS` (verbatim, with the helper and constants it uses)

`agent/tools.py:13-16`

```python
MAX_ERROR_LEN = 200
SKILL_NAME_PATTERN = "^[a-z0-9_]{1,40}$"
INTENT_TOOLS = ("shift_out", "save_skill", "reuse_skill")

```

`agent/tools.py:25-72`

```python
def _schema(name: str, description: str, properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "function", "function": {
        "name": name,
        "description": description,
        "parameters": {"type": "object", "properties": properties,
                       "required": required if required is not None else list(properties),
                       "additionalProperties": False},
    }}


_PIN = {"type": "integer", "minimum": 0, "maximum": 39}
_INTENT = {"type": "string", "description": "One short sentence: what this call is for."}

TOOL_SCHEMAS: list[dict] = [
    _schema("shift_out",
            "Shift bytes out MSB first on a data/clock pin pair; after every group_size bytes the latch pin "
            f"is pulsed so that group takes effect. At most {MAX_SHIFT_BYTES} bytes per call, "
            "a multiple of group_size.",
            {"data_pin": _PIN, "clock_pin": _PIN, "latch_pin": _PIN,
             "group_size": {"type": "integer", "minimum": 1, "maximum": MAX_SHIFT_BYTES},
             "data_hex": {"type": "string", "pattern": f"^([0-9A-Fa-f]{{2}}){{1,{MAX_SHIFT_BYTES}}}$",
                          "description": "Bytes to send as hex, e.g. '0C01'."},
             "intent": _INTENT},
            required=["data_pin", "clock_pin", "latch_pin", "group_size", "data_hex"]),
    _schema("wait", "Pause for a number of milliseconds without sending anything.",
            {"duration_ms": {"type": "integer", "minimum": 0, "maximum": MAX_WAIT_MS}}),
    _schema("read_shift_state",
            "Return the current state of the bound shift device, as confirmed by the device's replies.", {}),
    _schema("read_recent_frames", "Return the most recent confirmed states of the bound shift device, oldest first.",
            {"count": {"type": "integer", "minimum": 1, "maximum": MAX_HISTORY}}),
    _schema("list_skills", "List saved skills with name, description, step count and version.", {}),
    _schema("get_skill", "Return the full JSON definition of a saved skill.",
            {"name": {"type": "string", "pattern": SKILL_NAME_PATTERN}}),
    _schema("save_skill",
            "Save a skill: a JSON action sequence of shift_out, wait and repeat steps. "
            f"Saving an existing name creates a new version. {SKILL_KEYS_RULE} "
            f"A complete, valid definition: {json.dumps(SKILL_EXAMPLE)}",
            {"name": {"type": "string", "pattern": SKILL_NAME_PATTERN},
             "definition": {"type": "object", "description": "Skill JSON with type 'action_sequence', "
                            "optional description and params, and a non-empty actions list."},
             "intent": _INTENT},
            required=["name", "definition"]),
    _schema("reuse_skill", "Replay a saved skill on the hardware, optionally overriding its params.",
            {"skill_name": {"type": "string", "pattern": SKILL_NAME_PATTERN},
             "params": {"type": "object", "description": "Values for the skill's $name placeholders."},
             "intent": _INTENT},
            required=["skill_name"]),
]
```

### `AgentContext` (verbatim)

`agent/tools.py:18-22`

```python
@dataclass
class AgentContext:
    bridge: Bridge
    skills: SkillStore
    should_stop: Callable[[], bool]
```

### `call_tool` (verbatim)

`agent/tools.py:141-152`

```python
def call_tool(name: str, args: dict, ctx: AgentContext) -> dict:
    if name not in _ARGS:
        return {"success": False, "error": "unknown_tool"}
    problem = _check_args(name, args)
    if problem is not None:
        return {"success": False, "error": f"bad_args: {problem}"}
    try:
        return _run(name, args, ctx)
    except SkillError as e:
        return {"success": False, "error": str(e)[:MAX_ERROR_LEN]}
    except Exception as e:
        return {"success": False, "error": f"{type(e).__name__}: {e}"[:MAX_ERROR_LEN]}
```

It relies on `_ARGS`, `_check_args` and `_run`:

`agent/tools.py:74-119`

```python
# tool -> (required {arg: type}, optional {arg: type})
_ARGS: dict[str, tuple[dict, dict]] = {
    "shift_out": ({"data_pin": int, "clock_pin": int, "latch_pin": int, "group_size": int, "data_hex": str}, {}),
    "wait": ({"duration_ms": int}, {}),
    "read_shift_state": ({}, {}),
    "read_recent_frames": ({"count": int}, {}),
    "list_skills": ({}, {}),
    "get_skill": ({"name": str}, {}),
    "save_skill": ({"name": str, "definition": dict}, {}),
    "reuse_skill": ({"skill_name": str}, {"params": dict}),
}


def split_intent(name: str, args: dict) -> tuple[str | None, dict]:
    """Remove the optional 'intent' note before dispatch. Returns (intent or None, remaining args)."""
    if name not in INTENT_TOOLS or "intent" not in args:
        return None, args
    intent = args["intent"]
    rest = {k: v for k, v in args.items() if k != "intent"}
    return (intent.strip() or None) if isinstance(intent, str) else None, rest


def _has_type(value: object, expected: type) -> bool:
    if expected is int:
        return isinstance(value, int) and not isinstance(value, bool)
    return isinstance(value, expected)


def _check_args(name: str, args: object) -> str | None:
    """Return a bad_args detail, or None if the arguments are acceptable."""
    if not isinstance(args, dict):
        return "arguments must be an object"
    required, optional = _ARGS[name]
    allowed = {**required, **optional}
    for key in args:
        if key not in allowed:
            return f"unexpected argument {key!r}"
    for key, expected in allowed.items():
        if key not in args:
            if key in required:
                return f"missing argument {key!r}"
        elif not _has_type(args[key], expected):
            return f"{key} must be {expected.__name__}"
    if name == "read_recent_frames" and not 1 <= args["count"] <= MAX_HISTORY:
        return f"count must be 1..{MAX_HISTORY}"
    return None
```

### Tool implementations

There are no functions named `save_skill`, `reuse_skill`, `list_skills`, `get_skill`, `read_recent_frames` or `read_shift_state` [NOT FOUND]. All tools are branches of `_run`, which delegates to `SkillStore`, `GridStore`, `Bridge` and `run_skill`:

`agent/tools.py:122-138`

```python
def _run(name: str, args: dict, ctx: AgentContext) -> dict:
    if name == "shift_out":
        return ctx.bridge.shift_out(**args)
    if name == "wait":
        return ctx.bridge.wait(args["duration_ms"], ctx.should_stop)
    if name == "read_shift_state":
        return {"success": True, "state": ctx.bridge.store.current()}
    if name == "read_recent_frames":
        return {"success": True, "frames": ctx.bridge.store.recent(args["count"])}
    if name == "list_skills":
        return {"success": True, "skills": ctx.skills.list()}
    if name == "get_skill":
        return {"success": True, "definition": ctx.skills.get(args["name"])}
    if name == "save_skill":
        return {"success": True, **ctx.skills.save(args["name"], args["definition"])}
    definition = ctx.skills.get(args["skill_name"])
    return run_skill(definition, ctx.bridge, args.get("params"), ctx.should_stop)
```

The delegated code:

- `save_skill` → `SkillStore.save` (`skills/store.py:146-162`, pasted in section 6).
- `get_skill` → `SkillStore.get` (`skills/store.py:134-144`, section 6).
- `list_skills` → `SkillStore.list` (`skills/store.py:116-132`, section 6).
- `reuse_skill` → `SkillStore.get` then `run_skill` (`skills/runner.py:31-81`, section 6).
- `read_shift_state` → `GridStore.current`:

`bridge/grid_store.py:112-114`

```python
    def current(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._map)
```

- `read_recent_frames` → `GridStore.recent`:

`bridge/grid_store.py:116-137`

```python
    def recent(self, count: int) -> list[dict]:
        count = max(1, min(count, MAX_HISTORY))
        with self._lock:
            if not self.log_path.exists():
                return []
            with self.log_path.open("rb") as f:
                size = f.seek(0, os.SEEK_END)
                offset = max(0, size - TAIL_BYTES)
                f.seek(offset)
                tail = f.read()
        lines = tail.decode("utf-8", errors="replace").splitlines()
        if offset > 0:
            lines = lines[1:]
        frames = []
        for line in lines:
            try:
                frame = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(frame, dict):
                frames.append(frame)
        return frames[-count:]
```

`split_intent` (used by the loop, not by `call_tool`):

`agent/tools.py:87-93`

```python
def split_intent(name: str, args: dict) -> tuple[str | None, dict]:
    """Remove the optional 'intent' note before dispatch. Returns (intent or None, remaining args)."""
    if name not in INTENT_TOOLS or "intent" not in args:
        return None, args
    intent = args["intent"]
    rest = {k: v for k, v in args.items() if k != "intent"}
    return (intent.strip() or None) if isinstance(intent, str) else None, rest
```

### Answers

- **What writes a skill to disk on `save_skill`**: `ctx.skills.save(args["name"], args["definition"])` (`agent/tools.py:136`) → `SkillStore.save`, which validates (`skills/store.py:148`), writes `<name>.json.tmp` with `tmp.write_text(...)` (`skills/store.py:160`) and renames it with `os.replace(tmp, path)` (`skills/store.py:161`). The tool returns `{"success": True, "name": name, "version": version}` (`agent/tools.py:136` + `skills/store.py:162`); on a validation error, `{"success": False, "error": "<SkillError message>"[:200]}` (`agent/tools.py:149-150`). The loop also sends this dict back to the model as the `tool` message content (`agent/loop.py:97`).
- **What `list_skills` returns per skill**: `name` (file stem), `description` (default `""`), `steps` (number of **top-level** actions only, `skills/store.py:102-104`), `version`, `type` (`skills/store.py:125-131`). It does **not** return `params` or the actions themselves. Wrapped as `{"success": True, "skills": [...]}` (`agent/tools.py:132`). Real examples:
  - Test: `tests/test_skills.py:72-73` asserts `store.list() == [{"name": "symbol_heart", "description": "Heart", "steps": 3, "version": 1, "type": "action_sequence"}]`.
  - Test: `tests/test_server.py:186-187` asserts `{"name": "symbol_heart", "description": "", "steps": 1, "version": 1, "type": "action_sequence"}`.
  - Real run log `logs/sample_events.jsonl:4` (first entries of the `list_skills` result): `{"name": "anim_heartbeat", "description": "Animation: heart blinks three times (300 ms on, 300 ms off).", "steps": 12, "version": 1, "type": "action_sequence"}, {"name": "anim_test_blink", "description": "Test animation: heart blinks 5 times (300 ms on, 300 ms off)", "steps": 1, "version": 1, "type": "action_sequence"}, ...`
- **Where the `read_recent_frames` cap of 30 is enforced**: three places. (1) The schema says `"maximum": MAX_HISTORY` (`agent/tools.py:53-54`), (2) `_check_args` rejects `count` outside `1..MAX_HISTORY` with `bad_args` (`agent/tools.py:117-118`), (3) `GridStore.recent` clamps `count = max(1, min(count, MAX_HISTORY))` (`bridge/grid_store.py:117`). `MAX_HISTORY = 30` (`config.py:24`). Independently, `recent` only reads the last 64 KiB of the log (`bridge/grid_store.py:15,121-128`).
- **`intent` handling**: allowed in the schema of `shift_out`, `save_skill`, `reuse_skill` only (`agent/tools.py:15,47,65,70`), never required. The loop parses the arguments, then calls `split_intent(name, args)` (`agent/loop.py:81-82`), which removes `intent` for those three tools and returns it stripped, or `None` if it is not a non-blank string (`agent/tools.py:87-93`). A non-empty intent is emitted as `{"type": "agent_message", "text": intent}` before the `tool_call` event (`agent/loop.py:83-84`); the `tool_call` event and `call_tool` get the args without it (`agent/loop.py:85-91`). For any other tool, `intent` stays in the args and `_check_args` returns `bad_args: unexpected argument 'intent'` (`agent/tools.py:108-110`; test `tests/test_loop.py:185-190`). The assistant message sent back to the model keeps the raw arguments including `intent` (`agent/loop.py:68-71`).

---

## 5. `agent/prompts.py`

`build_system_prompt()` and everything it uses (the whole file):

`agent/prompts.py:1-124`

```python
"""System prompt, generated from config so pins and bit order are never hand-typed."""

import json

from config import (CLEAR_HEX, CLOCK_PIN, DATA_PIN, DEFAULT_INTENSITY, GROUP_SIZE, LATCH_PIN, MAX_HISTORY,
                    MAX_SHIFT_BYTES, MAX_WAIT_MS, MSB_IS_LEFT, WAKE_HEX)

WORD_FRAME_WAIT_MS = 600


def _row_byte(row: str) -> int:
    """Byte for a picture row string ('1' = lit, character 0 = leftmost LED)."""
    return int(row if MSB_IS_LEFT else row[::-1], 2)


def _example_shift(data_hex: str) -> dict:
    return {"tool": "shift_out", "args": {"data_pin": DATA_PIN, "clock_pin": CLOCK_PIN, "latch_pin": LATCH_PIN,
                                         "group_size": GROUP_SIZE, "data_hex": data_hex}}


# A complete skill that passes skills.store.validate: the top-left LED blinks 3 times.
_TOP_LEFT_HEX = f"01{_row_byte('10000000'):02X}" + "".join(f"{reg:02X}00" for reg in range(2, 9))
SKILL_EXAMPLE: dict = {
    "type": "action_sequence",
    "description": "Top-left LED blinks 3 times",
    "actions": [{"tool": "repeat", "args": {"count": 3, "actions": [
        _example_shift(_TOP_LEFT_HEX),
        {"tool": "wait", "args": {"duration_ms": 300}},
        _example_shift(CLEAR_HEX),
        {"tool": "wait", "args": {"duration_ms": 300}},
    ]}}],
}
SKILL_KEYS_RULE = (
    "A skill definition has only these top-level keys: type (always 'action_sequence'), description, "
    "params (optional) and actions. Every action is exactly {\"tool\": ..., \"args\": {...}}. shift_out args "
    "are exactly data_pin, clock_pin, latch_pin, group_size, data_hex. wait args are exactly duration_ms "
    f"(0..{MAX_WAIT_MS}). repeat args are exactly count (1..1000) and actions (at most 3 repeats nested). "
    "No other keys are allowed anywhere; the version is set by the store."
)


def build_system_prompt() -> str:
    example_row = "11000000"
    example_byte = _row_byte(example_row)
    leftmost_bit = "bit 7 (the most significant bit)" if MSB_IS_LEFT else "bit 0 (the least significant bit)"
    frame_hex_len = 8 * GROUP_SIZE * 2
    frame_template = "".join(f"{reg:02X}[r{reg - 1}]" for reg in range(1, 9))
    pins = f"data_pin {DATA_PIN}, clock_pin {CLOCK_PIN}, latch_pin {LATCH_PIN}, group_size {GROUP_SIZE}"

    sections = [
        "## Role\n"
        "You control physical hardware only through the tools listed. You have no other way to act. "
        "Report plainly what you did.",

        "## Hardware\n"
        f"A MAX7219 driving an 8x8 LED matrix is connected on data pin {DATA_PIN}, clock pin {CLOCK_PIN}, "
        f"latch pin {LATCH_PIN}. Every message to the chip is {GROUP_SIZE} bytes (group_size {GROUP_SIZE}): "
        "register address, then value.\n"
        "| Register | Meaning | Value used |\n"
        "|---|---|---|\n"
        "| 01..08 | Row 1..8 data, one bit per LED | picture data |\n"
        "| 09 | Decode mode | 00 (plain LEDs) |\n"
        f"| 0A | Intensity, 00..0F | {DEFAULT_INTENSITY:02X} default |\n"
        "| 0B | Scan limit | 07 (all 8 rows) |\n"
        "| 0C | Shutdown: 01 = on, 00 = off | 01 |\n"
        "| 0F | Display test: 01 = all LEDs on | 00 |",

        "## Picture to bytes\n"
        "Rows are registers 01 (top) to 08 (bottom). In a picture row string, character 0 is the leftmost LED "
        f"and '1' means lit. The leftmost LED is {leftmost_bit} of the row byte. Example: the row "
        f"{example_row} (two leftmost LEDs lit) is the byte 0x{example_byte:02X}, so on the top row the "
        f"message is 01{example_byte:02X}. A full frame is 8 messages in one shift_out call: "
        f"{frame_hex_len} hex characters ({MAX_SHIFT_BYTES} bytes at most per call). The 8 messages are "
        f"registers 01 to 08 in that order, each exactly once: {frame_template} ([rN] = the byte of picture "
        "row N, row 0 = top). A repeated or missing register shifts the picture.",

        "## Procedure for drawing\n"
        "- First call list_skills. If a suitable skill exists, use reuse_skill.\n"
        "- Otherwise state in one or two sentences what you will draw, then send one full frame per "
        f"shift_out call ({pins}).\n"
        "- After every shift_out, compare all 8 rows of decoded_state.rows with your 8 intended rows. If "
        "exactly one row differs, resend only that row (one 2-byte message). If several rows differ, the "
        "register sequence is wrong: rebuild the full frame with registers 01 to 08 in order and resend it. "
        "Do not call save_skill until all 8 rows match.\n"
        f"- If decoded_state.display is not 'on', send the wake-up sequence {WAKE_HEX} first.\n"
        "- When the picture is correct and reusable, save_skill it with a short description.",

        "## Intent\n"
        "Every shift_out, save_skill and reuse_skill call must include the intent argument: one short "
        "sentence saying what the call is for, e.g. \"Draw the full smiley face frame.\" It is shown to "
        "the user and is never saved into skills.",

        "## Skill JSON\n"
        f"{SKILL_KEYS_RULE} A complete, valid example:\n{json.dumps(SKILL_EXAMPLE)}",

        "## Conventions\n"
        "Letters and digits are 5 columns wide and 7 rows tall, using columns 1 to 5 and rows 0 to 6 "
        "(0-based, row 0 = top, column 0 = left), so every glyph sits in the same place. Symbols and "
        "patterns may use all 8x8. Skill names match ^[a-z0-9_]{1,40}$ and use these prefixes: glyph_a, "
        "digit_7, symbol_heart, pattern_checker, word_hi, anim_heartbeat.",

        "## Words and numbers with several characters\n"
        f"Show one character per frame, with a wait of about {WORD_FRAME_WAIT_MS} ms between frames. Build "
        "the word skill by reading each glyph skill with get_skill and copying its frame into the new "
        "skill. Skills cannot call other skills.",

        "## Animations\n"
        "Build and check each frame with its own shift_out call first. Then save all frames with wait "
        "steps inside a repeat. Then play it with reuse_skill. Use repeat for repeated frames: put one "
        "cycle (its frames and waits) inside one repeat whose count is the number of repetitions. Never "
        "write the same frames out more than once.",

        "## History\n"
        "read_shift_state shows the current picture at any time. read_recent_frames shows what was "
        f"displayed recently (up to {MAX_HISTORY} frames).",

        "## Limits\n"
        f"Do not use pins other than {DATA_PIN}, {CLOCK_PIN} and {LATCH_PIN}. If a request cannot be shown "
        "legibly on 8x8, say so and offer the closest option.",

        "## Finish\n"
        "End with one short sentence saying what is now on the display and which skill was saved or reused.",
    ]
    return "\n\n".join(sections)
```

### Full prompt text

The rendered text is not stored anywhere in the repository [NOT FOUND]. With the current `config.py` values (`DATA_PIN=25`, `CLOCK_PIN=26`, `LATCH_PIN=27`, `GROUP_SIZE=2`, `MSB_IS_LEFT=True`, `DEFAULT_INTENSITY=2`, `MAX_SHIFT_BYTES=64`, `MAX_WAIT_MS=10000`, `MAX_HISTORY=30`, `WAKE_HEX`, `CLEAR_HEX`; `config.py:11-39`) the f-strings evaluate to the text below. This was evaluated by hand from `agent/prompts.py:42-124`, not by running the code [INFERRED]; the parts asserted by `tests/test_prompts.py:6-22` match it. Derived values: `_row_byte('11000000')` = 0xC0; `frame_hex_len` = 8·2·2 = 32; `_TOP_LEFT_HEX` = `0180` + `0200…0800`.

```text
## Role
You control physical hardware only through the tools listed. You have no other way to act. Report plainly what you did.

## Hardware
A MAX7219 driving an 8x8 LED matrix is connected on data pin 25, clock pin 26, latch pin 27. Every message to the chip is 2 bytes (group_size 2): register address, then value.
| Register | Meaning | Value used |
|---|---|---|
| 01..08 | Row 1..8 data, one bit per LED | picture data |
| 09 | Decode mode | 00 (plain LEDs) |
| 0A | Intensity, 00..0F | 02 default |
| 0B | Scan limit | 07 (all 8 rows) |
| 0C | Shutdown: 01 = on, 00 = off | 01 |
| 0F | Display test: 01 = all LEDs on | 00 |

## Picture to bytes
Rows are registers 01 (top) to 08 (bottom). In a picture row string, character 0 is the leftmost LED and '1' means lit. The leftmost LED is bit 7 (the most significant bit) of the row byte. Example: the row 11000000 (two leftmost LEDs lit) is the byte 0xC0, so on the top row the message is 01C0. A full frame is 8 messages in one shift_out call: 32 hex characters (64 bytes at most per call). The 8 messages are registers 01 to 08 in that order, each exactly once: 01[r0]02[r1]03[r2]04[r3]05[r4]06[r5]07[r6]08[r7] ([rN] = the byte of picture row N, row 0 = top). A repeated or missing register shifts the picture.

## Procedure for drawing
- First call list_skills. If a suitable skill exists, use reuse_skill.
- Otherwise state in one or two sentences what you will draw, then send one full frame per shift_out call (data_pin 25, clock_pin 26, latch_pin 27, group_size 2).
- After every shift_out, compare all 8 rows of decoded_state.rows with your 8 intended rows. If exactly one row differs, resend only that row (one 2-byte message). If several rows differ, the register sequence is wrong: rebuild the full frame with registers 01 to 08 in order and resend it. Do not call save_skill until all 8 rows match.
- If decoded_state.display is not 'on', send the wake-up sequence 0F0009000B070A020C01 first.
- When the picture is correct and reusable, save_skill it with a short description.

## Intent
Every shift_out, save_skill and reuse_skill call must include the intent argument: one short sentence saying what the call is for, e.g. "Draw the full smiley face frame." It is shown to the user and is never saved into skills.

## Skill JSON
A skill definition has only these top-level keys: type (always 'action_sequence'), description, params (optional) and actions. Every action is exactly {"tool": ..., "args": {...}}. shift_out args are exactly data_pin, clock_pin, latch_pin, group_size, data_hex. wait args are exactly duration_ms (0..10000). repeat args are exactly count (1..1000) and actions (at most 3 repeats nested). No other keys are allowed anywhere; the version is set by the store. A complete, valid example:
{"type": "action_sequence", "description": "Top-left LED blinks 3 times", "actions": [{"tool": "repeat", "args": {"count": 3, "actions": [{"tool": "shift_out", "args": {"data_pin": 25, "clock_pin": 26, "latch_pin": 27, "group_size": 2, "data_hex": "01800200030004000500060007000800"}}, {"tool": "wait", "args": {"duration_ms": 300}}, {"tool": "shift_out", "args": {"data_pin": 25, "clock_pin": 26, "latch_pin": 27, "group_size": 2, "data_hex": "01000200030004000500060007000800"}}, {"tool": "wait", "args": {"duration_ms": 300}}]}}]}

## Conventions
Letters and digits are 5 columns wide and 7 rows tall, using columns 1 to 5 and rows 0 to 6 (0-based, row 0 = top, column 0 = left), so every glyph sits in the same place. Symbols and patterns may use all 8x8. Skill names match ^[a-z0-9_]{1,40}$ and use these prefixes: glyph_a, digit_7, symbol_heart, pattern_checker, word_hi, anim_heartbeat.

## Words and numbers with several characters
Show one character per frame, with a wait of about 600 ms between frames. Build the word skill by reading each glyph skill with get_skill and copying its frame into the new skill. Skills cannot call other skills.

## Animations
Build and check each frame with its own shift_out call first. Then save all frames with wait steps inside a repeat. Then play it with reuse_skill. Use repeat for repeated frames: put one cycle (its frames and waits) inside one repeat whose count is the number of repetitions. Never write the same frames out more than once.

## History
read_shift_state shows the current picture at any time. read_recent_frames shows what was displayed recently (up to 30 frames).

## Limits
Do not use pins other than 25, 26 and 27. If a request cannot be shown legibly on 8x8, say so and offer the closest option.

## Finish
End with one short sentence saying what is now on the display and which skill was saved or reused.
```

`build_system_prompt()` takes no arguments (`agent/prompts.py:42`); everything comes from `config` and module constants.

---

## 6. `skills/store.py` and `skills/runner.py`

Both files are under 300 lines (162 and 81 lines), so both are pasted whole.

### `skills/store.py`

`skills/store.py:1-162`

```python
"""Skill store: validated, versioned JSON action sequences on disk (master section 6.6)."""

import json
import os
import re
from pathlib import Path

from config import MAX_WAIT_MS

NAME_RE = re.compile(r"[a-z0-9_]{1,40}")
HEX_RE = re.compile(r"(?:[0-9A-Fa-f]{2})+")
TOP_LEVEL_KEYS = {"type", "version", "description", "params", "actions"}
SHIFT_OUT_INTS = ("data_pin", "clock_pin", "latch_pin", "group_size")
ARG_KEYS = {
    "shift_out": set(SHIFT_OUT_INTS) | {"data_hex"},
    "wait": {"duration_ms"},
    "repeat": {"count", "actions"},
}
MAX_DESCRIPTION = 200
MAX_REPEAT_COUNT = 1000
MAX_REPEAT_DEPTH = 3


class SkillError(Exception):
    pass


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _check_name(name: object) -> None:
    if not isinstance(name, str) or NAME_RE.fullmatch(name) is None:
        raise SkillError(f"name must match ^[a-z0-9_]{{1,40}}$, got {name!r}")


def _is_param(value: object, params: dict | None, where: str) -> bool:
    """True if value is a `$name` placeholder. params=None means placeholders are not allowed."""
    if not (isinstance(value, str) and value.startswith("$")):
        return False
    if params is None or value[1:] not in params:
        raise SkillError(f"{where}: parameter {value!r} has no default in 'params'")
    return True


def _check_int(value: object, params: dict | None, where: str, low: int | None = None,
               high: int | None = None) -> None:
    if _is_param(value, params, where):
        return
    if not _is_int(value):
        raise SkillError(f"{where}: must be an integer, got {value!r}")
    if (low is not None and value < low) or (high is not None and value > high):
        raise SkillError(f"{where}: must be {low}..{high}, got {value}")


def _validate_actions(actions: object, params: dict | None, where: str, depth: int) -> None:
    if not isinstance(actions, list) or not actions:
        raise SkillError(f"{where}: must be a non-empty list")
    for i, action in enumerate(actions):
        at = f"{where}[{i}]"
        if not isinstance(action, dict) or set(action) != {"tool", "args"}:
            raise SkillError(f"{at}: must be an object with exactly 'tool' and 'args'")
        tool, args = action["tool"], action["args"]
        if tool not in ARG_KEYS:
            raise SkillError(f"{at}.tool: must be one of shift_out, wait, repeat, got {tool!r}")
        if not isinstance(args, dict) or set(args) != ARG_KEYS[tool]:
            raise SkillError(f"{at}.args: {tool} needs exactly {sorted(ARG_KEYS[tool])}")
        if tool == "shift_out":
            for key in SHIFT_OUT_INTS:
                _check_int(args[key], params, f"{at}.args.{key}")
            data_hex = args["data_hex"]
            if not _is_param(data_hex, params, f"{at}.args.data_hex") and (
                    not isinstance(data_hex, str) or HEX_RE.fullmatch(data_hex) is None):
                raise SkillError(f"{at}.args.data_hex: must be a non-empty even-length hex string")
        elif tool == "wait":
            _check_int(args["duration_ms"], params, f"{at}.args.duration_ms", 0, MAX_WAIT_MS)
        else:
            if depth >= MAX_REPEAT_DEPTH:
                raise SkillError(f"{at}: repeat nesting deeper than {MAX_REPEAT_DEPTH}")
            _check_int(args["count"], params, f"{at}.args.count", 1, MAX_REPEAT_COUNT)
            _validate_actions(args["actions"], params, f"{at}.args.actions", depth + 1)


def validate(definition: dict) -> None:
    if not isinstance(definition, dict):
        raise SkillError("definition must be an object")
    extra = set(definition) - TOP_LEVEL_KEYS
    if extra:
        raise SkillError(f"unknown keys: {sorted(extra)}")
    if definition.get("type") != "action_sequence":
        raise SkillError("type must be 'action_sequence'")
    description = definition.get("description", "")
    if not isinstance(description, str) or len(description) > MAX_DESCRIPTION:
        raise SkillError(f"description must be a string of at most {MAX_DESCRIPTION} characters")
    params = definition.get("params", {})
    if not isinstance(params, dict) or not all(
            isinstance(k, str) and (_is_int(v) or isinstance(v, str)) for k, v in params.items()):
        raise SkillError("params must be an object of name -> integer or string")
    _validate_actions(definition.get("actions"), params, "actions", 0)


def count_steps(definition: dict) -> int:
    actions = definition.get("actions")
    return len(actions) if isinstance(actions, list) else 0


class SkillStore:
    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, name: str) -> Path:
        _check_name(name)
        return self.directory / f"{name}.json"

    def list(self) -> list[dict]:
        skills = []
        for path in sorted(self.directory.glob("*.json")):
            try:
                definition = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(definition, dict):
                continue
            skills.append({
                "name": path.stem,
                "description": definition.get("description", ""),
                "steps": count_steps(definition),
                "version": definition.get("version"),
                "type": definition.get("type"),
            })
        return skills

    def get(self, name: str) -> dict:
        path = self._path(name)
        try:
            definition = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise SkillError(f"skill {name!r} not found") from None
        except (OSError, ValueError) as e:
            raise SkillError(f"skill {name!r} could not be read: {e}") from e
        if not isinstance(definition, dict):
            raise SkillError(f"skill {name!r} is not an object")
        return definition

    def save(self, name: str, definition: dict) -> dict:
        path = self._path(name)
        validate(definition)
        try:
            previous = self.get(name).get("version")
        except SkillError:
            previous = None
        version = previous + 1 if _is_int(previous) else 1
        stored = {"type": definition["type"], "version": version}
        for key in ("description", "params"):
            if key in definition:
                stored[key] = definition[key]
        stored["actions"] = definition["actions"]
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(stored, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
        return {"name": name, "version": version}
```

### `skills/runner.py`

`skills/runner.py:1-81`

```python
"""Skill runner: replays a validated skill through the Bridge. No LLM, no generated code."""

import time
from typing import Callable

from bridge.bridge import Bridge
from config import SKILL_TIME_CAP_S
from skills.store import SkillError, _validate_actions, validate


class _MissingParam(Exception):
    pass


def _substitute(actions: list, values: dict) -> list:
    resolved = []
    for action in actions:
        args = {}
        for key, value in action["args"].items():
            if key == "actions":
                value = _substitute(value, values)
            elif isinstance(value, str) and value.startswith("$"):
                if value[1:] not in values:
                    raise _MissingParam(value)
                value = values[value[1:]]
            args[key] = value
        resolved.append({"tool": action["tool"], "args": args})
    return resolved


def run_skill(definition: dict, bridge: Bridge, params: dict | None = None,
              should_stop: Callable[[], bool] | None = None,
              time_cap_s: float = SKILL_TIME_CAP_S) -> dict:
    try:
        validate(definition)
    except SkillError as e:
        return {"success": False, "error": str(e)}
    values = {**definition.get("params", {}), **(params or {})}
    try:
        actions = _substitute(definition["actions"], values)
        _validate_actions(actions, None, "actions", 0)
    except (_MissingParam, SkillError):
        return {"success": False, "error": "bad_params"}

    deadline = time.monotonic() + time_cap_s
    state = {"steps_run": 0, "stopped": False, "timed_out": False, "error": None}

    def interrupted() -> bool:
        if should_stop is not None and should_stop():
            state["stopped"] = True
        elif time.monotonic() >= deadline:
            state["timed_out"] = True
        return state["stopped"] or state["timed_out"]

    def run(block: list) -> bool:
        """Run actions in order. Returns False when the run must end."""
        for action in block:
            if interrupted():
                return False
            tool, args = action["tool"], action["args"]
            if tool == "shift_out":
                result = bridge.shift_out(**args)
                if not result["success"]:
                    state["error"] = result["error"]
                    return False
                state["steps_run"] += 1
            elif tool == "wait":
                bridge.wait(args["duration_ms"], interrupted)
                state["steps_run"] += 1
            elif tool == "repeat":
                for _ in range(args["count"]):
                    if not run(args["actions"]):
                        return False
        return True

    run(actions)
    if state["error"] is not None:
        return {"success": False, "error": state["error"], "steps_run": state["steps_run"],
                "final_state": bridge.store.current()}
    return {"success": True, "steps_run": state["steps_run"], "stopped": state["stopped"],
            "timed_out": state["timed_out"], "final_state": bridge.store.current()}
```

### Skill JSON schema as actually validated (`skills/store.py:84-99`, `skills/store.py:56-81`)

- Top-level keys allowed: `type`, `version`, `description`, `params`, `actions` (`skills/store.py:12`); any other key → `unknown keys` (`skills/store.py:87-89`).
- **Required**: `type` == `"action_sequence"` (`skills/store.py:90-91`); `actions` = non-empty list (`skills/store.py:57-58`, called at `:99`).
- **Optional**:
  - `description`: string, at most 200 characters, default `""` (`skills/store.py:19,92-94`).
  - `params`: object of `str` → `int` (not bool) or `str`; default `{}` (`skills/store.py:95-98`).
  - `version`: accepted by `validate` but its value is not checked (`skills/store.py:12`); `save` replaces it (`skills/store.py:153-154`).
- Each action: exactly `{"tool", "args"}` (`skills/store.py:61-62`); `tool` ∈ `shift_out`, `wait`, `repeat` (`skills/store.py:14-18,64-65`); `args` has exactly the tool's keys (`skills/store.py:66-67`).
  - `shift_out`: `data_pin`, `clock_pin`, `latch_pin`, `group_size` integers (no range check here) (`skills/store.py:69-70`); `data_hex` = non-empty, even-length hex (`skills/store.py:11,71-74`). No byte-count limit in the store; the 64-byte limit is enforced later by `Bridge` (`bridge/bridge.py:23-25`) and the device.
  - `wait`: `duration_ms` integer 0..`MAX_WAIT_MS` (10000) (`skills/store.py:75-76`).
  - `repeat`: `count` integer 1..1000 (`skills/store.py:20,80`), `actions` non-empty list validated recursively (`skills/store.py:81`); a `repeat` at depth ≥ 3 is rejected (`skills/store.py:21,78-79`), i.e. at most 3 nested `repeat` levels (tests: `tests/test_skills.py:103,130`).
- Any int field or `data_hex` may instead be a `"$name"` string, but only if `name` is a key of `params` (`skills/store.py:37-43,46-49,72`). The default value in `params` is **not** checked against the field's type or range at save time (`skills/store.py:48-49` returns before the checks) [INFERRED from code; the runner catches it later as `bad_params`].
- Name (not part of the definition): `^[a-z0-9_]{1,40}$`, checked in `SkillStore._path` (`skills/store.py:10,32-34,112-114`).

### Versioning on save (`skills/store.py:146-162`)

Overwrite with a bumped version. `save` reads the existing file's `version` (`skills/store.py:149-152`); new version = previous + 1 if it is an int, else 1 (`skills/store.py:153`). The file `<name>.json` is replaced atomically (`skills/store.py:159-161`). **Old versions are not kept** (single file per name; no history) — no other write path exists [NOT FOUND for any archive of old versions]. The stored dict is rebuilt in a fixed key order: `type`, `version`, then `description`/`params` if present, then `actions` (`skills/store.py:154-158`).

### `$param` substitution

`run_skill` merges `{**definition.get("params", {}), **(params or {})}` (`skills/runner.py:38`), then `_substitute` replaces every string arg value starting with `$` by `values[name]`, recursing into `repeat`'s `actions` (`skills/runner.py:15-28`). A missing name raises `_MissingParam` (`skills/runner.py:23-24`). The result is validated again with placeholders forbidden (`params=None`), so wrong types/ranges are caught (`skills/runner.py:41`); either failure returns `{"success": False, "error": "bad_params"}` (`skills/runner.py:42-43`).

**Can a `repeat` step take its count from a `$param`? Yes.** The deciding code is `_check_int(args["count"], params, f"{at}.args.count", 1, MAX_REPEAT_COUNT)` (`skills/store.py:80`), and `_check_int` returns early when `_is_param` is true (`skills/store.py:46-49`). Test: `tests/test_skills.py:154-169` (`repeat("$n", ...)` with `params={"n": 2}` runs 2 times by default and 4 times with `{"n": 4}`; `"x"`, `0`, `True` give `bad_params`).

### Can `run_skill` run a definition dict that is not stored?

**Yes.** `run_skill(definition: dict, bridge, params=None, should_stop=None, time_cap_s=SKILL_TIME_CAP_S)` (`skills/runner.py:31-33`) takes the dict itself and never touches the store; it validates the dict (`skills/runner.py:34-37`). Tests run inline dicts this way, e.g. `tests/test_skills.py:146`.

### Return value, 30 s cap, `should_stop`

- Invalid definition: `{"success": False, "error": "<SkillError message>"}` (`skills/runner.py:36-37`).
- Bad params: `{"success": False, "error": "bad_params"}` (`skills/runner.py:43`).
- A `shift_out` failed: `{"success": False, "error": <bridge error>, "steps_run": n, "final_state": <LED map>}` (`skills/runner.py:63-65,77-79`).
- Otherwise: `{"success": True, "steps_run": n, "stopped": bool, "timed_out": bool, "final_state": <LED map>}` (`skills/runner.py:80-81`). A stop or timeout still gives `success: True`.
- `steps_run` counts successful `shift_out`s and every `wait` (also a wait cut short) — `repeat` itself is not counted (`skills/runner.py:66,69`).
- Cap: deadline = now + `time_cap_s` (default `SKILL_TIME_CAP_S` = 30, `config.py:26`), set after validation (`skills/runner.py:45`). `interrupted()` is checked before every action (`skills/runner.py:58`) and is passed to `bridge.wait` as its stop callable, so a long wait ends at the deadline (`skills/runner.py:68`). `should_stop` is checked first, so a stop wins over a timeout (`skills/runner.py:48-53`). A `shift_out` already sent is not interrupted.

### Three real skill files

`skills/library/symbol_heart.json`:

`skills/library/symbol_heart.json:1-17`

```json
{
  "type": "action_sequence",
  "version": 1,
  "description": "Draw an 8x8 heart symbol on the LED matrix.",
  "actions": [
    {
      "tool": "shift_out",
      "args": {
        "data_pin": 25,
        "clock_pin": 26,
        "latch_pin": 27,
        "group_size": 2,
        "data_hex": "016602FF03FF047E053C061807000800"
      }
    }
  ]
}
```

`skills/library/anim_heartbeat.json`:

`skills/library/anim_heartbeat.json:1-47`

```json
{
  "type": "action_sequence",
  "version": 2,
  "description": "Animation: heart alternates with a smaller heart for 6 beats.",
  "actions": [
    {
      "tool": "repeat",
      "args": {
        "count": 6,
        "actions": [
          {
            "tool": "shift_out",
            "args": {
              "data_pin": 25,
              "clock_pin": 26,
              "latch_pin": 27,
              "group_size": 2,
              "data_hex": "016602FF03FF04FF057E063C07180800"
            }
          },
          {
            "tool": "wait",
            "args": {
              "duration_ms": 250
            }
          },
          {
            "tool": "shift_out",
            "args": {
              "data_pin": 25,
              "clock_pin": 26,
              "latch_pin": 27,
              "group_size": 2,
              "data_hex": "01000224037E047E053C061807000800"
            }
          },
          {
            "tool": "wait",
            "args": {
              "duration_ms": 250
            }
          }
        ]
      }
    }
  ]
}
```

A skill that uses params: **none.** No file in `skills/library/` contains a `"params"` key or a `"$` placeholder (`grep -l '"params"' skills/library/*.json` and `grep -l '"\$' skills/library/*.json` both return nothing) [NOT FOUND].

---

## 7. `bridge/` (store and bridge)

### `GridStore` public methods (`bridge/grid_store.py`)

| Method | Signature | Returns / effect |
|---|---|---|
| constructor | `__init__(self, state_path: Path, log_path: Path, msb_is_left: bool = True) -> None` (`:21`) | creates parent dirs, empty model, seq 0 (`:22-29`) |
| `load` | `load(self) -> bool` (`:50`) | restores model, `seq`, `timestamp`, `bytes` from the state file; a corrupt file is renamed `.corrupt` and `False` returned (`:50-66`) |
| `record` | `record(self, data: bytes, group_size: int = 2) -> dict` (`:68`) | applies bytes to the model, commits a frame, notifies listeners, returns the LED map (`:68-73`) |
| `mark_unknown` | `mark_unknown(self) -> dict` (`:75`) | resets the model to unknown, commits a frame with `bytes: ""` (`:75-80`) |
| `current` | `current(self) -> dict` (`:112`) | deep copy of the current LED map (`:112-114`) |
| `recent` | `recent(self, count: int) -> list[dict]` (`:116`) | last `count` (1..30) frames from the log file, oldest first (`:116-137`) |
| `resync_bytes` | `resync_bytes(self) -> bytes` (`:139`) | bytes that re-send the whole model state (`:139-141`) |
| `add_listener` | `add_listener(self, fn: Callable[[dict], None]) -> None` (`:143`) | appends a listener (`:143-145`) |

Public attributes: `state_path`, `log_path`, `msb_is_left` (`bridge/grid_store.py:22-24`). There is no `remove_listener` [NOT FOUND].

**One line of `logs/shift_frames.jsonl`** — the LED map dict exactly as built by `_build_map` (`bridge/grid_store.py:38-48`), written with `json.dumps(self._map) + "\n"` (`bridge/grid_store.py:98-101`). Real first line:

`logs/shift_frames.jsonl:1-1`

```json
{"seq": 1, "timestamp": 1791333268.1549757, "display": "on", "intensity": 2, "rows": ["00000000", "00000000", "00000000", "00000000", "00000000", "00000000", "00000000", "00000000"], "bytes": "0F0009000B070A02010002000300040005000600070008000C01", "warnings": []}
```

**`seq`**: a per-store integer counter (`bridge/grid_store.py:33`), incremented by 1 in `_commit` (`bridge/grid_store.py:82-83`), i.e. on every `record` (`:71`) and every `mark_unknown` (`:78`). `record` is called by `Bridge._send_and_record` only when the device replied `OK` **and** the pins are exactly `(DATA_PIN, CLOCK_PIN, LATCH_PIN)` (`bridge/bridge.py:70-74`); so a failed `shift_out` or one on other pins does not increment it. Every `Bridge.start` / `reconnect` / reboot re-sync is a `shift_out` and therefore also one frame (`bridge/bridge.py:39,57-60,87-88,99-100`). `seq` survives restarts via the state file (`bridge/grid_store.py:57,64,91-96`). `mark_unknown` is not called anywhere in production code or tests (grep finds only its definition, `bridge/grid_store.py:75`).

**All frames after a given `seq`, uncapped: [NOT FOUND].** The closest existing method is `GridStore.recent(count)` (`bridge/grid_store.py:116-137`), capped at 30 (`:117`) and reading only the last 64 KiB of the file (`:15,121-128`). Other options that exist today: reading `log_path` directly (each line has `seq`), or a listener registered with `add_listener` that sees every committed map as it happens.

**Listeners**: registered with `add_listener(fn)` (`bridge/grid_store.py:143-145`). After `record`/`mark_unknown` releases the lock, `_notify` calls each `fn(copy.deepcopy(led_map))` in the caller's thread; exceptions are logged and ignored (`bridge/grid_store.py:103-110`). They receive the same LED map dict as the log line (keys `seq, timestamp, display, intensity, rows, bytes, warnings`). The only production listener is the server's `shift_state` emitter (`server/app.py:101`).

### `Bridge` public methods (`bridge/bridge.py`)

| Method | Signature | Returns |
|---|---|---|
| constructor | `__init__(self, transport, store: GridStore) -> None` (`:29`) | sets `transport`, `store`, `connected = False` (`:30-33`) |
| `start` | `start(self) -> None` (`:35`) | `transport.connect()`, `store.load()`, `connected = True`, `resync()` (`:36-39`); raises `TransportError` on failure |
| `ping` | `ping(self) -> bool` (`:41`) | `True` if reply is `OK PONG`; updates `connected` (`:42-47`) |
| `shift_out` | `shift_out(self, data_pin: int, clock_pin: int, latch_pin: int, group_size: int, data_hex: str) -> dict` (`:49-50`) | `{"success": False, "error": "bad_args"}` (`:52`); `{"success": False, "error": "timeout"}` (`:69`); `{"success": False, "error": reply, "raw_response": reply}` (`:71`); `{"success": True, "raw_response": "OK"}` plus `"decoded_state": <LED map>` on the bound pins (`:72-75`); plus `"resynced": True` after a device reboot (`:57-60`) |
| `wait` | `wait(self, duration_ms: int, should_stop: Callable[[], bool] \| None = None) -> dict` (`:77`) | `{"success": True}` or `{"success": True, "stopped": True}`; clamps to 0..`MAX_WAIT_MS`, sleeps in 50 ms slices (`:13,78-85`) |
| `resync` | `resync(self) -> dict` (`:87`) | result of `shift_out` with `store.resync_bytes()` (`:88`) |
| `reconnect` | `reconnect(self) -> bool` (`:90`) | closes, reconnects, resyncs; returns `connected` (`:91-101`) |
| `close` | `close(self) -> None` (`:103`) | closes transport, `connected = False` (`:104-106`) |

### Building a `Bridge` on the fake device in a test (shortest real example)

`tests/test_skills.py:40-43`

```python
def make_bridge(tmp_path) -> Bridge:
    bridge = Bridge(FakeTransport(), GridStore(tmp_path / "shift_state.json", tmp_path / "shift_frames.jsonl"))
    bridge.start()
    return bridge
```

`FakeTransport()` with no argument creates its own `FakeDevice()` (`bridge/transport.py:103-104`), which allows only pins 25, 26, 27 (`bridge/fake_device.py:12`). The sent bytes are visible as `bridge.transport.device.sent` (`bridge/fake_device.py:14,49`).

---

## 8. `server/runs.py` and `server/app.py`

### `RunManager` (verbatim — the whole file)

`server/runs.py:1-61`

```python
"""Run manager: one agent run at a time on a daemon thread, with a stop flag."""

import logging
import threading
from typing import Callable

from agent.loop import run_agent
from agent.tools import AgentContext

MAX_ERROR_LEN = 200

logger = logging.getLogger(__name__)


class RunBusy(Exception):
    pass


class RunManager:
    def __init__(self, ctx_factory: Callable[[Callable[[], bool]], AgentContext],
                 emit: Callable[[dict], None], client=None) -> None:
        self._ctx_factory = ctx_factory
        self._emit = emit
        self._client = client
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._counter = 0
        self.busy = False
        self.run_id: str | None = None

    def start(self, prompt: str) -> str:
        with self._lock:
            if self.busy:
                raise RunBusy()
            self._counter += 1
            run_id = f"r_{self._counter:04d}"
            self.busy = True
            self.run_id = run_id
            self._stop.clear()
        self._emit({"type": "status", "busy": True, "run_id": run_id})
        threading.Thread(target=self._run, args=(run_id, prompt), daemon=True).start()
        return run_id

    def stop(self) -> None:
        self._stop.set()

    def _run(self, run_id: str, prompt: str) -> None:
        self._emit({"type": "run_started", "run_id": run_id, "prompt": prompt})
        try:
            ctx = self._ctx_factory(self._stop.is_set)
            result = run_agent(prompt, ctx, lambda event: self._emit({**event, "run_id": run_id}),
                               client=self._client)
        except Exception as e:
            logger.exception("Run %s failed", run_id)
            result = {"status": "error", "summary": "", "error": f"{type(e).__name__}: {e}"[:MAX_ERROR_LEN]}
        self._emit({"type": "run_finished", "run_id": run_id, "status": result["status"],
                    "summary": result["summary"], "error": result["error"]})
        with self._lock:
            self.busy = False
            self.run_id = None
        self._emit({"type": "status", "busy": False, "run_id": None})
```

### Concurrency model

- **One daemon thread per run.** `RunManager.start` creates `threading.Thread(target=self._run, args=(run_id, prompt), daemon=True)` (`server/runs.py:41`). Only one run at a time: `busy` under a lock, a second start raises `RunBusy` (`server/runs.py:32-34`) → HTTP 409 (`server/app.py:141-142`).
- **Path to `run_agent`**: `POST /api/prompt` (sync handler, `server/app.py:130-143`) → `s.runs.start(prompt)` (`server/app.py:140`) → thread `_run` → `ctx = self._ctx_factory(self._stop.is_set)` (`server/runs.py:50`), where the factory is `lambda should_stop: AgentContext(s.bridge, s.skills, should_stop)` (`server/app.py:95`) → `run_agent(prompt, ctx, lambda event: self._emit({**event, "run_id": run_id}), client=self._client)` (`server/runs.py:51-52`). `model` and `max_turns` are not passed.
- **Events to the WebSocket**: `self._emit` is `run_emit` (`server/app.py:95`), which adds `connected` to `status` events (`server/app.py:59-62`) and calls `emit`. `emit` (`server/app.py:46-54`), under one lock: adds `ts`, optionally appends to `EVENTS_LOG` when `RECORD_EVENTS=1` (`server/app.py:49-51,91`), and hands the event to the asyncio loop with `s.loop.call_soon_threadsafe(s.queue.put_nowait, (None, event))` (`server/app.py:54`). A single `sender()` task (`server/app.py:64-71,102`) pops `(target, event)` and `send_json`s to `target` or to every client in `s.clients`; a failing socket is dropped. `shift_state` events come from the `GridStore` listener (`server/app.py:101`), called in the worker thread inside `Bridge.shift_out` — so they appear between `tool_call` and `tool_result` (`tests/test_server.py:113-114`). The on-connect `status` goes only to the new client (`server/app.py:154`).
- **Health task**: `health()` pings or reconnects every 5 s via `asyncio.to_thread`, skipped while a run is busy (`server/app.py:31,73-85`).
- The REST handlers are plain `def` functions (`server/app.py:114-148`); FastAPI runs those in its threadpool [INFERRED from FastAPI behaviour, not shown in this code].

### 500-character limit

Only at the HTTP endpoint: `MAX_PROMPT_CHARS = 500` (`server/app.py:32`) and `if len(prompt) > MAX_PROMPT_CHARS: return JSONResponse({"error": "prompt_too_long"}, status_code=400)` after `.strip()` (`server/app.py:132-136`). A search of `*.py` and `*.js` finds no other 500-character check: `RunManager.start` (`server/runs.py:31-42`), `run_agent` (`agent/loop.py:34-38`) and `agent/cli.py` (`agent/cli.py:41,60`) do not limit it. The dashboard's input has `maxlength="2000"` (`server/static/app.js:56`), not 500. Test: `tests/test_server.py:95-99`.

### Stop end to end

1. `POST /api/stop` → `s.runs.stop()` → `{"stopped": True}` (`server/app.py:145-148`), always 200, also when idle (`tests/test_server.py:174-176`).
2. `RunManager.stop` sets a `threading.Event` (`server/runs.py:44-45`); `start` clears it for the next run (`server/runs.py:39`).
3. The agent sees it through `ctx.should_stop` = `self._stop.is_set` (`server/runs.py:50`, `server/app.py:95`): before each model request and each tool call (`agent/loop.py:51,77`), inside `Bridge.wait` every 50 ms (`agent/tools.py:126`, `bridge/bridge.py:80-81`), and inside `run_skill` before each action and during waits (`agent/tools.py:138`, `skills/runner.py:48-53,58,68`).
4. `run_agent` returns `status: "stopped"` (`agent/loop.py:41-42`); `_run` emits `run_finished` then `status` busy=false (`server/runs.py:56-61`). Test: `tests/test_server.py:160-171`.
5. An in-flight LLM request is not cancelled; the stop takes effect after it returns [INFERRED from `agent/loop.py:51-54`]. App shutdown also calls `s.runs.stop()` (`server/app.py:108`).

### `create_app`

`server/app.py:41-43`

```python
def create_app(port: str | None = SERIAL_PORT, state_file: Path = STATE_FILE, frames_log: Path = FRAMES_LOG,
               skills_dir: Path = SKILLS_DIR, events_log: Path = EVENTS_LOG,
               client=None, transport=None) -> FastAPI:
```

A test can inject: `port` (`"fake"` → `FakeTransport`, `bridge/transport.py:129-131`), the state file, frames log, skills dir and events log paths, `client` (the LLM client, passed to `RunManager` and on to `run_agent`, `server/app.py:95`, `server/runs.py:52`), and `transport` (overrides `port`, `server/app.py:93`). It cannot inject the context factory, system prompt, tool set, model or `max_turns` [NOT FOUND]. Module import runs `app = create_app()` (`server/app.py:165`), but all I/O is in the lifespan (`server/app.py:87-109`). Test usage: `tests/test_server.py:53-63`.

### One real payload per WebSocket event

All from the real recorded run log `logs/sample_events.jsonl` (written by `emit` when `RECORD_EVENTS=1`, `server/app.py:49-51`); the shape of each is produced at the cited code line.

- `status` (`server/runs.py:40` + `server/app.py:59-61`), `logs/sample_events.jsonl:1`:
  `{"type": "status", "busy": true, "run_id": "r_0001", "connected": true, "ts": 1791342321.559256}`
  and idle, `logs/sample_events.jsonl:11`: `{"type": "status", "busy": false, "run_id": null, "connected": true, "ts": 1791342332.5304906}`. (Health/on-connect variant: `status_event()`, `server/app.py:56-57`, keys in order `type, connected, busy, run_id`; test `tests/test_server.py:121`.)
- `shift_state` (`server/app.py:101`), `logs/sample_events.jsonl:7`:
  `{"type": "shift_state", "state": {"seq": 58, "timestamp": 1791342328.2247708, "display": "on", "intensity": 2, "rows": ["01100110", "11111111", "11111111", "01111110", "00111100", "00011000", "00000000", "00000000"], "bytes": "016602FF03FF047E053C061807000800", "warnings": []}, "ts": 1791342328.229757}`
  Note: no `run_id` (the listener does not know the run).
- `run_started` (`server/runs.py:48`), `logs/sample_events.jsonl:2`:
  `{"type": "run_started", "run_id": "r_0001", "prompt": "Draw a heart", "ts": 1791342321.5607953}`
- `agent_message` (`agent/loop.py:64` or `:84`), `logs/sample_events.jsonl:5` (an intent):
  `{"type": "agent_message", "text": "Draw the saved heart symbol.", "run_id": "r_0001", "ts": 1791342328.1618829}`
- `tool_call` (`agent/loop.py:85-86`), `logs/sample_events.jsonl:33`:
  `{"type": "tool_call", "call_id": "call_21eabc86f2774ccb829b935b0e7fa5a5", "tool": "shift_out", "args": {"clock_pin": 26, "data_hex": "01000200030004000500060007000800", "data_pin": 25, "group_size": 2, "latch_pin": 27}, "run_id": "r_0001", "ts": 1791346030.8191445}`
- `tool_result` (`agent/loop.py:93-94`), `logs/sample_events.jsonl:35`:
  `{"type": "tool_result", "call_id": "call_21eabc86f2774ccb829b935b0e7fa5a5", "tool": "shift_out", "result": {"success": true, "raw_response": "OK", "decoded_state": {"seq": 62, "timestamp": 1791346030.8851311, "display": "on", "intensity": 2, "rows": ["00000000", "00000000", "00000000", "00000000", "00000000", "00000000", "00000000", "00000000"], "bytes": "01000200030004000500060007000800", "warnings": []}}, "duration_ms": 62, "run_id": "r_0001", "ts": 1791346030.8913949}`
- `skill_saved` (`agent/loop.py:95-96`), `logs/sample_events.jsonl:39`:
  `{"type": "skill_saved", "name": "pattern_clear", "version": 1, "run_id": "r_0001", "ts": 1791346033.7544553}`
- `run_finished` (`server/runs.py:56-57`), `logs/sample_events.jsonl:10`:
  `{"type": "run_finished", "run_id": "r_0001", "status": "completed", "summary": "A heart is now on the display, using the saved skill `symbol_heart`.", "error": null, "ts": 1791342332.5288951}`

---

## 9. Dashboard (`server/static/`)

### Files

| File | Lines | Purpose |
|---|---|---|
| `index.html` | 15 | Shell page: `<div id="led-dashboard">` and `<script type="module" src="./app.js">` (`server/static/index.html:13-14`). |
| `app.js` | 260 | Builds the whole UI as an HTML string, chooses mock or live client, wires store → grid/trace rendering (`server/static/app.js:35-37,115,230`). |
| `api.js` | 124 | Live backend client: REST calls, WebSocket with reconnect, `normalizeEvent` mapping server events to internal kinds. |
| `store.js` | 111 | State container: applies normalized events to status, map, history and trace entries (`server/static/store.js:29-69`). |
| `trace.js` | 99 | Renders the agent trace list from store entries (`server/static/trace.js:11-99`). |
| `grid.js` | 39 | Renders an 8×8 LED grid (large and thumbnail) (`server/static/grid.js:1`). |
| `icons.js` | 24 | Inline SVG icon paths (`server/static/icons.js:1`). |
| `mock.js` | 159 | Mock backend with simulated runs and `?replay=` of a recording (`server/static/mock.js:18-28,70-78,150-152`). |
| `config.js` | 10 | `CONFIG`: `apiBase`, `wsPath`, `mock: null`, suggested prompts, `maxTraceEntries: 500`, `maxHistoryFrames: 30`. |
| `demo-recording.ndjson` | 13 | Recorded event stream for mock replay (`run_id` `r_recorded_01`) [INFERRED purpose from `server/static/mock.js:70-78`]. |
| `style.css` | 350 | Styles. |
| `favicon.svg` | 1 (575 bytes, no trailing newline) | Tab icon (`server/static/index.html:9`). |
| `README.md` | 76 | Frontend developer's notes on running, mock/live modes and simulation lab. |

### `api.js` (verbatim)

`server/static/api.js:1-125`

```javascript
export const EVENTS = Object.freeze({
  STATUS: "connection.status", MAP: "map.updated", START: "run.begin",
  MESSAGE: "agent.text", CALL: "tool.begin", RESULT: "tool.end",
  SAVED: "skill.stored", FINISH: "run.end"
});

export function normalizeMap(raw) {
  return raw && typeof raw === "object" ? { ...raw } : null;
}

export function normalizeStatus(raw = {}) {
  raw ??= {};
  return { connected: Boolean(raw.connected), busy: Boolean(raw.busy), run_id: raw.run_id ?? null, runId: raw.run_id ?? null };
}

const normalizeReply = raw => ({ ...raw });

function summarizeTool(tool, args = {}) {
  switch (tool) {
    case "shift_out": return String(args.data_hex ?? "").slice(0, 24);
    case "wait": return `${args.duration_ms ?? "?"} ms`;
    case "read_recent_frames": return `${args.count ?? "?"} frames`;
    case "get_skill": case "save_skill": return String(args.name ?? "");
    case "reuse_skill": return String(args.skill_name ?? "");
    case "read_shift_state": case "list_skills": return "";
    default: return Object.keys(args).slice(0, 2).map(key => `${key}: ${String(args[key])}`).join(", ").slice(0, 60);
  }
}

export function normalizeEvent(raw) {
  if (!raw || typeof raw !== "object") return null;
  const at = raw.ts ?? Date.now() / 1000;
  const base = { ...raw, ts: at, at, runId: raw.run_id ?? null };
  const args = raw.args ?? {};
  switch (raw.type) {
    case "status": return { ...base, kind: EVENTS.STATUS, status: normalizeStatus(raw) };
    case "shift_state": return { ...base, kind: EVENTS.MAP, map: normalizeMap(raw.state) };
    case "run_started": return { ...base, kind: EVENTS.START, prompt: raw.prompt ?? "" };
    case "agent_message": return { ...base, kind: EVENTS.MESSAGE, text: raw.text ?? "" };
    case "tool_call": return {
      ...base, kind: EVENTS.CALL, callId: raw.call_id, tool: raw.tool ?? "unknown",
      args, summary: summarizeTool(raw.tool, args), preview: null,
      activity: raw.tool === "shift_out" ? "Drawing..." :
        ["read_shift_state", "read_recent_frames"].includes(raw.tool) ? "Checking the grid..." :
        raw.tool === "reuse_skill" ? `Playing ${raw.args?.skill_name ?? "a skill"}...` :
        ["get_skill", "list_skills"].includes(raw.tool) ? "Looking through saved skills..." : null
    };
    case "tool_result": return {
      ...base, kind: EVENTS.RESULT, callId: raw.call_id, tool: raw.tool ?? "unknown",
      result: raw.result ?? {}, duration: raw.duration_ms ?? null,
      success: raw.result?.success !== false, preview: normalizeMap(raw.result?.decoded_state ?? raw.result?.final_state ?? raw.result?.state)
    };
    case "skill_saved": return { ...base, kind: EVENTS.SAVED, name: raw.name ?? "unnamed", version: raw.version ?? 1 };
    case "run_finished": return {
      ...base, kind: EVENTS.FINISH, outcome: raw.status ?? "completed",
      summary: raw.summary ?? "", error: raw.error ?? null
    };
    default: return null;
  }
}

export function clientError(status, error) {
  return { status, error, code: status === 409 ? "busy" : status === 400 ? "empty" : status === 503 ? "offline" : "network" };
}

export function createApiClient(config) {
  const events = new Set(), connections = new Set(), abort = new AbortController();
  let socket, retryTimer, attempt = 0, disposed = false, started = false;
  const base = config.apiBase.replace(/\/$/, "");
  async function request(path, options = {}) {
    try {
      const response = await fetch(base + path, {
        ...options, signal: abort.signal,
        headers: { "Content-Type": "application/json", ...options.headers }
      });
      const data = await response.json().catch(() => null);
      if (!response.ok) throw clientError(response.status, data?.error);
      if (data === null) throw clientError(0, "invalid_json");
      return data;
    } catch (error) {
      if (error?.code) throw error;
      throw clientError(0, "network_error");
    }
  }
  function connect() {
    if (disposed) return;
    try {
      const url = new URL(config.wsPath, config.apiBase || location.href);
      url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
      const ws = new WebSocket(url);
      socket = ws;
      ws.onopen = () => { attempt = 0; connections.forEach(fn => fn("open")); };
      ws.onmessage = message => {
        try { const event = normalizeEvent(JSON.parse(message.data)); if (event) events.forEach(fn => fn(event)); }
        catch { /* Ignore a malformed message without interrupting the stream. */ }
      };
      ws.onerror = () => ws.close();
      ws.onclose = reconnect;
    } catch { reconnect(); }
  }
  function reconnect() {
    if (disposed) return;
    connections.forEach(fn => fn("closed"));
    clearTimeout(retryTimer);
    retryTimer = setTimeout(connect, Math.min(1000 * 2 ** attempt++, 10000));
  }
  return {
    getStatus: async () => normalizeStatus(await request("/api/status")),
    getShiftState: async () => normalizeMap(await request("/api/shift_state")),
    getFrames: async count => {
      const raw = await request(`/api/frames?count=${Math.max(1, Math.min(30, count ?? 30))}`);
      return { frames: (raw.frames ?? []).map(normalizeMap).filter(Boolean) };
    },
    getSkills: async () => normalizeReply(await request("/api/skills")),
    sendPrompt: async prompt => normalizeReply(await request("/api/prompt", { method: "POST", body: JSON.stringify({ prompt }) })),
    stop: async () => normalizeReply(await request("/api/stop", { method: "POST" })),
    onEvent(fn) { events.add(fn); return () => events.delete(fn); },
    onConnection(fn) {
      connections.add(fn);
      if (!started) { started = true; queueMicrotask(connect); }
      return () => connections.delete(fn);
    },
    dispose() { disposed = true; clearTimeout(retryTimer); abort.abort(); socket?.close(); events.clear(); connections.clear(); }
  };
}
```

### Which file renders the agent trace; unknown event types

- Rendering: `server/static/trace.js` (`createTrace`, `server/static/trace.js:11-99`), fed by `store.state.trace` (`server/static/app.js:115,230`). Trace entries are created in `server/static/store.js:39-66` with a `view` of `start`, `message`, `tool`, `saved`, `finish`.
- Unknown event type: **silently ignored.** `normalizeEvent` returns `null` for any `type` not in its switch (`server/static/api.js:58`), and `ws.onmessage` only dispatches when the result is truthy (`server/static/api.js:94`). A malformed JSON message is also ignored (`server/static/api.js:95`). Even if an event reached the store with an unknown `kind`, `apply`'s switch has no default and nothing is added (`server/static/store.js:32-67`); `trace.js`'s `build` produces an empty `<article>` for an unknown `view` (`server/static/trace.js:31-74`). No error, no raw display.

### What would have to change for `role` and for `round_started` / `audit_verdict`

Files and functions only:

- `role` on events: backend — `agent/loop.py` (`_emit` / `run_agent` event dicts) or `server/runs.py` (`RunManager._run`, the `on_event` lambda). Frontend — nothing is required for it to pass through, because `normalizeEvent` spreads all raw fields into `base` (`server/static/api.js:33`); to **show** it: `server/static/trace.js` `build` (labels such as `"Agent"` at `server/static/trace.js:40`) and possibly `server/static/style.css`.
- New events `round_started`, `audit_verdict`: `server/static/api.js` — `EVENTS` and `normalizeEvent` (new `case`s); `server/static/store.js` — `apply` (new `case`s and `addTrace` with new `view`s); `server/static/trace.js` — `build` (new `view` branches); `server/static/style.css` (styles for the new entries); `server/static/mock.js` if mock mode should produce them; backend emitters (`server/runs.py` and/or the new orchestration code). `server/static/app.js` would need a change only if the status line or controls should react (`render` around `server/static/app.js:220-230`).

---

## 10. Tests

### Files and test counts

Counts are static (the suite was not run, see section 1): number of `def test_` functions, with each `@pytest.mark.parametrize` expanded by the length of its list.

| File | `def test_` | Parametrized (cases) | Collected tests |
|---|---|---|---|
| `tests/test_bridge.py` | 10 | `test_bad_hex_sends_nothing` ×5 (`tests/test_bridge.py:60`) | 14 |
| `tests/test_fake_device.py` | 7 | `test_validation_failures` ×8 (`tests/test_fake_device.py:29-38`) | 14 |
| `tests/test_grid_model.py` | 14 | — | 14 |
| `tests/test_grid_store.py` | 10 | — | 10 |
| `tests/test_loop.py` | 12 | — | 12 |
| `tests/test_prompts.py` | 3 | — | 3 |
| `tests/test_server.py` | 13 | `test_empty_prompt_400` ×2 (`:87`), `test_run_events_in_order` ×2 (`:102`) | 15 |
| `tests/test_skills.py` | 12 | `test_validation_rejects` ×26 (`:83-113`), `test_bad_name_rejected` ×5 (`:122`), `test_other_tools_rejected` ×3 (`:135`) | 43 |
| `tests/test_tools.py` | 24 | `test_split_intent` ×5 (`:73-79`), `test_shift_out_bad_args` ×4 (`:97-102`), `test_read_recent_frames_bad_count` ×3 (`:134`) | 33 |
| **Total** | 105 | | **158** |

### Scripted LLM client

There are **two separate copies**, one per test file, with different bodies; there is no shared module or `conftest.py`.

`tests/test_loop.py` (records every request):

`tests/test_loop.py:17-45`

```python
def call(call_id: str, name: str, args) -> SimpleNamespace:
    arguments = args if isinstance(args, str) else json.dumps(args)
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=arguments))


def reply(content: str | None = None, tool_calls: list | None = None) -> SimpleNamespace:
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def shift(call_id: str, data_hex: str) -> SimpleNamespace:
    return call(call_id, "shift_out", {"data_pin": 25, "clock_pin": 26, "latch_pin": 27,
                                       "group_size": 2, "data_hex": data_hex})


class ScriptedClient:
    """Returns prepared replies in order; the last one repeats. Records every request."""

    def __init__(self, replies: list) -> None:
        self.replies = replies
        self.requests: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        item = self.replies[min(len(self.requests), len(self.replies)) - 1]
        if isinstance(item, Exception):
            raise item
        return item
```

`tests/test_server.py` (counts calls only):

`tests/test_server.py:31-41`

```python
class ScriptedClient:
    """Returns prepared replies in order; the last one repeats. Never calls a real LLM."""

    def __init__(self, replies: list) -> None:
        self.replies = replies
        self.calls = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **_kwargs):
        self.calls += 1
        return self.replies[min(self.calls, len(self.replies)) - 1]
```

It works by duck typing: `run_agent` only calls `client.chat.completions.create(model=..., messages=..., tools=...)` and reads `.choices[0].message.content` / `.tool_calls[i].id / .function.name / .function.arguments` (`agent/loop.py:54-55,59-60,69-70,79-80`). `SimpleNamespace` objects stand in for the OpenAI response types. The last reply repeats once the list is exhausted (`tests/test_loop.py:42`).

Shortest real test that drives `run_agent` with it (calls `run_agent` directly; `ctx` fixture below):

`tests/test_loop.py:151-157`

```python
def test_on_event_exceptions_are_ignored(ctx):
    def bad_listener(_event: dict) -> None:
        raise RuntimeError("listener broke")

    client = ScriptedClient([reply(None, [shift("c1", HEART_HEX)]), reply("Done.")])
    result = run_agent("Draw a heart", ctx, bad_listener, client=client, model="m")
    assert result["status"] == "completed"
```

(Even shorter, via the file's `run` helper `tests/test_loop.py:55-58`: `test_max_turns`, `tests/test_loop.py:137-141`.)

### Shared fixtures (`conftest.py`)

**[NOT FOUND]** — there is no `conftest.py` anywhere in the folder. Fixtures are defined per file and duplicated:

`tests/test_loop.py`:

`tests/test_loop.py:48-52`

```python
@pytest.fixture
def ctx(tmp_path) -> AgentContext:
    bridge = Bridge(FakeTransport(), GridStore(tmp_path / "shift_state.json", tmp_path / "shift_frames.jsonl"))
    bridge.start()
    return AgentContext(bridge=bridge, skills=SkillStore(tmp_path / "library"), should_stop=lambda: False)
```

`tests/test_tools.py` (identical body):

`tests/test_tools.py:32-36`

```python
@pytest.fixture
def ctx(tmp_path) -> AgentContext:
    bridge = Bridge(FakeTransport(), GridStore(tmp_path / "shift_state.json", tmp_path / "shift_frames.jsonl"))
    bridge.start()
    return AgentContext(bridge=bridge, skills=SkillStore(tmp_path / "library"), should_stop=lambda: False)
```

`tests/test_server.py`:

`tests/test_server.py:53-63`

```python
@pytest.fixture
def make_server(tmp_path, monkeypatch):
    monkeypatch.delenv("RECORD_EVENTS", raising=False)

    def make(replies: list | None = None, transport=None) -> TestClient:
        app = create_app(port="fake", state_file=tmp_path / "shift_state.json",
                         frames_log=tmp_path / "shift_frames.jsonl", skills_dir=tmp_path / "library",
                         events_log=tmp_path / "sample_events.jsonl",
                         client=ScriptedClient(replies or [reply("Done.")]), transport=transport)
        return TestClient(app)
    return make
```

Helpers such as `HEART_HEX`, `frame`, `reply`, `call`, `shift`, `rows_of`, `make_bridge` are also redefined per file (e.g. `tests/test_loop.py:13,17-29`, `tests/test_tools.py:13,17-29`, `tests/test_skills.py:12-43`, `tests/test_bridge.py:11-21`).

---

## 11. Docs

### `docs/DEVIATIONS.md` (verbatim, every entry)

`docs/DEVIATIONS.md:1-147`

```markdown
# Deviations

## Stage 1

- What: added `.venv/` to `.gitignore` (not in the stage 1 list).
  Why: the user chose a local virtual environment in `led_grid/.venv` (Python 3.11.9 via `py -3.11`; the `python` on PATH is MSYS2 3.12, which cannot install the wheels in `requirements.txt`).
  Files: `.gitignore`.

## Stage 3

- What: the firmware was written from scratch, without reading `POC/`. Board settings come from the stage file.
  Why: user instruction for this session. It overrides the stage file's "Reference (read-only)" line and master section 9.
  Files: `firmware/platformio.ini`, `firmware/src/main.cpp`.
- What: H1 (flash) is done by the user. Claude Code only builds (`pio run`) and does not upload or open the serial port.
  Why: user instruction.
  Files: none.
- What: H3 adds `SHIFT_OUT 25 26 27 2 0100` (clear the top row) before `SHIFT_OUT 25 26 27 2 0880`.
  Why: as written, step 2's top-row LED stays lit during step 3, so two LEDs would show instead of "the lit LED is now in the bottom row". The user chose this fix.
  Files: `docs/WIRING.md`.
- What: interpretation choices not spelled out in the spec, the same in firmware and fake:
  - The line buffer holds at most 255 characters (256 bytes with the terminator). A trailing `\r` counts toward the limit. The longest valid command is about 155 characters.
  - `PING` followed by extra tokens replies `ERR BAD_ARGS`.
  - "Decimal integer" means 1 to 9 ASCII digits. Signs and longer numbers reply `ERR BAD_ARGS`.
  Files: `firmware/src/main.cpp`, `bridge/fake_device.py`.

## Stage 4

- What: the transport was written from the stage spec alone, without reading `POC/`.
  Why: user instruction for this session. It overrides the stage file's "Reference (read-only)" line and master section 9.
  Files: `bridge/transport.py`.
- What: the user runs `scripts/manual_patterns.py` against the real port. Claude Code does not open the serial port.
  Why: user instruction.
  Files: none.
- What: interpretation choices not spelled out in the spec:
  - `SerialTransport` reads with a 0.1 s slice and buffers partial lines, so a read timeout in the middle of a line never produces a broken reply. `send` waits up to `SERIAL_TIMEOUT_S` in total.
  - `FakeTransport.send` raises `TransportError` when the fake device gives no reply (empty line), the same as a timeout.
  - `Bridge.shift_out` treats any reply other than exactly `OK` as a failure (`error` = reply text), not only replies that start with `ERR`.
  - Python-side validation rejects `bool` as an int.
  Files: `bridge/transport.py`, `bridge/bridge.py`.

## Stage 5

- What: the user runs `scripts/run_skill.py` against the real port. Claude Code does not open the serial port. `anim_test_blink` was written by calling `SkillStore.save`.
  Why: user instruction.
  Files: `skills/library/anim_test_blink.json`.
- What: interpretation choices not spelled out in the spec:
  - The name check (`^[a-z0-9_]{1,40}$`) is done in `SkillStore.get`/`save`, because `validate(definition)` has no name. This also blocks path traversal.
  - Each action must be exactly `{tool, args}`, and `args` must hold exactly the keys its tool needs (no missing or extra keys). Ints exclude `bool`. A skill's `data_hex` must be non-empty. Size limits are left to the bridge.
  - "Nesting depth at most 3" means at most 3 nested `repeat` levels.
  - `run_skill` validates the definition first and returns `{"success": False, "error": <message>}` if it is invalid. After `$name` substitution it validates again, and a missing, wrong-typed or out-of-range value returns `bad_params`. Nothing is sent in either case.
  - To apply the time cap during a wait, the runner passes `bridge.wait` a stop callable that combines the caller's `should_stop` with the deadline.
  - `steps_run` counts only actions that succeeded. A failed `shift_out` is not counted.
  - `scripts/run_skill.py` converts a `key=value` value to int only when the skill's default for that key is an int, so hex strings stay strings.
  - The HUMAN log check shows 11 new lines: 1 start-up re-sync from `Bridge.start`, then the skill's 10 frames.
  Files: `skills/store.py`, `skills/runner.py`, `scripts/run_skill.py`.

## Stage 6

- What: `POC/` was not read. The LLM client is built from config alone: `OpenAI(base_url=OPENAI_BASE_URL, api_key=OPENAI_API_KEY)`, Chat Completions with `tools=TOOL_SCHEMAS`, model `LLM_MODEL`. Findings (b) and (c) come from the real HUMAN runs, not from the POC.
  Why: user instruction for this session. It overrides the stage file's "Reference (read-only)" line and master section 9.
  Files: `agent/loop.py`.
- What: the loop logs one INFO line per model reply (`reply N: text=yes/no tool_calls=K`) through stdlib `logging`, and `agent/cli.py` turns INFO logging on (`httpx` request lines are silenced).
  Why: the loop's events cannot show turn boundaries, so this line is how the real runs answer (b) and (c).
  Files: `agent/loop.py`, `agent/cli.py`.
- What: the user runs `python -m agent.cli` against the real port and the LLM proxy. Claude Code does not open the serial port.
  Why: user instruction (same as stages 4 and 5).
  Files: none.
- What: interpretation choices not spelled out in the spec:
  - `call_tool` rejects unexpected argument keys as well as missing or wrong-typed ones (`bad_args: <detail>`). Ints exclude `bool`. `read_recent_frames` also rejects `count` outside 1..`MAX_HISTORY`.
  - `SkillError` becomes `error` with its message. Any other exception becomes `"<Type>: <message>"`, cut to 200 characters.
  - Tool-call arguments that are not a JSON object (invalid JSON, or valid JSON that is not an object) are not executed. `tool_call` is still emitted, with `args` set to the raw string, followed by a `tool_result` holding the `bad_args` result. That result is returned to the model.
  - `summary` for `stopped` and `max_turns` is the last text the model sent. For `completed` it is the final reply's text.
  - Creating the default client is inside the error handling, so a bad config returns `error` instead of raising.
  - The `shift_out` schema says that the byte count must be a multiple of `group_size`, because the firmware rejects other counts.
  Files: `agent/tools.py`, `agent/loop.py`.

## Stage 6 follow-up

- What: `shift_out`, `save_skill` and `reuse_skill` take an optional string argument `intent` (one short sentence: what the call is for). This adds an optional argument to three rows of the master 6.5 tool table.
  - The loop removes it (`agent/tools.py::split_intent`) before calling `call_tool`, so bridge, store and runner never see it. `call_tool` itself is unchanged, and an `intent` passed to any other tool still gets `bad_args`.
  - A non-empty `intent` is emitted as an `agent_message` event just before that call's `tool_call` event. The `tool_call` args do not include it. It does not change `summary`. A non-string or blank `intent` is dropped without an event.
  - Saved skills never contain `intent`: it is a top-level tool argument, not part of `definition` (and the store rejects unknown keys in `definition`).
  - The assistant message sent back to the model keeps the raw arguments, including `intent`.
  Why: the model sends text only in its final reply (stage 6 finding b), so the user could not see what each call was for.
  Files: `agent/tools.py`, `agent/loop.py`, `agent/prompts.py`.
- What: the system prompt gains two sections beyond the spec's ten: `Intent` and `Skill JSON` (after "Procedure for drawing"). `Skill JSON` lists the exact keys that `skills/store.py` accepts and shows one complete, valid example (`SKILL_EXAMPLE`, built from config: the top-left LED blinks 3 times using `repeat`, `shift_out` and `wait`). The `save_skill` description contains the same rules and example. The Animations section now tells the agent to use `repeat` instead of writing out repeated frames.
  Why: fixes for the stage 6 open issues (first `save_skill` rejected on shape, unrolled animation).
  Files: `agent/prompts.py`, `agent/tools.py`.
- What: "Picture to bytes" now says that a full frame is registers 01 to 08 in order, each exactly once. It shows the template `01[r0]02[r1]...08[r7]`, generated in code. The compare step in "Procedure for drawing" now says: if exactly one row differs, resend only that row; if several rows differ, rebuild and resend the full frame; do not call `save_skill` until all 8 rows match. Spec item 4 asks only for single-row resends.
  Why: in two HUMAN runs the agent sent register 04 twice (and 08 never, or an extra `0800`), which shifted the smiley. Its single-row "correction" fixed the wrong row, and it saved the malformed frame (`symbol_smiley` v1 and v2).
  Files: `agent/prompts.py`.

## Stage 7

- What: `POC/` was not read. The server, including how worker-thread events reach WebSocket clients, was written from the stage spec alone.
  Why: user instruction for this session. It overrides the stage file's "Reference (read-only)" line and master section 9.
  Files: `server/app.py`, `server/runs.py`.
- What: the user runs the server against the real board and the LLM proxy. Claude Code does not open the serial port.
  Why: user instruction (same as stages 4 to 6).
  Files: none.
- What: interpretation choices not spelled out in the spec:
  - `server/app.py` has a factory `create_app(port, state_file, frames_log, skills_dir, events_log, client, transport)`. `app = create_app()` uses the config values. Tests pass `port="fake"`, tmp paths and a scripted client, so they never open the real port or call the real LLM. All I/O happens in the lifespan, so importing the module opens nothing.
  - The lifespan calls `GridStore.load()` before `bridge.start()`. If the board is absent at startup, `start()` fails before its own `load()`, and a later `reconnect()` would re-sync a blank map. Loading first keeps the saved picture. Bridge code is unchanged.
  - `RunManager` does not know the bridge. Its `status` events carry `busy` and `run_id`, and the app adds `connected` before broadcasting.
  - Order: `emit` stamps `ts`, appends to the events file and queues the event under one lock. One sender task sends everything, so the file and every client see the same order.
  - The `status` sent to a new WebSocket client on connect goes only to that client. It is not an emitted event, so it is not in `EVENTS_LOG`.
  - `RECORD_EVENTS` is read at startup (lifespan). The events file is appended to, never truncated.
  - The 5 s health check skips while a run is busy. It broadcasts `status` when `connected` differs from the last value it broadcast.
  - The prompt is trimmed before the 500-character check. Check order: empty, too long, offline, busy.
  - A body without `prompt` counts as empty (400). Invalid JSON, or a `count` that is not an integer, gets FastAPI's 422.
  Files: `server/app.py`, `server/runs.py`, `tests/test_server.py`.

## Stage 8 Part A

- What: `POC/` was not read. The dashboard files were copied unchanged from the frontend developer's `static/` folder (not its `src/`, `public/`, `package.json` or Vite files), apart from the three edits below.
  Why: user instruction for this session.
  Files: `server/static/*`.
- What: `config.js` keeps `mock: null` and `apiBase: ""`, instead of the spec's `mock: false`. With `null`, `app.js` runs live unless the URL has `?mock=1`, so a plain `http://127.0.0.1:8000/` is live.
  Why: user instruction.
  Files: `server/static/config.js`.
- What: three frontend edits, made at the user's request. Two of them touch view code (`app.js`, `style.css`), which the stage says not to edit.
  - `api.js`: the `tool_result` preview uses `result.decoded_state`, falling back to `result.final_state` (`reuse_skill`) and then `result.state` (`read_shift_state`). Before this, only `shift_out` results had a thumbnail.
  - `app.js` + `style.css`: in live mode the whole simulation side panel (including the "Open mock mode" link) is hidden, and the root gets the class `is-live`. `.dashboard.is-live .workspace` uses a single column, so the main content takes the freed width. Mock mode is unchanged.
  - `style.css`: the agent trace is larger for a projector. `.agent-text` and `.tool-name` are 16px in `var(--ink)` (primary text colour), and `.tool-arguments` is 13px. The rules sit at the end of the file, so they override the media-query sizes too.
  Why: the demo is shown on a projector. The simulation panel does nothing in live mode, and `reuse_skill`/`read_shift_state` results had no thumbnail.
  Files: `server/static/api.js`, `server/static/app.js`, `server/static/style.css`.
- What: `api.js` needed no other change for the stage 7 interface. It passes the raw REST and event fields through, so nullable `intensity`, `warnings` and the skill `description` already work.
  Files: none.
- What: the stage 7 placeholder test became `test_dashboard_page`. It checks that `/` serves the dashboard's `index.html` (`id="led-dashboard"`, `./app.js`) and not the placeholder.
  Files: `tests/test_server.py`.

## Stage 8 Part B

- What: a failed reconnect now logs one WARNING line (`Reconnect failed: <error>`) instead of a full traceback. The traceback came from `Bridge.reconnect` (`logger.exception`), not from the server's health loop: `reconnect` catches the error itself. New test `test_reconnect_failure_logs_one_line`. The stage says not to change bridge code; the user allowed this one fix.
  Why: while the board was unplugged, the 5 s health check printed a full traceback every tick (stage 7 open issue).
  Files: `bridge/bridge.py`, `tests/test_bridge.py`.
- What: `scripts/library_prompts.txt` has one extra line beyond the spec list: `Create a skill named pattern_clear that turns every LED off.`
  Why: the dashboard suggests "Clear the display", and the agent-made `pattern_clear` from Part A was deleted with the other test skills, so it is made again by the agent.
  Files: `scripts/library_prompts.txt`.
- What: `scripts/build_library.py` waits for a run by polling `/api/status` (0.5 s), not over the WebSocket. It uses the standard library only. It prints the skills saved or changed (from `/api/skills`) and the final map, not the run's `summary`.
  Why: the spec allows polling, and a WebSocket client would need a package outside R8's list.
  Files: `scripts/build_library.py`.
- What: `scripts/demo_video_script.py` is a copy of `build_library.py` (only the docstring differs), for recording a demo video. Pass a prompts file as the argument.
  Why: user request.
  Files: `scripts/demo_video_script.py`.
- What: before the build, the user approved deleting `anim_test_blink`, `symbol_diamond` and the Part A `pattern_clear`. `anim_up_arrow_blink` was kept.
  Files: `skills/library/`.
```

### `docs/PROGRESS.md` — final status section

`docs/PROGRESS.md:309-343`

```markdown
## Stage 8 Part C — Runbook and README (2026-10-07)

Part C documents only. No code was changed.

Files created:
- `docs/RUNBOOK.md` (checklist, start commands, demo script, limits, failure playbook, reset)

Files changed:
- `README.md` (the stub is replaced: framing, architecture, setup, run, test, directory guide, "Deviations from the POC")
- `docs/PROGRESS.md`

Test results: `.venv/Scripts/python.exe -m pytest` with Python 3.11.9: 158 passed.

Demo script (8 prompts, chosen by the user):

| # | Prompt | Shows |
|---|---|---|
| 1 | Show a heart. | Reuse of `symbol_heart` |
| 2 | Play the heartbeat animation. | `anim_heartbeat` replayed by the runner |
| 3 | Say HI. | `word_hi` |
| 4 | Show the bouncing dot. | `anim_bounce` |
| 5 | Draw a star and save it as a skill named symbol_star. | Live composition, check against the map, correction, save |
| 6 | Show the star again. | Replay with zero LLM drawing steps |
| 7 | Count down 3, 2, 1. | Live composition from `digit_3`, `digit_2`, `digit_1` |
| 8 | What have you displayed in the last minute? | Reading history (`read_recent_frames`) |

The user asked for "checkerboard / bounce / fill" without picking one; the runbook uses `anim_bounce`.

Notes:
- Claude Code did not start the server, open the serial port or call the LLM. `POC/` was not read.
- All commands are PowerShell and call `.\.venv\Scripts\python.exe` directly. The server command is the one used in Part A (`uvicorn server.app:app --host 127.0.0.1 --port 8000`), run as `python -m uvicorn` through the venv. The proxy command (`uvx openai-api-server-via-codex`) comes from the stage 8 spec; earlier PROGRESS entries do not record it.
- The reset step restores the library by deleting `skills\library\*.json` and copying the backup, so a rehearsal's `symbol_star` is removed.

Open issues:
- The HUMAN rehearsal of the demo script, with timings, is not done yet (stage 8 "Done when").
```

---

## 12. Assumptions to confirm

1. **FALSE.** `run_agent` hard-codes `build_system_prompt()` (`agent/loop.py:37`), `tools=TOOL_SCHEMAS` (`agent/loop.py:54`) and `call_tool` (`agent/loop.py:91`); none is a parameter (`agent/loop.py:34-35`). Only monkeypatching module globals would avoid an edit [INFERRED].
2. **TRUE.** The only write is `ctx.skills.save(...)` in `_run` (`agent/tools.py:135-136`). Giving `AgentContext.skills` an object whose `save` holds the definition in memory and returns `{"name", "version"}` (or changing that branch in `agent/tools.py`) needs no loop change; the loop only reads `result["success"]`, `result["name"]`, `result["version"]` (`agent/loop.py:95-96`). Caveat: the loop would still emit an event named `skill_saved` for a held skill (`agent/loop.py:96`), and the same `skills` object also serves `list_skills`/`get_skill`/`reuse_skill` (`agent/tools.py:131-138`).
3. **TRUE.** `run_skill(definition: dict, bridge, params=None, should_stop=None, time_cap_s=...)` takes the dict directly and never reads the store (`skills/runner.py:31-43`); the dict must pass `validate` (`skills/runner.py:34-37`). Tests replay inline dicts (`tests/test_skills.py:146`).
4. **TRUE.** `scripts/run_skill.py:32,47` does `SkillStore(SKILLS_DIR).get(name)` then `run_skill(definition, bridge, params)` with no LLM; `skills/runner.py:1` "No LLM, no generated code."
5. **TRUE.** The only check is `server/app.py:32,135-136`. `run_agent` (`agent/loop.py:34-38`), `RunManager.start` (`server/runs.py:31-42`) and `agent/cli.py:41,60` have none; the dashboard input allows 2000 characters (`server/static/app.js:56`).
6. **PARTLY.** `seq` increments by one per committed frame (`bridge/grid_store.py:82-83`) and one run at a time is enforced (`server/runs.py:32-34`), so `current()["seq"]` before and after a run bounds the run's frames [INFERRED]. But no method returns frames after a `seq` [NOT FOUND]: `recent()` is capped at 30 and reads only the last 64 KiB (`bridge/grid_store.py:15,117,121-128`); frames carry no `run_id` (`bridge/grid_store.py:40-48`); the 5 s health check can re-sync (= write a frame) only when no run is busy (`server/app.py:77-80`), but a device reboot re-sync during a run is counted in the run's range (`bridge/bridge.py:57-60`). Reading the JSONL file or a listener (`bridge/grid_store.py:143`) is needed for >30.
7. **TRUE.** `skills/store.py:80` accepts a `$param` for `count` via `_check_int` (`skills/store.py:46-49`); `skills/runner.py:15-28,41` substitutes and range-checks it. Test `tests/test_skills.py:154-169`.
8. **PARTLY.** The stored file has `description` (optional, may be `""`), `params` (optional) and all `actions` including `repeat.count` (`skills/store.py:154-158`; e.g. `skills/library/anim_heartbeat.json`), available via `get_skill` (`agent/tools.py:133-134`). But the picture is only MAX7219 hex in `data_hex` and must be decoded; a `count` may be a `$param` whose real value is set at call time (`skills/runner.py:38`); `version` history is not kept; and `list_skills` gives only name/description/top-level step count/version/type, no params or steps (`skills/store.py:125-131`).
9. **PARTLY.** Any object with `chat.completions.create` works (`agent/loop.py:54`), and `RunManager` keeps the injected client in `self._client` (`server/runs.py:24,52`), so the same object could be reused for a single extra request [INFERRED]. But in production `create_app()` passes `client=None` (`server/app.py:43,165`), so `run_agent` builds its own `OpenAI(...)` per run inside the function (`agent/loop.py:45`) and never exposes it; the caller must create a client and inject it into both. `run_agent` passes no `tool_choice` (`agent/loop.py:54`), so a "one tool-call reply" request needs its own call code.
10. **TRUE.** `normalizeEvent` spreads every raw field into the event (`server/static/api.js:33`) and nothing in `store.js`/`trace.js` iterates over or rejects unknown fields (`server/static/store.js:29-69`, `server/static/trace.js:31-74`). Caveat: Python tests that compare whole event dicts would fail if `role` were added to those events (`tests/test_loop.py:65,78,112,166`; `tests/test_server.py:121`).

---

## 13. Surprises

- **No `.venv` in this copy.** `docs/PROGRESS.md:320` and `README.md:30-31,56,64` use `.\.venv\Scripts\python.exe` (and `README.md:27` still says "from `led_grid\`"), but `led_grid_agents/.venv` does not exist; `docs/DEVIATIONS.md:6` even names `led_grid/.venv`. Nothing can be run here until a venv is created.
- **The whole folder is untracked** in the parent git repo (`git status` → `?? led_grid_agents/`). Also, `logs/` is gitignored (`.gitignore:2`) but this copy carries the original `logs/` (293 frames, 784 events), including `logs/shift_state.json` with `seq` state from `led_grid` — a new server here continues that `seq` (`bridge/grid_store.py:50-66`).
- **Loop is closed to extension.** System prompt, tool schemas and dispatch are module-level imports inside `run_agent` (`agent/loop.py:10-11,37,54,91`); a second agent with a different role/tool set needs `run_agent` to grow parameters or a second loop.
- **`skill_saved` is keyed on the tool name.** `agent/loop.py:95-96` emits `skill_saved` whenever a tool named `save_skill` succeeds; an intercepted/held save would still be announced as saved and the dashboard would show "Saved skill …" (`server/static/trace.js:58-62`, `server/static/store.js:58-59`).
- **`split_intent` is called by the loop, not by `call_tool`** (`agent/loop.py:82`), and `INTENT_TOOLS` is a fixed tuple (`agent/tools.py:15`). Any new tool that should take `intent` must be added there, and `call_tool` used without the loop rejects `intent` as `bad_args`.
- **Single global run.** `RunManager` allows one run, has one stop `Event`, one counter, and `busy`/`run_id` (`server/runs.py:26-29,32-34`). Run ids restart at `r_0001` on every server start (`server/runs.py:27,36`); `logs/sample_events.jsonl` has 31 `run_finished` events, many with the same `r_0001` — ids are not unique across restarts.
- **`shift_state` events carry no `run_id`** (`server/app.py:101`), and frames in the log carry no run or agent identity (`bridge/grid_store.py:40-48`). Attributing frames to an agent/round must be done by `seq` ranges or ordering.
- **History is doubly capped.** `GridStore.recent` clamps to 30 and reads only the last 64 KiB of the JSONL (`bridge/grid_store.py:15,117,121-128`); the dashboard also hard-codes 30 (`server/static/api.js:111`, `server/static/config.js:10`). There is no "since seq" query.
- **`list_skills` `steps` counts top-level actions only** (`skills/store.py:102-104`): `anim_heartbeat` (12 frames+waits played) lists as `steps: 1` today because it is one `repeat`; an older v1 listed `steps: 12` (`logs/sample_events.jsonl:4`). A reader/auditor cannot judge size from `list_skills`.
- **Param defaults are not type-checked at save time** (`skills/store.py:46-49`): `{"params": {"n": "x"}}` with `count: "$n"` saves fine and fails only at replay with `bad_params` (`skills/runner.py:41-43`). No library skill uses params at all, so this path is only exercised by tests.
- **`steps_run` counts waits, including an interrupted one** (`skills/runner.py:67-69`), but not `repeat`; a failed `shift_out` is not counted (`skills/runner.py:63-66`). A stopped or timed-out replay still returns `success: True` (`skills/runner.py:80-81`).
- **Stop does not interrupt the LLM request** (`agent/loop.py:51-54`); with a slow proxy a Stop can take one full model round-trip to take effect.
- **Tool results grow the context.** Every `shift_out` result includes the full `decoded_state` and is sent back to the model as JSON (`agent/loop.py:97`, `bridge/bridge.py:74`); `reuse_skill` adds `final_state`. A second agent reading long transcripts will pay for this.
- **Dead code:** `GridStore.mark_unknown` (`bridge/grid_store.py:75-80`) is never called in code or tests. `TOP_LEVEL_KEYS` accepts `version` in input but `save` overwrites it (`skills/store.py:12,153-154`).
- **Hard-coded values outside `config.py`:** `MAX_PROMPT_CHARS = 500`, `HEALTH_INTERVAL_S = 5.0` (`server/app.py:31-32`); `MAX_ERROR_LEN = 200` in three files (`agent/loop.py:14`, `agent/tools.py:13`, `server/runs.py:10`); `MAX_DESCRIPTION = 200`, `MAX_REPEAT_COUNT = 1000`, `MAX_REPEAT_DEPTH = 3` (`skills/store.py:19-21`); `WORD_FRAME_WAIT_MS = 600` (`agent/prompts.py:8`); `FakeDevice` allowed pins `(25, 26, 27)` and `MAX_BYTES = 64` (`bridge/fake_device.py:6,12`); `Bridge.resync` group size `2` (`bridge/bridge.py:88`); repeat limits typed again in prose (`agent/prompts.py:37`).
- **Duplicated test scaffolding with drift.** Two different `ScriptedClient` classes (`tests/test_loop.py:32-45` records requests and can raise; `tests/test_server.py:31-41` cannot), duplicated `ctx` fixtures and helpers, no `conftest.py`. A two-agent test harness will want these shared.
- **Brittle exact-dict tests.** Several tests compare whole event/result dicts (`tests/test_loop.py:64-65,78-79,112,166`; `tests/test_server.py:121`; `tests/test_tools.py:104,142,170-171`), so any added field (e.g. `role`) breaks them even when behaviour is fine.
- **Test heart ≠ library heart.** Tests use `HEART_HEX = "0100026603FF04FF057E063C07180800"` (`tests/test_loop.py:13`), the library's `symbol_heart` uses `016602FF03FF047E053C061807000800` (`skills/library/symbol_heart.json:13`) — one row higher. Harmless, but a planner comparing frames should not assume they match.
- **Docs drift.** `docs/DEVIATIONS.md:45` lists `skills/library/anim_test_blink.json`, which no longer exists (deleted per `docs/DEVIATIONS.md:146`). `config.js` keeps `mock: null` instead of the spec's `false` (`docs/DEVIATIONS.md:118`). The dashboard input allows 2000 characters while the server rejects >500 (`server/static/app.js:56`, `server/app.py:32`), and the UI maps a 400 to the "empty" message (`server/static/api.js:63`, `server/static/store.js:104`), so a too-long prompt is reported as "Type a prompt first".
- **`library_backup` is only a convention.** No code references `skills/library_backup/`; it is mentioned in `README.md:87` and restored by hand per the runbook (`docs/PROGRESS.md:340`).
- **Ordering quirk.** The busy `status` is emitted from the HTTP thread before the worker emits `run_started` (`server/runs.py:40-41,48`), so clients always see `status` first (`tests/test_server.py:113`).
