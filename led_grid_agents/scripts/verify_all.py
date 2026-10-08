"""Run every scripts/verify_stageN.py in number order; stop at the first failure. Standard library only."""

import re
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent


def main() -> int:
    stages = sorted((int(m.group(1)), p) for p in SCRIPTS.glob("verify_stage*.py")
                    if (m := re.fullmatch(r"verify_stage(\d+)\.py", p.name)))
    if not stages:
        print("VERIFY ALL: FAIL (no verify_stageN.py found)")
        return 1
    for number, path in stages:
        print(f"=== {path.name} ===", flush=True)
        if subprocess.run([sys.executable, str(path)], cwd=SCRIPTS.parent).returncode != 0:
            print(f"VERIFY ALL: FAIL (stage {number})")
            return 1
    print("VERIFY ALL: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
