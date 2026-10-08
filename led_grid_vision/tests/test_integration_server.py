"""Stage 5 Part C: the server's vision endpoints and events. FastAPI test client, fake device, a camera that
follows the fake chip, scripted LLM clients. Nothing here opens the real port or camera."""

import threading
import time

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

import config
from bridge.transport import FakeTransport
from server.app import Throttle, create_app
from server.runs import RunBusy, RunManager
from tests.integration_fakes import COVERED, HEART, ChipCamera, write_calibration
from tests.led_grid.test_server import HEART_HEX, HEART_RUN, SLOW_RUN, ScriptedClient, call, read_run, reply, shift
from vision import viewfinder as V
from vision_service import VisionService

STATUS_KEYS = {"connected", "busy", "run_id"}


@pytest.fixture(scope="module")
def cal_file(tmp_path_factory):
    return write_calibration(tmp_path_factory.mktemp("vision") / "calibration.json")


@pytest.fixture(autouse=True)
def fast_reads(monkeypatch):
    monkeypatch.delenv("RECORD_EVENTS", raising=False)
    monkeypatch.setattr(config, "VISION_SETTLE_MS", 0)
    monkeypatch.setattr(config, "VISION_FLUSH_FRAMES", 0)
    monkeypatch.setattr(config, "VISION_AVG_FRAMES", 2)
    monkeypatch.setattr(config, "VISION_REQUIRE_MATCH_FOR_SAVE", "1")


class Server:
    """The app with an injected fake transport and a VisionService on a chip-following fake camera."""

    def __init__(self, tmp_path, cal_path, replies=None, vision=True, camera_factory=None, **cam_kw) -> None:
        self.transport = FakeTransport()
        self.log: list[str] = []
        self.camera = ChipCamera(self.transport.device, self.log, **cam_kw)
        self.vision = None
        if vision:
            self.vision = VisionService(True, camera_factory or (lambda: self.camera), cal_path,
                                        debug_dir=tmp_path / "debug")
        app = create_app(port="fake", state_file=tmp_path / "shift_state.json",
                         frames_log=tmp_path / "shift_frames.jsonl", skills_dir=tmp_path / "library",
                         events_log=tmp_path / "sample_events.jsonl",
                         client=ScriptedClient(replies or [reply("Done.")]), transport=self.transport,
                         vision=self.vision)
        self.client = TestClient(app)
        self.library = tmp_path / "library"

    def __enter__(self) -> TestClient:
        return self.client.__enter__()

    def __exit__(self, *exc) -> None:
        self.client.__exit__(*exc)


def wait_idle(client, timeout=20.0) -> None:
    deadline = time.monotonic() + timeout
    while client.get("/api/status").json()["busy"]:
        assert time.monotonic() < deadline, "run did not finish"
        time.sleep(0.05)


def draw_heart(client) -> None:
    assert client.post("/api/prompt", json={"prompt": "Draw a heart"}).status_code == 202
    wait_idle(client)


def picture(client) -> tuple:
    state = client.get("/api/shift_state").json()
    return state["rows"], state["display"]


# ---------------------------------------------------------------- vision off: exactly led_grid

def test_vision_off_is_led_grid(tmp_path, cal_file):
    with Server(tmp_path, cal_file, vision=False) as client:
        assert set(client.get("/api/status").json()) == STATUS_KEYS
        for method, path in (("get", "/api/vision/status"), ("get", "/api/vision/metrics"),
                             ("get", "/api/vision/preview.jpg"), ("get", "/api/vision/preview.mjpg"),
                             ("get", "/api/observed_state"), ("post", "/api/vision/calibrate"),
                             ("post", "/api/vision/check_position")):
            r = getattr(client, method)(path)
            assert r.status_code == 404 and r.json() == {"error": "vision_disabled"}, path


def test_lifespan_builds_service_from_config(tmp_path, monkeypatch, cal_file):
    monkeypatch.setattr(config, "VISION_ENABLED", "1")
    monkeypatch.setattr(config, "CALIBRATION_FILE", cal_file)
    app = create_app(port="fake", state_file=tmp_path / "s.json", frames_log=tmp_path / "f.jsonl",
                     skills_dir=tmp_path / "library", events_log=tmp_path / "e.jsonl", transport=FakeTransport())
    with TestClient(app) as client:
        status = client.get("/api/status").json()
        assert status["vision"] == {"enabled": True, "camera": "ok", "calibrated": True, "position_ok": None}


