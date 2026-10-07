"""Conversation loop: model turns, tool calls through the registry, events for every step."""

import json
import logging
import time
from typing import Callable

from openai import OpenAI

from agent.prompts import build_system_prompt
from agent.tools import TOOL_SCHEMAS, AgentContext, call_tool
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
