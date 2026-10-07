"""Build the skill library through the running server: python -m scripts.build_library [prompts_file]

Sends each prompt to POST /api/prompt, polls /api/status until the run ends, prints what changed,
then waits for the human: Enter accepts, r retries the same prompt once.
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_PROMPTS = Path(__file__).resolve().parent / "library_prompts.txt"
POLL_S = 0.5


def request(base_url: str, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base_url + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def skill_versions(base_url: str) -> dict[str, dict]:
    return {s["name"]: s for s in request(base_url, "GET", "/api/skills")[1]["skills"]}


def run_prompt(base_url: str, prompt: str) -> None:
    before = skill_versions(base_url)
    code, body = request(base_url, "POST", "/api/prompt", {"prompt": prompt})
    if code != 202:
        print(f"  not started: HTTP {code} {body.get('error')}")
        return
    run_id = body["run_id"]
    t0 = time.monotonic()
    while True:
        time.sleep(POLL_S)
        status = request(base_url, "GET", "/api/status")[1]
        if not status["busy"] or status["run_id"] != run_id:
            break
    print(f"  {run_id} finished in {time.monotonic() - t0:.1f} s")

    changed = [s for name, s in skill_versions(base_url).items()
               if name not in before or s["version"] != before[name]["version"]]
    if not changed:
        print("  no skill saved")
    for s in changed:
        print(f"  saved {s['name']} v{s['version']} ({s['steps']} steps): {s['description']}")
    print("  final map:")
    for row in request(base_url, "GET", "/api/shift_state")[1]["rows"]:
        print("    " + row.replace("0", ".").replace("1", "#"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompts_file", nargs="?", type=Path, default=DEFAULT_PROMPTS)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()

    prompts = [line.strip() for line in args.prompts_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    try:
        print(f"Server status: {request(args.base_url, 'GET', '/api/status')[1]}")
    except urllib.error.URLError as e:
        print(f"Server not reachable at {args.base_url}: {e}")
        return 1

    for i, prompt in enumerate(prompts, 1):
        retried = False
        while True:
            print(f"\n[{i}/{len(prompts)}] {prompt}")
            run_prompt(args.base_url, prompt)
            answer = input("Enter = accept, r = retry once: ").strip().lower()
            if answer == "r" and not retried:
                retried = True
                continue
            break

    print("\nLibrary:")
    for s in sorted(skill_versions(args.base_url).values(), key=lambda s: s["name"]):
        print(f"  {s['name']} v{s['version']} ({s['steps']} steps): {s['description']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
