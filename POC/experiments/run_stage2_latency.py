"""
Stage 2 latency comparison, implementing
POC/Experiments_NEW/STAGE_2_AUTOMATED_LATENCY_COMPARISON.md.

Per the user's explicit design choice, all three measurement groups target
the exact same physical action -- turn the LED fully on, then turn it back
off -- purely to record latency, with no sensor reads anywhere (dropped
entirely; an earlier revision included read_analog "confirm" steps, which
the user removed for consistency, since this experiment measures set_pwm
timing only). This maximizes fairness (spec section 5: "the two compared
timing intervals should contain equivalent physical I/O") since every group
replays or performs the identical two-step sequence:

  Group A -- primitive baseline: this script directly writes the LED pin's
             PWM duty to 255 (on) then 0 (off) via the tool registry
             (bridge/serial_transport.py) -- pure, direct hardware calls,
             no LLM, no skill, no dependency on Group B. n_cycles on/off
             pairs.
  Group B -- the LLM learns to turn the LED on, then off, and saves the
             two-step sequence as a reusable action_sequence skill
             ("led_on"), timed per independent conversation cycle --
             n_cycles fresh conversations.
  Group C -- skills/skill_runner.py replays the saved "led_on" skill
             locally via its own, unmodified execute_action_sequence(),
             zero LLM calls, timed per cycle -- n_cycles replays.

Run order: Group A is fully independent (no skill needed) and can run any
time. Group B must run before Group C -- it creates/updates the "led_on"
skill that Group C replays. Each group is an independently callable
function AND its own CLI subcommand, run separately in your own time, same
usage pattern as run_manual_discovery.py.

Zero-LLM guarantee for Group C: this module never imports `openai` at
module level -- it's only imported inside run_group_b_llm_skill_cycles().
Invoking `--group c` as its own process therefore guarantees `openai` never
enters sys.modules, which the local-replay function also asserts.

Usage (5 cycles each by default; override with --n-cycles):
    python run_stage2_latency.py --group b   # run first
    python run_stage2_latency.py --group a
    python run_stage2_latency.py --group c
"""

import argparse
import csv
import json
import os
import shutil
import statistics
import sys
import time
from pathlib import Path

_EXPERIMENTS_DIR = Path(__file__).resolve().parent
_POC_DIR = _EXPERIMENTS_DIR.parent
_AGENT_DIR = _POC_DIR / "agent"
_BRIDGE_DIR = _POC_DIR / "bridge"
_SKILLS_DIR = _POC_DIR / "skills"
_LOGS_DIR = _EXPERIMENTS_DIR / "logs"

sys.path.insert(0, str(_AGENT_DIR))
sys.path.insert(0, str(_BRIDGE_DIR))
sys.path.insert(0, str(_SKILLS_DIR))
sys.path.insert(0, str(_EXPERIMENTS_DIR))

from dotenv import load_dotenv

import registry
import serial_transport
import skill_runner
import tool_declarations

load_dotenv()

# Mirrors agent_loop.py's LED_PIN -- duplicated as a literal rather than
# imported, to avoid pulling in agent_loop.py's discovery-specific
# TARGET/TOLERANCE/SYSTEM_PROMPT, which have nothing to do with this task.
LED_PIN = 5

SKILL_NAME = "led_on"
LED_ON_DUTY = 255
LED_OFF_DUTY = 0

N_CYCLES_DEFAULT = 5  # every group: one full on/off sequence per cycle
WARMUP_CALLS = 3      # full sequence repetitions discarded before Group A's real samples

PRIMITIVE_LATENCY_CSV = _LOGS_DIR / "primitive_latency.csv"
LLM_CYCLE_LATENCY_CSV = _LOGS_DIR / "llm_cycle_latency.csv"
LOCAL_SKILL_LATENCY_CSV = _LOGS_DIR / "local_skill_latency.csv"

