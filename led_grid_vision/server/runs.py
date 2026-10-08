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
