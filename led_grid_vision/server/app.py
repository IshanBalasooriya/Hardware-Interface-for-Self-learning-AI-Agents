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

from fastapi import FastAPI, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent.tools import AgentContext
from bridge.bridge import Bridge
from bridge.grid_store import GridStore
from bridge.transport import open_transport
import config
from config import (CLOCK_PIN, DATA_PIN, EVENTS_LOG, FRAMES_LOG, GROUP_SIZE, LATCH_PIN, MAX_HISTORY, MSB_IS_LEFT,
                    SERIAL_PORT, SKILLS_DIR, STATE_FILE, WAKE_HEX)
from server.runs import RunBusy, RunManager
from skills.store import SkillStore
from vision.ledmap import rows_to_hex
from vision.patterns import all_on

STATIC_DIR = Path(__file__).resolve().parent / "static"
HEALTH_INTERVAL_S = 5.0
MAX_PROMPT_CHARS = 500
METRICS_EVENTS_PER_S = 4.0  # vision_metrics WebSocket events, at most
MJPEG_POLL_S = 0.04
MJPEG_BOUNDARY = "frame"

logger = logging.getLogger(__name__)


class PromptBody(BaseModel):
    prompt: str = ""


class LockBody(BaseModel):
    action: str
    x: float | None = None
    y: float | None = None


class LightBody(BaseModel):
    on: bool


class Throttle:
    """True at most `per_second` times per second."""

    def __init__(self, per_second: float, clock=time.monotonic) -> None:
        self.interval = 1.0 / per_second
        self.clock = clock
        self._last: float | None = None

    def ready(self) -> bool:
        now = self.clock()
        if self._last is None or now - self._last >= self.interval:
            self._last = now
            return True
        return False