PRIMITIVE_FIELDS = [
    "cycle_index", "timestamp", "argument_pin", "on_duty", "off_duty",
    "on_latency_ms", "off_latency_ms", "latency_ms", "success", "raw_response",
]
LLM_CYCLE_FIELDS = [
    "run_id", "cycle_index", "cycle_start_timestamp", "cycle_end_timestamp",
    "cycle_latency_ms", "llm_latency_ms", "set_pwm_latency_ms",
    "post_tool_processing_ms", "duty_commanded", "status",
]
LOCAL_SKILL_FIELDS = [
    "replay_run_id", "cycle_index", "cycle_start_timestamp", "cycle_end_timestamp",
    "cycle_latency_ms", "cold_start", "skill_load_ms", "policy_execution_ms",
    "set_pwm_latency_ms", "llm_api_calls", "zero_llm_calls_confirmed", "status",
]


def log(msg):
    print(f"[stage2_latency] {msg}", flush=True)


def fail(msg):
    log(f"ABORT: {msg}")
    raise SystemExit(1)


# --- Shared helpers ------------------------------------------------------

def _preflight_serial(port):
    port = port or os.environ.get("SERIAL_PORT")
    if not port:
        fail("SERIAL_PORT is not set (check your .env).")
    log(f"connecting to ESP32 on {port} ...")
    try:
        serial_transport.connect(port)
    except Exception as exc:
        fail(f"could not open serial port {port}: {exc} (see free_com_port.ps1 if it's held by another process).")
    ping = registry.call_tool("ping", {})
    if not ping.get("success"):
        fail(f"ESP32 did not respond to PING: {ping}")
    log("serial connected, PING succeeded.")


def _timed_call(tool_name, args):
    start = time.perf_counter()
    result = registry.call_tool(tool_name, args)
    latency_ms = round((time.perf_counter() - start) * 1000, 2)
    return result, latency_ms


def _load_skill_or_fail(name):
    path = _SKILLS_DIR / f"{name}.json"
    if not path.exists():
        fail(f"no saved skill named '{name}' at {path} -- run Group B first (--group b) to have the LLM create it.")
    with open(path) as f:
        return json.load(f)


def _write_csv(path: Path, fields, rows, label):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 0:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        backup_path = path.with_name(f"{path.stem}_pre_stage2_{stamp}{path.suffix}.bak")
        shutil.copy2(path, backup_path)
        log(f"backed up existing {label} -> {backup_path.name}")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    log(f"wrote {len(rows)} rows to {path}")


def _percentile(values, pct):
    """Linear-interpolation percentile (matches numpy's default 'linear'
    method), no numpy dependency added solely for this."""
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * (pct / 100)
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


def _print_latency_stats(label, values_ms):
    values_ms = [v for v in values_ms if v is not None]
    if not values_ms:
        log(f"{label}: no valid samples")
        return
    log(
        f"{label}: n={len(values_ms)} mean={statistics.mean(values_ms):.2f}ms "
        f"median={statistics.median(values_ms):.2f}ms "
        f"std={statistics.pstdev(values_ms):.2f}ms "
        f"min={min(values_ms):.2f}ms max={max(values_ms):.2f}ms "
        f"p95={_percentile(values_ms, 95):.2f}ms"
    )


# --- Group A: primitive baseline ------------------------------------------

