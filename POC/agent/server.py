"""
Layer: Agent
Component: Backend server -- bridges the real hardware agent (agent_loop.py's
LLM-driven discovery loop) to the dashboard frontend in ../liquid-gas/frontend,
per BACKEND_INTEGRATION_CONTRACT.md and BACKEND_IMPLEMENTATION_TASK.md.

Replaces liquid-gas/app.py's scripted simulator: instead of emitting canned
events on a timer, this drives the exact same WebSocket event shapes from
real tool calls against real hardware, via the on_event callback added to
agent_loop.run_discovery().

Run with: python agent/server.py  (serves on http://0.0.0.0:8000)
"""

import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path

_AGENT_DIR = Path(__file__).resolve().parent
_ROOT_DIR = _AGENT_DIR.parent
_BRIDGE_DIR = _ROOT_DIR / "bridge"
_SKILLS_DIR = _ROOT_DIR / "skills"
_FRONTEND_DIR = _ROOT_DIR / "liquid-gas" / "frontend"

sys.path.insert(0, str(_AGENT_DIR))
sys.path.insert(0, str(_BRIDGE_DIR))

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from openai import OpenAI
from pydantic import BaseModel

import agent_loop
import briefing_task
import composition_task
import registry
import serial_transport
import tool_declarations

load_dotenv()

app = FastAPI(title="Hardware Agent Mind-Map Dashboard -- real backend")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class GoalRequest(BaseModel):
    prompt: str = agent_loop.GOAL
    stage: str = "full_demo"  # accepted for parity, ignored -- contract §2


class BriefingRequest(BaseModel):
    prompt: str = "What can you see?"


class ComposeRequest(BaseModel):
    prompt: str = "Turn the LED on."


# ---------------------------------------------------------------------------
# WebSocket connection management
# ---------------------------------------------------------------------------

class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, message: dict) -> None:
        dead = []
        for ws in self.active:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


manager = ConnectionManager()
_run_in_progress = False


# ---------------------------------------------------------------------------
# Skill schema translation (BACKEND_IMPLEMENTATION_TASK.md §1) -- the
# internal skill schema written by bridge/tool_functions.save_skill() uses
# different field names than the frontend's skill_saved/initial_state
# events expect. The internal schema is never renamed (skill_runner.py
# depends on it) -- translation happens here, at the wire edge, instead.
# ---------------------------------------------------------------------------

def skill_to_wire_format(skill: dict) -> dict:
    """Translate the internal light_policy skill schema to the frontend's expected shape."""
    return {
        "name": skill["name"],
        "version": f"v{skill['version']}",  # frontend expects a STRING like "v1", not an int
        "skill_type": "light_policy",
        "target_sensor_value": skill["target"],
        "tolerance": skill.get("tolerance"),
        "discovered_duty": skill["brightness"],
        "iterations_taken": skill["iterations"],
        "final_error": skill["final_error"],
        "sensor_pin": skill.get("sensor_pin", agent_loop.SENSOR_PIN),
        "actuator_pin": skill.get("actuator_pin", agent_loop.LED_PIN),
        "timestamp": skill.get("timestamp", ""),
        "environmental_note": skill.get("environmental_note"),
    }


def action_sequence_to_wire_format(skill: dict) -> dict:
    """
    Translate an internal action_sequence skill (blink_twice, led_on, ...)
    to a Branch D card shape. There's no existing frontend contract for this
    -- action_sequence skills were never shown in the skill library before --
    so this is a new, minimal shape (name/version/step_count/tools_used)
    rather than forcing light_policy's target/duty/error fields onto
    something that has none of those concepts.
    """
    actions = skill.get("actions") or []
    tools_used = sorted({a["tool"] for a in actions if isinstance(a, dict) and "tool" in a})
    return {
        "name": skill["name"],
        "version": f"v{skill['version']}",
        "skill_type": "action_sequence",
        "step_count": len(actions),
        "tools_used": tools_used,
        "timestamp": skill.get("timestamp", ""),
    }


def _skill_to_wire_format_any(skill: dict) -> dict:
    """Dispatches to the right translator by the skill's own 'type' field."""
    if skill.get("type") == "action_sequence":
        return action_sequence_to_wire_format(skill)
    return skill_to_wire_format(skill)


