"""FastAPI app: owns the serial port, runs one agent run at a time, REST + WebSocket (master 6.7).

Run with: uvicorn server.app:app --host 127.0.0.1 --port 8000   (one worker, no --reload)
"""

import asyncio
import contextlib
import json
import logging
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent.tools import AgentContext
from bridge.bridge import Bridge
from bridge.grid_store import GridStore
from bridge.transport import open_transport
from config import EVENTS_LOG, FRAMES_LOG, MAX_HISTORY, MSB_IS_LEFT, SERIAL_PORT, SKILLS_DIR, STATE_FILE
from server.runs import RunBusy, RunManager
from skills.store import SkillStore

STATIC_DIR = Path(__file__).resolve().parent / "static"
HEALTH_INTERVAL_S = 5.0
MAX_PROMPT_CHARS = 500

logger = logging.getLogger(__name__)


class PromptBody(BaseModel):
    prompt: str = ""


def create_app(port: str | None = SERIAL_PORT, state_file: Path = STATE_FILE, frames_log: Path = FRAMES_LOG,
               skills_dir: Path = SKILLS_DIR, events_log: Path = EVENTS_LOG,
               client=None, transport=None) -> FastAPI:
    s = SimpleNamespace(loop=None, queue=None, clients=set(), record=False, emit_lock=threading.Lock())

    def emit(event: dict) -> None:
        with s.emit_lock:
            event = {**event, "ts": time.time()}
            if s.record:
                with Path(events_log).open("a", encoding="utf-8") as f:
                    f.write(json.dumps(event) + "\n")
            if s.loop is not None:
                with contextlib.suppress(RuntimeError):
                    s.loop.call_soon_threadsafe(s.queue.put_nowait, (None, event))

    def status_event() -> dict:
        return {"type": "status", "connected": s.bridge.connected, "busy": s.runs.busy, "run_id": s.runs.run_id}

    def run_emit(event: dict) -> None:
        if event["type"] == "status":
            event = {**event, "connected": s.bridge.connected}
        emit(event)

    async def sender() -> None:
        while True:
            target, event = await s.queue.get()
            for ws in [target] if target is not None else list(s.clients):
                try:
                    await ws.send_json(event)
                except Exception:
                    s.clients.discard(ws)

    async def health() -> None:
        last = s.bridge.connected
        while True:
            await asyncio.sleep(HEALTH_INTERVAL_S)
            if s.runs.busy:
                continue
            try:
                await asyncio.to_thread(s.bridge.ping if s.bridge.connected else s.bridge.reconnect)
            except Exception:
                logger.exception("Health check failed")
            if s.bridge.connected != last:
                last = s.bridge.connected
                emit(status_event())

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        s.loop = asyncio.get_running_loop()
        s.queue = asyncio.Queue()
        s.record = os.getenv("RECORD_EVENTS") == "1"
        s.grid_store = GridStore(state_file, frames_log, MSB_IS_LEFT)
        s.bridge = Bridge(transport if transport is not None else open_transport(port), s.grid_store)
        s.skills = SkillStore(skills_dir)
        s.runs = RunManager(lambda should_stop: AgentContext(s.bridge, s.skills, should_stop), run_emit, client)
        s.grid_store.load()
        try:
            s.bridge.start()
        except Exception:
            logger.exception("Device not connected at startup")
        s.grid_store.add_listener(lambda led_map: emit({"type": "shift_state", "state": led_map}))
        tasks = [asyncio.create_task(sender()), asyncio.create_task(health())]
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            s.runs.stop()
            s.bridge.close()

    app = FastAPI(lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST"], allow_headers=["*"])

    @app.get("/api/status")
    def get_status() -> dict:
        return {"connected": s.bridge.connected, "busy": s.runs.busy, "run_id": s.runs.run_id}

    @app.get("/api/shift_state")
    def get_shift_state() -> dict:
        return s.grid_store.current()

    @app.get("/api/frames")
    def get_frames(count: int = MAX_HISTORY) -> dict:
        return {"frames": s.grid_store.recent(max(1, min(count, MAX_HISTORY)))}

    @app.get("/api/skills")
    def get_skills() -> dict:
        return {"skills": s.skills.list()}

    @app.post("/api/prompt")
    def post_prompt(body: PromptBody) -> JSONResponse:
        prompt = body.prompt.strip()
        if not prompt:
            return JSONResponse({"error": "empty_prompt"}, status_code=400)
        if len(prompt) > MAX_PROMPT_CHARS:
            return JSONResponse({"error": "prompt_too_long"}, status_code=400)
        if not s.bridge.connected:
            return JSONResponse({"error": "device_offline"}, status_code=503)
        try:
            run_id = s.runs.start(prompt)
        except RunBusy:
            return JSONResponse({"error": "run_in_progress"}, status_code=409)
        return JSONResponse({"run_id": run_id}, status_code=202)

    @app.post("/api/stop")
    def post_stop() -> dict:
        s.runs.stop()
        return {"stopped": True}

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        await ws.accept()
        s.clients.add(ws)
        s.queue.put_nowait((ws, {**status_event(), "ts": time.time()}))
        try:
            while (await ws.receive())["type"] != "websocket.disconnect":
                pass
        finally:
            s.clients.discard(ws)

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


app = create_app()