def run_group_a_primitive_baseline(n_cycles=N_CYCLES_DEFAULT, serial_port=None):
    """
    Pure, direct set_pwm round-trip latency on the LED pin: write duty 255
    (on) then duty 0 (off), n_cycles times. No read_analog, no skill file,
    no dependency on Group B -- a fully independent, deterministic hardware
    primitive measurement (bridge/serial_transport.py only).

    One row per cycle (not per call), for consistency with Group B/C's
    one-row-per-cycle CSVs: the on and off calls' individual latencies are
    both preserved (on_latency_ms/off_latency_ms) and summed into
    latency_ms, the merged per-cycle figure.
    """
    _preflight_serial(serial_port)

    log(f"warm-up: {WARMUP_CALLS} discarded on/off pairs...")
    for _ in range(WARMUP_CALLS):
        registry.call_tool("set_pwm", {"pin": LED_PIN, "duty": LED_ON_DUTY})
        registry.call_tool("set_pwm", {"pin": LED_PIN, "duty": LED_OFF_DUTY})

    rows = []
    for cycle in range(1, n_cycles + 1):
        on_result, on_latency_ms = _timed_call("set_pwm", {"pin": LED_PIN, "duty": LED_ON_DUTY})
        off_result, off_latency_ms = _timed_call("set_pwm", {"pin": LED_PIN, "duty": LED_OFF_DUTY})
        latency_ms = round(on_latency_ms + off_latency_ms, 2)
        success = bool(on_result.get("success") and off_result.get("success"))
        rows.append({
            "cycle_index": cycle, "timestamp": round(time.time(), 3),
            "argument_pin": LED_PIN, "on_duty": LED_ON_DUTY, "off_duty": LED_OFF_DUTY,
            "on_latency_ms": on_latency_ms, "off_latency_ms": off_latency_ms,
            "latency_ms": latency_ms, "success": success,
            "raw_response": f"ON:{on_result.get('raw_response')} | OFF:{off_result.get('raw_response')}",
        })
        log(f"  cycle {cycle}/{n_cycles}: on {on_latency_ms}ms + off {off_latency_ms}ms = {latency_ms}ms")

    _write_csv(PRIMITIVE_LATENCY_CSV, PRIMITIVE_FIELDS, rows, "primitive_latency.csv")

    on_latencies = [r["on_latency_ms"] for r in rows if r["success"]]
    off_latencies = [r["off_latency_ms"] for r in rows if r["success"]]
    combined_latencies = [r["latency_ms"] for r in rows if r["success"]]
    _print_latency_stats("set_pwm (on, duty=255)", on_latencies)
    _print_latency_stats("set_pwm (off, duty=0)", off_latencies)
    _print_latency_stats("set_pwm (on+off merged per cycle)", combined_latencies)
    log(f"total cycles: {len(rows)} (2 set_pwm calls each)")
    return rows


# --- Group B: LLM-driven skill-creation cycles -----------------------------