# save_skill() always (over)writes the canonical <name>.json that
# skill_runner.py reads as "the current policy" AND that tool_functions.
# list_skills()/get_skill() scan for the LLM's own use -- that directory's
# contents must stay exactly what save_skill() itself writes, nothing more.
# For the dashboard's version history (Branch D shows v1 *and* v2 as
# separate cards), a versioned snapshot is archived instead into this
# separate subdirectory -- NOT _SKILLS_DIR itself. It used to be written
# alongside the canonical files, which meant the model's own list_skills()
# calls returned confusing noise like "blink_twice_v1" as if it were a
# second, different skill from "blink_twice" -- directly undermining Stage
# 4's "inspect what you actually saved" step. Kept as a subdirectory (not a
# sibling naming scheme) so the fix requires no change to tool_functions.py:
# os.listdir(_SKILLS_DIR)'s existing ".json"-suffix filter already skips
# subdirectories, so the archive is invisible to the agent for free.
_SKILLS_ARCHIVE_DIR = _SKILLS_DIR / "_versions"
_VERSIONED_SKILL_RE = re.compile(r"^(?P<name>.+)_v(?P<version>\d+)\.json$")


def _archive_skill_version(name: str, definition: dict) -> dict:
    """
    Re-reads the just-written canonical skill file (so the archive picks up
    the sensor_pin/actuator_pin/timestamp defaults save_skill() fills in),
    writes a versioned copy under _SKILLS_ARCHIVE_DIR, and returns the
    enriched dict.
    """
    canonical_path = _SKILLS_DIR / f"{name}.json"
    try:
        with open(canonical_path) as f:
            enriched = json.load(f)
    except (OSError, json.JSONDecodeError):
        enriched = {"name": name, **definition}

    version = enriched.get("version", definition.get("version"))
    _SKILLS_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    versioned_path = _SKILLS_ARCHIVE_DIR / f"{name}_v{version}.json"
    with open(versioned_path, "w") as f:
        json.dump(enriched, f, indent=2)
    return enriched


def _run_config() -> dict:
    """
    The static, run-independent facts the dashboard needs to render itself
    without hardcoding anything (target, tolerance, pin numbers, PWM range,
    default goal text) -- sourced from agent_loop's own constants so this
    can never drift out of sync with what the agent loop actually enforces.
    Sent once, inside initial_state, on every WebSocket connect.
    """
    return {
        "target": agent_loop.TARGET,
        "tolerance": agent_loop.TOLERANCE,
        "max_iterations": agent_loop.MAX_ITERATIONS,
        "sensor_pin": agent_loop.SENSOR_PIN,
        "actuator_pin": agent_loop.LED_PIN,
        "pwm_max": agent_loop.PWM_MAX,
        "default_goal": agent_loop.GOAL,
        # Sourced straight from tool_declarations.TOOL_DECLARATIONS -- never
        # duplicated as a separate frontend literal -- so the hero-briefing
        # table always reflects the model's real, current tool surface.
        "tools": [
            {"name": t["function"]["name"], "description": t["function"]["description"]}
            for t in tool_declarations.TOOL_DECLARATIONS
        ],
    }


def _load_saved_skills() -> list:
    """Reads every archived versioned skill (either type), translated to wire format."""
    skills = []
    if not _SKILLS_ARCHIVE_DIR.exists():
        return skills
    for path in sorted(_SKILLS_ARCHIVE_DIR.iterdir()):
        if not _VERSIONED_SKILL_RE.match(path.name):
            continue
        try:
            with open(path) as f:
                data = json.load(f)
            skills.append(_skill_to_wire_format_any(data))
        except (OSError, json.JSONDecodeError, KeyError):
            continue
    return skills


# ---------------------------------------------------------------------------
# Shared push_event factory -- every execute_* function below (discovery,
# briefing, composition) needs the same two things: broadcast every event
# generically, and additionally archive + emit skill_saved whenever a
# save_skill tool call succeeds, regardless of which skill type it was.
# Shared here instead of duplicated per function so Branch D shows skills
# saved from any of the three flows, not just agent_loop's.
# ---------------------------------------------------------------------------

def _make_push_event(loop):
    # tool_call fires before the matching tool_result, so the save_skill
    # call's args are stashed here to be read back once its result arrives.
    pending_save_args: dict = {}

    def push_event(event_type: str, payload: dict) -> None:
        envelope = {"type": event_type, "timestamp": time.time(), "payload": payload}
        asyncio.run_coroutine_threadsafe(manager.broadcast(envelope), loop)

        if event_type == "tool_call" and payload.get("tool") == "save_skill":
            pending_save_args["args"] = payload.get("args")

        # A successful save_skill tool call -- of either skill type -- also
        # archives a versioned snapshot and emits the dashboard's skill_saved
        # event (contract §4.6), translated by _skill_to_wire_format_any.
        if event_type == "tool_result" and payload.get("tool") == "save_skill":
            result = payload.get("result") or {}
            if result.get("success"):
                args = pending_save_args.get("args") or {}
                name = args.get("name")
                definition = args.get("definition") or {}
                if not name:
                    return
                enriched = _archive_skill_version(name, definition)
                try:
                    wire_skill = _skill_to_wire_format_any(enriched)
                except KeyError:
                    return
                skill_envelope = {
                    "type": "skill_saved",
                    "timestamp": time.time(),
                    "payload": {"skill": wire_skill},
                }
                asyncio.run_coroutine_threadsafe(manager.broadcast(skill_envelope), loop)

    return push_event