# ---------------------------------------------------------------- status

def test_status_backward_compatible(tmp_path, cal_file):
    with Server(tmp_path, cal_file) as client:
        status = client.get("/api/status").json()
        assert {k: status[k] for k in STATUS_KEYS} == {"connected": True, "busy": False, "run_id": None}
        assert status["vision"] == {"enabled": True, "camera": "ok", "calibrated": True, "position_ok": None}
        v = client.get("/api/vision/status").json()
        assert v["preview_active"] is False and v["operation"] is None and v["light"] is False


def test_camera_failure_leaves_server_running(tmp_path, cal_file):
    def broken():
        raise RuntimeError("no camera")

    srv = Server(tmp_path, cal_file, replies=HEART_RUN, camera_factory=broken)
    with srv as client:
        assert client.get("/api/status").json()["vision"]["camera"] == "error"
        assert client.get("/api/vision/metrics").status_code == 503
        assert client.get("/api/vision/preview.jpg").status_code == 503
        draw_heart(client)
        assert picture(client)[0] == HEART
        assert client.get("/api/observed_state").json()["physical_check"]["result"] == "unavailable"


# ---------------------------------------------------------------- preview and metrics

def test_metrics_match_viewfinder_readout(tmp_path):
    srv = Server(tmp_path, tmp_path / "none.json")  # uncalibrated: the tracker logic applies
    with srv as client:
        assert client.post("/api/vision/light", json={"on": True}).json() == {"success": True, "light": True}
        m = client.get("/api/vision/metrics").json()
        raw = srv.vision.raw_preview_frame()
        box = V.find_lit_grid(raw)
        sharp = V.sharpness(raw, V.choose_inset(raw.shape, None, box, None, config.VISION_VIEW_ZOOM))
        expected = V.position_readout(box, raw.shape, sharp)
        assert m["ready"] == expected["overall"] == "READY"
        assert m["px_per_led"] == expected["px_per_led"] and m["in_frame"] is expected["in_frame"] is True
        assert m["sharpness"] == round(sharp, 1)
        assert m["lock"] == "SEARCHING" and m["width"] == 1280 and m["height"] == 720
        parts = V.readout_parts(expected, sharp, None, box, False)
        assert m["colours"] == {"ready": "green", "px_per_led": V.COLOUR_NAMES[parts[1][1]], "in_frame": "green",
                                "sharpness": "neutral", "lock": "amber"}


def test_metrics_grid_not_found_when_dark(tmp_path, cal_file):
    srv = Server(tmp_path, tmp_path / "none.json")
    with srv as client:
        m = client.get("/api/vision/metrics").json()
        assert m["ready"] == "GRID NOT FOUND" and m["colours"]["ready"] == "red" and m["px_per_led"] is None


def test_mjpeg_stream_two_frames_then_stops(tmp_path, cal_file):
    srv = Server(tmp_path, cal_file)
    with srv as client:
        r = client.get("/api/vision/preview.mjpg?frames=2")
        assert r.status_code == 200 and r.headers["content-type"].startswith("multipart/x-mixed-replace")
        parts = [p for p in r.content.split(b"--frame\r\n") if p]
        assert len(parts) == 2
        for part in parts:
            head, body = part.split(b"\r\n\r\n", 1)
            assert b"Content-Type: image/jpeg" in head
            img = cv2.imdecode(np.frombuffer(body.rstrip(b"\r\n"), np.uint8), cv2.IMREAD_COLOR)
            assert img.shape == (V.CANVAS_H, V.CANVAS_W, 3)
        assert client.get("/api/vision/status").json()["preview_active"] is False
        srv.vision._loop_thread.join(timeout=2)
        assert not srv.vision._loop_thread.is_alive()


def test_preview_jpg(tmp_path, cal_file):
    with Server(tmp_path, cal_file) as client:
        r = client.get("/api/vision/preview.jpg")
        assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
        assert cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_COLOR).shape == (V.CANVAS_H, V.CANVAS_W, 3)


def test_preview_lock(tmp_path, cal_file):
    srv = Server(tmp_path, cal_file)
    with srv as client:
        client.get("/api/vision/preview.jpg")
        scale, ox, oy = V.main_geometry((720, 1280))
        r = client.post("/api/vision/preview/lock", json={"action": "point", "x": ox + 640 * scale, "y": oy + 360 * scale})
        assert r.json() == {"ok": True}
        assert srv.vision.preview_state.zoom_centre == pytest.approx((640.0, 360.0))
        assert client.post("/api/vision/preview/lock", json={"action": "point", "x": 1300, "y": 300}).json() == {"ok": False}
        assert client.post("/api/vision/preview/lock", json={"action": "release"}).json() == {"ok": True}
        assert srv.vision.preview_state.zoom_centre is None
        assert client.post("/api/vision/preview/lock", json={"action": "spin"}).status_code == 400
        assert client.post("/api/vision/preview/lock", json={"action": "point"}).status_code == 400