def run_group_b_llm_skill_cycles(n_cycles=N_CYCLES_DEFAULT, serial_port=None, model="gpt-5.5"):
    """
    Each cycle is an independent, fresh conversation asking the LLM to turn
    the LED on, then off, then save the two-step sequence as the 'led_on'
    action_sequence skill -- kept identical every cycle so cycles are
    directly comparable. No sensor reads anywhere: this is a pure set_pwm
    on/off latency measurement, matching Group A/C.

    cycle_latency_ms spans from the first LLM request to the moment the
    SECOND set_pwm (off) result is available -- the complete on-then-off
    round trip. save_skill/closing reply happen after that boundary, in the
    same conversation, but are not charged to the cycle.

    set_pwm_latency_ms in the output row is the SUM of both sub-calls (on +
    off) within the cycle, since the schema has one column per primitive,
    not per sub-step.
    """
    import openai  # local import: keeps 'openai' out of sys.modules for Group A/C runs
    from openai import OpenAI

    _preflight_serial(serial_port)
    client = OpenAI(base_url=os.environ["OPENAI_BASE_URL"], api_key=os.environ["OPENAI_API_KEY"])

    system_prompt = (
        "You are the reasoning layer of a hardware agent. You can only affect the physical "
        "world by calling the tools you're given.\n\n"
        f"Pin {LED_PIN} is a PWM-capable pin wired to an LED.\n\n"
        f"First turn the LED fully on with set_pwm({LED_PIN}, {LED_ON_DUTY}). Then turn it "
        f"off with set_pwm({LED_PIN}, {LED_OFF_DUTY}). Then call save_skill once with name "
        f"'{SKILL_NAME}' and a definition containing: type 'action_sequence', an 'actions' "
        "list of the exact two tool/args steps you just took in order (set_pwm on, set_pwm "
        "off), and version 1. After saving, reply with one short sentence and call no "
        "further tools."
    )

    run_id = time.strftime("%Y%m%dT%H%M%S")
    rows = []
    for cycle_index in range(1, n_cycles + 1):
        log(f"LLM cycle {cycle_index}/{n_cycles} ...")
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "Turn the LED on, then off."},
        ]
        cycle_start_ts = time.time()
        llm_latency_ms_total = 0.0
        set_pwm_latencies = []
        set_pwm_count = 0
        duty_commanded_on = None
        cycle_end_ts = None
        llm_latency_ms_at_cycle_end = None
        status = "ok"

        try:
            for _ in range(8):  # 2 primitive calls + save_skill + closing reply, with headroom
                api_start = time.perf_counter()
                response = client.chat.completions.create(model=model, messages=messages, tools=tool_declarations.TOOL_DECLARATIONS)
                llm_latency_ms_total += (time.perf_counter() - api_start) * 1000
                message = response.choices[0].message
                if not message.tool_calls:
                    break
                messages.append(message)
                for tool_call in message.tool_calls:
                    name = tool_call.function.name
                    args = json.loads(tool_call.function.arguments)
                    result, latency_ms = _timed_call(name, args)
                    if name == "set_pwm":
                        set_pwm_count += 1
                        set_pwm_latencies.append(latency_ms)
                        if set_pwm_count == 1:
                            duty_commanded_on = args.get("duty")
                        elif set_pwm_count == 2 and cycle_end_ts is None:
                            cycle_end_ts = time.time()  # full on+off cycle complete
                            # Snapshot now: llm_latency_ms_total would otherwise keep
                            # growing from the later save_skill/closing-reply turns,
                            # which happen after this boundary and must not be
                            # charged to the cycle (they'd make llm_latency_ms exceed
                            # cycle_latency_ms, an impossible/misleading figure).
                            llm_latency_ms_at_cycle_end = llm_latency_ms_total
                    messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": json.dumps(result)})
        except Exception as exc:
            status = "llm_runtime_failure"
            log(f"  cycle {cycle_index} FAILED: {exc}")

        if status == "ok" and cycle_end_ts is None:
            status = "incomplete_cycle"
            log(f"  WARN cycle {cycle_index}: incomplete (set_pwm calls={set_pwm_count})")

        effective_end_ts = cycle_end_ts or time.time()
        cycle_latency_ms = round((effective_end_ts - cycle_start_ts) * 1000, 2)
        # Fall back to the full total only for an incomplete/failed cycle (no
        # snapshot was ever taken there); for a completed cycle, always use
        # the snapshot -- never the post-loop total, which may have kept
        # growing after the cycle boundary.
        llm_latency_ms = round(
            llm_latency_ms_at_cycle_end if llm_latency_ms_at_cycle_end is not None else llm_latency_ms_total, 2
        )
        set_pwm_latency_ms = round(sum(set_pwm_latencies), 2) if set_pwm_latencies else None
        post_tool_processing_ms = None
        if set_pwm_latency_ms is not None:
            accounted = llm_latency_ms + set_pwm_latency_ms
            post_tool_processing_ms = round(max(cycle_latency_ms - accounted, 0), 2)

        rows.append({
            "run_id": run_id, "cycle_index": cycle_index,
            "cycle_start_timestamp": round(cycle_start_ts, 3),
            "cycle_end_timestamp": round(effective_end_ts, 3),
            "cycle_latency_ms": cycle_latency_ms,
            "llm_latency_ms": llm_latency_ms,
            "set_pwm_latency_ms": set_pwm_latency_ms,
            "post_tool_processing_ms": post_tool_processing_ms,
            "duty_commanded": duty_commanded_on,
            "status": status,
        })
        log(
            f"  cycle {cycle_index}: {cycle_latency_ms}ms total "
            f"(llm {llm_latency_ms}ms, set_pwm sum-of-{set_pwm_count} {set_pwm_latency_ms}ms) "
            f"status={status}"
        )

    _write_csv(LLM_CYCLE_LATENCY_CSV, LLM_CYCLE_FIELDS, rows, "llm_cycle_latency.csv")

    ok_rows = [r for r in rows if r["status"] == "ok"]
    _print_latency_stats("llm_control_cycle", [r["cycle_latency_ms"] for r in ok_rows])
    _print_latency_stats("llm_api", [r["llm_latency_ms"] for r in ok_rows])
    log(f"skill '{SKILL_NAME}' saved at {_SKILLS_DIR / (SKILL_NAME + '.json')} -- ready for Group A/C.")
    return rows