# ---------------------------------------------------------------------------
# Running the real discovery loop and mirroring it onto the WebSocket
# ---------------------------------------------------------------------------

async def execute_run(prompt: str) -> None:
    global _run_in_progress
    loop = asyncio.get_running_loop()
    push_event = _make_push_event(loop)

    await manager.broadcast({
        "type": "status",
        "timestamp": time.time(),
        "payload": {"state": "THINKING", "message": f'Analyzing prompt: "{prompt}"', "stage": "full_demo"},
    })

    try:
        await asyncio.to_thread(
            agent_loop.run_discovery,
            _openai_client,
            "gpt-5.5",
            prompt,
            push_event,
        )
    except Exception as exc:
        print(f"[server] run failed: {exc}")
        await manager.broadcast({
            "type": "status",
            "timestamp": time.time(),
            "payload": {"state": "ERROR", "message": str(exc), "stage": "full_demo"},
        })
    finally:
        # Non-negotiable safety net (contract §6.4): the Run button only
        # re-enables on a CONVERGED status, so this must always fire, even
        # after an exception above.
        await manager.broadcast({
            "type": "status",
            "timestamp": time.time(),
            "payload": {"state": "CONVERGED", "message": "Run finished.", "stage": "full_demo"},
        })
        _run_in_progress = False


# ---------------------------------------------------------------------------
# Running a briefing (informational-question) request -- deliberately not
# execute_run/agent_loop.run_discovery, see briefing_task.py's docstring for
# why. Reuses the same generic WebSocket broadcast shape (status/tool_call/
# tool_result/agent_message) so liquid-gas/frontend's existing event
# handling works unchanged for this path.
# ---------------------------------------------------------------------------

async def execute_briefing(prompt: str) -> None:
    global _run_in_progress
    loop = asyncio.get_running_loop()
    push_event = _make_push_event(loop)

    await manager.broadcast({
        "type": "status",
        "timestamp": time.time(),
        "payload": {"state": "THINKING", "message": f'Analyzing prompt: "{prompt}"', "stage": "briefing"},
    })

    try:
        await asyncio.to_thread(briefing_task.run_briefing, prompt, push_event)
    except Exception as exc:
        print(f"[server] briefing failed: {exc}")
        await manager.broadcast({
            "type": "status",
            "timestamp": time.time(),
            "payload": {"state": "ERROR", "message": str(exc), "stage": "briefing"},
        })
    finally:
        # Same non-negotiable safety net as execute_run's finally block --
        # the Run/Ask buttons only re-enable on a terminal status.
        await manager.broadcast({
            "type": "status",
            "timestamp": time.time(),
            "payload": {"state": "CONVERGED", "message": "Briefing finished.", "stage": "briefing"},
        })
        _run_in_progress = False


# ---------------------------------------------------------------------------
# Running a composition/generalization request (Stages 2-4 of
# mid_evaluation_demo_plan_revised.md -- "turn the LED on", "blink twice and
# save it", "generalize to blink_n_times") -- composition_task.py, not
# agent_loop.run_discovery, for the same reason briefing_task isn't either:
# a different system prompt, no TARGET/TOLERANCE framing. This was CLI-only
# until now; wiring it up here is what makes it reachable from the
# dashboard's prompt box instead of only `python agent/composition_task.py`.
# ---------------------------------------------------------------------------

async def execute_compose(prompt: str) -> None:
    global _run_in_progress
    loop = asyncio.get_running_loop()
    push_event = _make_push_event(loop)

    await manager.broadcast({
        "type": "status",
        "timestamp": time.time(),
        "payload": {"state": "THINKING", "message": f'Analyzing prompt: "{prompt}"', "stage": "composition"},
    })

    try:
        await asyncio.to_thread(composition_task.run_composition_task, prompt, push_event)
    except Exception as exc:
        print(f"[server] composition run failed: {exc}")
        await manager.broadcast({
            "type": "status",
            "timestamp": time.time(),
            "payload": {"state": "ERROR", "message": str(exc), "stage": "composition"},
        })
    finally:
        await manager.broadcast({
            "type": "status",
            "timestamp": time.time(),
            "payload": {"state": "CONVERGED", "message": "Composition run finished.", "stage": "composition"},
        })
        _run_in_progress = False


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.post("/api/run_goal")
async def run_goal(req: GoalRequest):
    global _run_in_progress
    if _run_in_progress:
        raise HTTPException(status_code=409, detail="A run is already in progress")
    _run_in_progress = True
    asyncio.create_task(execute_run(req.prompt))
    return {"status": "STARTED", "prompt": req.prompt, "stage": req.stage}


