from __future__ import annotations

import subprocess
import sys


def main() -> int:
    result = subprocess.run(
        [sys.executable, "solution.py"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    print(result.stdout.strip())
    if result.returncode != 0:
        print(result.stderr)
        return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
