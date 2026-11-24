"""One-time setup: create the public Railway host that serves approved pages.

Creates a new project named unwall-pages and writes data/host.json. It never touches
other Railway projects, and it does not deploy or submit anything.
"""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "data" / "host.json"
NAME = "unwall-pages"


def railway(*args: str, cwd: str) -> str:
    result = subprocess.run(["railway", *args], cwd=cwd, capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise SystemExit(f"railway {' '.join(args)} failed:\n{result.stderr or result.stdout}")
    return result.stdout


def main() -> None:
    if CONFIG.exists():
        raise SystemExit(f"Already set up: {json.loads(CONFIG.read_text())['public_url']}")
    # A throwaway directory keeps the Railway link out of this repo.
    with tempfile.TemporaryDirectory() as cwd:
        railway("init", "--name", NAME, "--json", cwd=cwd)
        railway("add", "--service", NAME, "--json", cwd=cwd)
        domain = railway("domain", "--service", NAME, "--port", "8080", "--json", cwd=cwd)
        status = json.loads(railway("status", "--json", cwd=cwd))
    match = re.search(r"[a-z0-9-]+\.up\.railway\.app", domain)
    if not match:
        raise SystemExit(f"Couldn't read the new domain from:\n{domain}")
    CONFIG.parent.mkdir(exist_ok=True)
    CONFIG.write_text(json.dumps({
        "public_url": f"https://{match.group(0)}", "project": status["id"],
        "service": NAME, "environment": "production",
    }, indent=2))
    print(f"Public host ready: https://{match.group(0)}")
    print("Now click Publish live in the app.")


if __name__ == "__main__":
    main()