@app.post("/api/briefing")
async def briefing(req: BriefingRequest):
    global _run_in_progress
    if _run_in_progress:
        raise HTTPException(status_code=409, detail="A run is already in progress")
    _run_in_progress = True
    asyncio.create_task(execute_briefing(req.prompt))
    return {"status": "STARTED", "prompt": req.prompt}


@app.post("/api/compose")
async def compose(req: ComposeRequest):
    global _run_in_progress
    if _run_in_progress:
        raise HTTPException(status_code=409, detail="A run is already in progress")
    _run_in_progress = True
    asyncio.create_task(execute_compose(req.prompt))
    return {"status": "STARTED", "prompt": req.prompt}


@app.get("/api/skills")
async def get_skills():
    return {"skills": _load_saved_skills()}


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        await websocket.send_json({
            "type": "initial_state",
            "payload": {"skills": _load_saved_skills(), "config": _run_config()},
        })
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


if _FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_FRONTEND_DIR)), name="static")


@app.get("/")
async def index():
    index_path = _FRONTEND_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return HTMLResponse(f"<h1>Backend running -- frontend not found at {index_path}</h1>")


# ---------------------------------------------------------------------------
# Startup: open the one serial connection and the one OpenAI client for the
# lifetime of the server, same as agent_loop.main()'s standalone CLI path.
# ---------------------------------------------------------------------------

_openai_client: OpenAI = None  # set in on_startup

# ---------------------------------------------------------------------------
# Background sensor polling -- keeps Branch A's chart continuously live at
# all times, including *during* an active discovery/composition run, not
# just between runs. (Previously paused while _run_in_progress was true --
# but a run's own sensor readings only arrive once per LLM round trip, which
# takes several seconds of "thinking" with zero events in between, so the
# chart looked frozen for stretches during every run. Removed the pause: it
# was only ever about avoiding redundant reads, never about correctness --
# serial_transport's lock, see bridge/serial_transport.py, is what actually
# guarantees the poller and a run's own tool calls can safely interleave on
# the wire without corrupting either one's response.)
# ---------------------------------------------------------------------------

_SENSOR_POLL_INTERVAL_S = 1.0
_sensor_poll_task = None  # keeps the background task referenced so it isn't GC'd


async def _sensor_poll_loop():
    while True:
        await asyncio.sleep(_SENSOR_POLL_INTERVAL_S)
        try:
            sensor_result = await asyncio.to_thread(registry.call_tool, "read_analog", {"pin": agent_loop.SENSOR_PIN})
            # Real hardware readback (firmware's own ledcRead), not Python-side
            # memory of the last set_pwm call -- stays correct across bridge/
            # dashboard restarts, which don't reset the ESP32's PWM state.
            pwm_result = await asyncio.to_thread(registry.call_tool, "read_pwm", {"pin": agent_loop.LED_PIN})
        except Exception as exc:
            print(f"[server] background sensor poll failed: {exc}")
            continue
        if not sensor_result.get("success"):
            continue
        value = sensor_result.get("value")
        duty = pwm_result.get("value", 0) if pwm_result.get("success") else 0
        await manager.broadcast({
            "type": "sensor_reading",
            "timestamp": time.time(),
            "payload": {
                "iteration": None,
                "value": value,
                "target": agent_loop.TARGET,
                "tolerance": agent_loop.TOLERANCE,
                "duty": duty,
                "error": agent_loop.TARGET - value if value is not None else None,
                "stage": "live monitor",
            },
        })


@app.on_event("startup")
async def on_startup():
    global _openai_client, _sensor_poll_task
    port = os.environ.get("SERIAL_PORT")
    if not port:
        raise RuntimeError("Set SERIAL_PORT in your .env (e.g. SERIAL_PORT=COM5) before running the server.")
    serial_transport.connect(port)
    _openai_client = OpenAI(
        base_url=os.environ["OPENAI_BASE_URL"],
        api_key=os.environ["OPENAI_API_KEY"],
    )
    os.makedirs(_SKILLS_DIR, exist_ok=True)
    print(f"[server] frontend dir: {_FRONTEND_DIR} (exists={_FRONTEND_DIR.exists()})")
    _sensor_poll_task = asyncio.create_task(_sensor_poll_loop())


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