# ---------------------------------------------------------------- light, calibrate, check position

def test_light_on_off_restores_picture(tmp_path, cal_file):
    with Server(tmp_path, cal_file, replies=HEART_RUN) as client:
        draw_heart(client)
        before = picture(client)
        assert client.post("/api/vision/light", json={"on": True}).json()["light"] is True
        assert picture(client) == (["11111111"] * 8, "on")
        assert client.get("/api/vision/status").json()["light"] is True
        assert client.post("/api/vision/light", json={"on": False}).json() == {"success": True, "light": False}
        assert picture(client) == before
        assert client.get("/api/vision/status").json()["light"] is False


def test_calibrate_and_check_position_restore_the_picture(tmp_path, cal_file):
    path = tmp_path / "cal" / "calibration.json"
    with Server(tmp_path, path, replies=HEART_RUN) as client:
        draw_heart(client)
        before = picture(client)
        assert client.get("/api/status").json()["vision"]["calibrated"] is False
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # initial status
            r = client.post("/api/vision/calibrate").json()
            assert r["success"] is True and r["summary"].startswith("Calibration OK"), r
            phases = []
            while len(phases) < 2:
                event = ws.receive_json()
                if event["type"] == "vision_status":
                    phases.append((event["operation"], event["phase"]))
            assert phases == [("calibrate", "started"), ("calibrate", "finished")]
        assert path.exists()
        assert picture(client) == before
        assert client.get("/api/status").json()["vision"] == {"enabled": True, "camera": "ok", "calibrated": True,
                                                              "position_ok": None}
        p = client.post("/api/vision/check_position").json()
        assert p["ok"] is True and p["max_corner_shift_px"] < 1.0
        assert picture(client) == before
        assert client.get("/api/status").json()["vision"]["position_ok"] is True
        assert client.get("/api/observed_state").json()["physical_check"]["result"] == "match"


def test_calibrate_with_light_on_restores_the_pre_light_picture(tmp_path, cal_file):
    with Server(tmp_path, tmp_path / "c.json", replies=HEART_RUN) as client:
        draw_heart(client)
        before = picture(client)
        client.post("/api/vision/light", json={"on": True})
        assert client.post("/api/vision/calibrate").json()["success"] is True
        assert picture(client) == before
        assert client.get("/api/vision/status").json()["light"] is False


def test_vision_endpoints_409_during_run(tmp_path, cal_file):
    with Server(tmp_path, cal_file, replies=SLOW_RUN) as client:
        assert client.post("/api/prompt", json={"prompt": "Wait"}).status_code == 202
        assert client.get("/api/vision/status").json()["operation"] == "run"
        for method, path, body in (("post", "/api/vision/light", {"on": True}), ("get", "/api/observed_state", None),
                                   ("post", "/api/vision/calibrate", None),
                                   ("post", "/api/vision/check_position", None)):
            r = client.post(path, json=body) if method == "post" else client.get(path)
            assert r.status_code == 409 and r.json() == {"error": "run_in_progress"}, path
        assert client.get("/api/vision/preview.jpg").status_code in (200, 503)  # never a new read
        client.post("/api/stop")
        wait_idle(client)
        assert client.get("/api/vision/status").json()["operation"] is None


def test_run_manager_tasks_exclude_runs():
    started, release = threading.Event(), threading.Event()
    runs = RunManager(lambda stop: None, lambda event: None)

    def task():
        started.set()
        release.wait(5)
        return {"done": True}

    out = {}
    worker = threading.Thread(target=lambda: out.update(runs.run_task("calibrate", task)))
    worker.start()
    assert started.wait(5) and runs.busy and runs.operation == "calibrate"
    with pytest.raises(RunBusy):
        runs.start("Draw a heart")
    with pytest.raises(RunBusy):
        runs.run_task("light", dict)
    release.set()
    worker.join(5)
    assert out == {"done": True} and not runs.busy and runs.operation is None


# ---------------------------------------------------------------- observations and events