# --- Group C: local skill-replay cycles ------------------------------------

def run_group_c_local_skill_replay(n_cycles=N_CYCLES_DEFAULT, serial_port=None):
    """
    Uses skills/skill_runner.py's own, unmodified execute_action_sequence()
    as the real replay path (spec: must not rewrite skill_runner.py) to
    replay the on/off sequence saved by Group B. That function reloads the
    skill JSON from disk on every call, so there is no separable
    steady-state (no-reload) path without an invasive change -- every cycle
    here is legitimately a cold-start cycle; skill_load_ms and the
    per-primitive set_pwm sub-latency aren't isolable from the aggregate
    without modifying skill_runner.py, so they're left blank per the spec's
    explicit allowance for unavailable sub-components.
    """
    assert "openai" not in sys.modules, "zero-LLM guarantee violated: openai is loaded in this process"

    _load_skill_or_fail(SKILL_NAME)  # fail fast before touching hardware if it doesn't exist
    _preflight_serial(serial_port)

    replay_run_id = time.strftime("%Y%m%dT%H%M%S")
    rows = []
    for cycle_index in range(1, n_cycles + 1):
        cycle_start_ts = time.time()
        start = time.perf_counter()
        try:
            outcome = skill_runner.execute_action_sequence(SKILL_NAME)
            status = "ok" if outcome.get("success") else "local_runtime_failure"
        except Exception as exc:
            outcome = {"trail": []}
            status = "local_runtime_failure"
            log(f"  cycle {cycle_index} FAILED: {exc}")
        cycle_latency_ms = round((time.perf_counter() - start) * 1000, 2)
        cycle_end_ts = time.time()

        rows.append({
            "replay_run_id": replay_run_id, "cycle_index": cycle_index,
            "cycle_start_timestamp": round(cycle_start_ts, 3),
            "cycle_end_timestamp": round(cycle_end_ts, 3),
            "cycle_latency_ms": cycle_latency_ms,
            "cold_start": True,
            "skill_load_ms": None,
            "policy_execution_ms": cycle_latency_ms,
            "set_pwm_latency_ms": None,
            "llm_api_calls": 0,
            "zero_llm_calls_confirmed": "openai" not in sys.modules,
            "status": status,
        })
        if cycle_index % 10 == 0 or cycle_index == n_cycles:
            log(f"  {cycle_index}/{n_cycles} local cycles collected...")

    _write_csv(LOCAL_SKILL_LATENCY_CSV, LOCAL_SKILL_FIELDS, rows, "local_skill_latency.csv")

    ok_rows = [r for r in rows if r["status"] == "ok"]
    _print_latency_stats("local_control_cycle", [r["cycle_latency_ms"] for r in ok_rows])
    zero_llm_confirmed = all(r["zero_llm_calls_confirmed"] for r in rows)
    log(f"zero LLM calls confirmed across all {len(rows)} local cycles: {zero_llm_confirmed}")
    return rows


# --- Main --------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Stage 2 latency comparison (LED on/off primitive vs LLM vs local skill).")
    parser.add_argument("--group", choices=["a", "b", "c"], required=True)
    parser.add_argument("--n-cycles", type=int, default=N_CYCLES_DEFAULT, help="cycles to sample, all groups (default 5)")
    parser.add_argument("--serial-port", default=os.environ.get("SERIAL_PORT"))
    parser.add_argument("--model", default="gpt-5.5")
    args = parser.parse_args()

    if args.group == "a":
        run_group_a_primitive_baseline(n_cycles=args.n_cycles, serial_port=args.serial_port)
    elif args.group == "b":
        run_group_b_llm_skill_cycles(n_cycles=args.n_cycles, serial_port=args.serial_port, model=args.model)
    elif args.group == "c":
        run_group_c_local_skill_replay(n_cycles=args.n_cycles, serial_port=args.serial_port)


if __name__ == "__main__":
    main()
