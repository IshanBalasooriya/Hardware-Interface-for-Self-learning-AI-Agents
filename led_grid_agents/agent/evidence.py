"""Evidence for the Orchestrator: skill catalogue, skill outlines and frame digests, with no hex (agents master 7.5)."""

import re

from config import MAX_EVIDENCE_FRAMES
from skills.store import _is_int

HEX_RUN_RE = re.compile(r"[0-9A-Fa-f]{8,}")
HIDDEN_FRAME = "<frame data>"


def _is_param(value: object) -> bool:
    return isinstance(value, str) and value.startswith("$")


def _number(value: object, params: dict) -> int:
    """An int field's value; a $param uses its default. Anything unusable counts as 0."""
    if _is_param(value):
        value = params.get(value[1:])
    return value if _is_int(value) else 0


def _label(index: int) -> str:
    return chr(ord("A") + index) if index < 26 else f"F{index + 1}"


def _walk(actions: list, params: dict, labels: dict[str, str]) -> tuple[int, int, str]:
    """(frames sent, duration in ms, structure text) of one action list, computed without unrolling."""
    frames = duration = 0
    parts = []
    for action in actions:
        tool, args = action.get("tool"), action.get("args", {})
        if tool == "shift_out":
            key = str(args.get("data_hex"))
            if key not in labels:
                labels[key] = key if _is_param(key) else _label(len(labels))
            frames += 1
            parts.append(f"frame {labels[key]}")
        elif tool == "wait":
            ms = args.get("duration_ms")
            duration += _number(ms, params)
            parts.append(f"wait {ms}" if _is_param(ms) else f"wait {_number(ms, params)}")
        elif tool == "repeat":
            count = args.get("count")
            inner_frames, inner_duration, inner = _walk(args.get("actions") or [], params, labels)
            n = _number(count, params)
            frames += n * inner_frames
            duration += n * inner_duration
            parts.append(f"repeat {count if _is_param(count) else n} x [{inner}]")
    return frames, duration, ", ".join(parts)


def _shown_params(params: dict) -> dict:
    return {k: HIDDEN_FRAME if isinstance(v, str) and HEX_RUN_RE.search(v) else v for k, v in params.items()}


def skill_outline(definition: dict) -> dict:
    params = definition.get("params") or {}
    if not isinstance(params, dict):
        params = {}
    labels: dict[str, str] = {}
    frames, duration, structure = _walk(definition.get("actions") or [], params, labels)
    return {"params": _shown_params(params), "frames_sent": frames, "distinct_frames": len(labels),
            "duration_ms": duration, "structure": structure}


def build_catalogue(store) -> list[dict]:
    catalogue = []
    for entry in store.list():
        try:
            outline = skill_outline(store.get(entry["name"]))
        except Exception:
            continue
        catalogue.append({"name": entry["name"], "description": entry.get("description", ""),
                          "version": entry.get("version"), **outline})
    return catalogue


def frame_digest(frames: list[dict], max_frames: int = MAX_EVIDENCE_FRAMES) -> dict:
    pictures: dict[str, dict] = {}
    labels: dict[tuple, str] = {}
    sequence: list[str] = []
    counts: dict[str, int] = {}
    for frame in frames:
        rows = [str(row) for row in frame.get("rows") or []]
        display = frame.get("display")
        key = (tuple(rows), display)
        if key not in labels:
            labels[key] = f"P{len(labels) + 1}"
            pictures[labels[key]] = {"rows": rows, "lit": sum(row.count("1") for row in rows), "display": display}
        label = labels[key]
        sequence.append(label)
        counts[label] = counts.get(label, 0) + 1
    kept = frames[:max_frames]
    times = [float(frame.get("timestamp") or 0) for frame in kept]
    gaps = [int(round((later - earlier) * 100)) * 10 for earlier, later in zip(times, times[1:])]
    return {"total": len(frames), "truncated": len(frames) > max_frames, "pictures": pictures,
            "sequence": sequence[:max_frames], "gaps_ms": gaps, "counts": counts}