def test_websocket_observed_state_after_shift_out(tmp_path, cal_file):
    with Server(tmp_path, cal_file, replies=HEART_RUN) as client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            client.post("/api/prompt", json={"prompt": "Draw a heart"})
            events = read_run(ws)
        observed = [e for e in events if e["type"] == "observed_state"]
        assert len(observed) == 1
        assert observed[0]["physical_check"]["result"] == "match" and observed[0]["state"]["rows"] == HEART
        assert observed[0]["run_id"] == "r_0001"
        result = next(e for e in events if e["type"] == "tool_result")["result"]
        assert result["physical_check"]["result"] == "match"


def test_occlusion_mismatch_refused_save_via_server(tmp_path, cal_file):
    save = call("c2", "save_skill", {"name": "symbol_heart", "definition": {"type": "action_sequence", "actions": [
        {"tool": "shift_out", "args": {"data_pin": 25, "clock_pin": 26, "latch_pin": 27, "group_size": 2,
                                       "data_hex": HEART_HEX}}]}})
    srv = Server(tmp_path, cal_file, replies=[reply(None, [shift("c1", HEART_HEX)]), reply(None, [save]),
                                              reply("Covered.")], occluded=COVERED)
    with srv as client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            client.post("/api/prompt", json={"prompt": "Draw a heart"})
            events = read_run(ws)
        results = {e["tool"]: e["result"] for e in events if e["type"] == "tool_result"}
        assert results["shift_out"]["physical_check"]["result"] == "mismatch"
        assert not results["save_skill"]["success"] and results["save_skill"]["error"].startswith("save_refused")
        assert not srv.library.exists() or not list(srv.library.glob("*.json"))


def test_observed_state_endpoint_is_read_only(tmp_path, cal_file):
    with Server(tmp_path, cal_file, replies=HEART_RUN) as client:
        draw_heart(client)
        before = client.get("/api/shift_state").json()
        r = client.get("/api/observed_state").json()
        assert r["physical_check"]["result"] == "match" and r["observed_state"]["source"] == "camera"
        assert client.get("/api/shift_state").json() == before


# ---------------------------------------------------------------- measurement isolation

def test_preview_loop_pauses_for_operations_and_reads_grab_their_own_frames(tmp_path, cal_file):
    srv = Server(tmp_path, cal_file, replies=HEART_RUN)
    with srv as client:
        draw_heart(client)
        v = srv.vision
        calls = []
        cam = srv.camera
        flush, grab = cam.flush, cam.grab
        cam.flush = lambda n: (calls.append(("flush", threading.current_thread().name)), flush(n))[1]
        cam.grab = lambda n: (calls.append(("grab", threading.current_thread().name)), grab(n))[1]
        v.open_preview()
        try:
            deadline = time.monotonic() + 5
            while not any(t == "vision-preview" for _, t in calls):
                assert time.monotonic() < deadline
                time.sleep(0.02)
            for _ in range(3):
                calls.clear()
                result = v.observe(srv.client.get("/api/shift_state").json())
                assert result["physical_check"]["result"] == "match"
                mine = [c for c, t in calls if t != "vision-preview"]
                assert mine[0] == "flush" and "grab" in mine  # each read flushes and grabs its own frames
            v.set_operation("run")
            time.sleep(0.3)
            calls.clear()
            time.sleep(0.5)
            assert not [c for c in calls if c[1] == "vision-preview"]
            v.set_operation(None)
        finally:
            v.close_preview()


def test_throttle_limits_rate():
    now = [0.0]
    t = Throttle(4.0, clock=lambda: now[0])
    fired = 0
    for _ in range(100):  # 100 frames over 1 s
        fired += t.ready()
        now[0] += 0.01
    assert fired == 4


def test_preview_state_metrics_follow_drawn_parts():
    cam = FakeCameraLit()
    state = V.PreviewState()
    for i in range(12):  # a still, full grid locks after LOCK_STABLE_S
        _view, m = state.step(cam.grab(1)[0], "live", now=i * 0.1)
    assert m["lock"] == "LOCKED" and m["colours"]["lock"] == "green" and m["ready"] == "READY"
    assert m["fps"] == pytest.approx(10.0, rel=0.01)
    state.release()
    assert not state.tracker.locked


class FakeCameraLit:
    def __init__(self) -> None:
        from vision.fake_camera import FakeCamera
        from vision.patterns import all_on

        self._cam = FakeCamera(rows=all_on())

    def grab(self, n):
        return self._cam.grab(n)