def create_app(port: str | None = SERIAL_PORT, state_file: Path = STATE_FILE, frames_log: Path = FRAMES_LOG,
               skills_dir: Path = SKILLS_DIR, events_log: Path = EVENTS_LOG,
               client=None, transport=None, vision=None) -> FastAPI:
    """vision: a VisionService to use (tests); None builds one when config.VISION_ENABLED == "1" (stage 5)."""
    s = SimpleNamespace(loop=None, queue=None, clients=set(), record=False, emit_lock=threading.Lock(),
                        vision=None, light_restore=None, closing=False, metrics_throttle=Throttle(METRICS_EVENTS_PER_S))

    def emit(event: dict) -> None:
        with s.emit_lock:
            event = {**event, "ts": time.time()}
            if s.record and event["type"] != "vision_metrics":
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
            if s.vision is not None:  # runs and vision tasks pause the preview loop
                s.vision.set_operation(s.runs.operation if event["busy"] else None)
        emit(event)

    def on_observation(observation: dict) -> None:
        emit({"type": "observed_state", "state": observation["observed_state"],
              "physical_check": observation["physical_check"], "run_id": s.runs.run_id})

    def on_metrics(metrics: dict) -> None:
        if s.metrics_throttle.ready():
            emit({"type": "vision_metrics", "metrics": metrics})

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
        s.runs = RunManager(lambda should_stop: AgentContext(s.bridge, s.skills, should_stop, vision=s.vision),
                            run_emit, client)
        s.grid_store.load()
        try:
            s.bridge.start()
        except Exception:
            logger.exception("Device not connected at startup")
        s.grid_store.add_listener(lambda led_map: emit({"type": "shift_state", "state": led_map}))
        s.vision = vision
        if s.vision is None and config.VISION_ENABLED == "1":
            from vision_service import VisionService

            s.vision = VisionService(True, calibration_path=config.CALIBRATION_FILE, debug_dir=config.VISION_DEBUG_DIR)
        if s.vision is not None:
            s.vision.add_listener("observation", on_observation)
            s.vision.add_listener("metrics", on_metrics)
            await asyncio.to_thread(s.vision.start)  # a camera failure is logged in one line; the server runs on
        tasks = [asyncio.create_task(sender()), asyncio.create_task(health())]
        try:
            yield
        finally:
            s.closing = True
            for task in tasks:
                task.cancel()
            s.runs.stop()
            if s.vision is not None:
                s.vision.stop()
            s.bridge.close()

    app = FastAPI(lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST"], allow_headers=["*"])

    @app.get("/api/status")
    def get_status() -> dict:
        status = {"connected": s.bridge.connected, "busy": s.runs.busy, "run_id": s.runs.run_id}
        if s.vision is not None:  # without vision the reply is exactly led_grid's
            v = s.vision.status()
            status["vision"] = {k: v[k] for k in ("enabled", "camera", "calibrated", "position_ok")}
        return status

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

    # ---------------------------------------------------------------- vision (stage 5, Part C)

    def no_vision() -> JSONResponse | None:
        return JSONResponse({"error": "vision_disabled"}, status_code=404) if s.vision is None else None

    def busy() -> JSONResponse:
        return JSONResponse({"error": "run_in_progress"}, status_code=409)

    def send_rows(rows: list[str]) -> None:
        """Calibration and position-check frames: normal confirmed commands through the Bridge."""
        result = s.bridge.shift_out(DATA_PIN, CLOCK_PIN, LATCH_PIN, GROUP_SIZE, rows_to_hex(rows))
        if not result["success"]:
            raise RuntimeError(f"shift_out failed: {result['error']}")

    def restore(snapshot: bytes) -> None:
        """Re-send a saved picture (its full register state, as Bridge.resync does)."""
        s.bridge.shift_out(DATA_PIN, CLOCK_PIN, LATCH_PIN, GROUP_SIZE, snapshot.hex())

    def vision_status() -> dict:
        return {**s.vision.status(), "light": s.light_restore is not None}

    def vision_task(name: str, work, announce: bool = True):
        """Run `work` in the run slot (exclusive with agent runs); emit vision_status around it."""
        def task():
            if announce:
                emit({"type": "vision_status", "operation": name, "phase": "started", "status": vision_status()})
            result = work()
            if announce:
                emit({"type": "vision_status", "operation": name, "phase": "finished", "status": vision_status(),
                      "result": result})
            return result
        if not s.bridge.connected:
            return JSONResponse({"error": "device_offline"}, status_code=503)
        try:
            return s.runs.run_task(name, task)
        except RunBusy:
            return busy()

    def with_restore(fn):
        """Run fn(show), then put back the picture shown before (the pre-light picture if the light was on)."""
        def work():
            snapshot = s.light_restore if s.light_restore is not None else s.grid_store.resync_bytes()
            try:
                return fn(send_rows)
            finally:
                restore(snapshot)
                s.light_restore = None
        return work

    @app.get("/api/vision/status")
    def get_vision_status():
        return no_vision() or vision_status()

    @app.get("/api/vision/metrics")
    def get_vision_metrics():
        if (r := no_vision()) is not None:
            return r
        metrics = s.vision.metrics()  # never grabs while a run or task is active
        return metrics if metrics is not None else JSONResponse({"error": "no_frame"}, status_code=503)

    @app.get("/api/vision/preview.jpg")
    def get_preview_jpg():
        if (r := no_vision()) is not None:
            return r
        jpeg = s.vision.preview_jpeg()  # never grabs while a run or task is active: serves the last frame
        if jpeg is None:
            return JSONResponse({"error": "no_frame"}, status_code=503)
        return Response(jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/api/vision/preview.mjpg")
    async def get_preview_mjpg(request: Request, frames: int = 0):
        """MJPEG stream of the annotated preview; `frames` > 0 ends it after that many frames."""
        if (r := no_vision()) is not None:
            return r

        async def stream():
            s.vision.open_preview()  # counts as an open preview while this stream lives
            try:
                last, sent = -1, 0
                while (frames <= 0 or sent < frames) and not s.closing:
                    if await request.is_disconnected():
                        break
                    number, jpeg = s.vision.preview_frame()
                    if jpeg is None or number == last:
                        await asyncio.sleep(MJPEG_POLL_S)
                        continue
                    last, sent = number, sent + 1
                    head = f"--{MJPEG_BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(jpeg)}\r\n\r\n"
                    yield head.encode() + jpeg + b"\r\n"
            finally:
                s.vision.close_preview()

        return StreamingResponse(stream(), media_type=f"multipart/x-mixed-replace; boundary={MJPEG_BOUNDARY}",
                                 headers={"Cache-Control": "no-store"})

    @app.post("/api/vision/preview/lock")
    def post_preview_lock(body: LockBody):
        if (r := no_vision()) is not None:
            return r
        if body.action not in ("release", "point") or (body.action == "point" and (body.x is None or body.y is None)):
            return JSONResponse({"error": "bad_action"}, status_code=400)
        return {"ok": s.vision.lock(body.action, body.x, body.y)}

    @app.post("/api/vision/light")
    def post_light(body: LightBody):
        """Light the whole grid for aiming (as scripts.view --light); off re-sends the picture shown before."""
        if (r := no_vision()) is not None:
            return r

        def work():
            if body.on:
                if s.light_restore is None:
                    s.light_restore = s.grid_store.resync_bytes()
                wake = s.bridge.shift_out(DATA_PIN, CLOCK_PIN, LATCH_PIN, GROUP_SIZE, WAKE_HEX)
                lit = s.bridge.shift_out(DATA_PIN, CLOCK_PIN, LATCH_PIN, GROUP_SIZE, rows_to_hex(all_on()))
                return {"success": wake["success"] and lit["success"], "light": True}
            if s.light_restore is not None:
                restore(s.light_restore)
                s.light_restore = None
            return {"success": True, "light": False}
        return vision_task("light", work, announce=False)

    @app.get("/api/observed_state")
    def get_observed_state():
        if (r := no_vision()) is not None:
            return r
        if s.runs.busy:
            return busy()
        return s.vision.observe(s.grid_store.current())

    @app.post("/api/vision/calibrate")
    def post_calibrate():
        return no_vision() or vision_task("calibrate", with_restore(s.vision.calibrate))

    @app.post("/api/vision/check_position")
    def post_check_position():
        return no_vision() or vision_task("check_position", with_restore(s.vision.check_position))

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
