"""Publish the bundled demo corpus on a shell-less deployment.

Runs inside the container after the API is ready. Uploads every file in
``examples/demo/kb`` through the real multipart endpoint, waits for Celery,
then writes the approved version ids to ``DEMO_PUBLIC_VERSION_FILE`` so the
demo whitelist survives restarts on the persistent volume.

The model scope deployment cannot edit environment variables without a
restart, therefore the whitelist file is the runtime approval channel.
"""

import json
import os
import sys
import time

from pathlib import Path

import httpx


API = os.environ.get("DEMO_API", "http://127.0.0.1:7860")
TOKEN = os.environ.get("DEMO_ADMIN_TOKEN", "")
WHITELIST = os.environ.get("DEMO_PUBLIC_VERSION_FILE", "")
ROOT = Path(__file__).resolve().parent.parent
KB_DIR = ROOT / "examples" / "demo" / "kb"


def wait_ready(client: httpx.Client, timeout: float = 300.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if client.get("/api/v1/ready").json().get("ready"):
                return
        except (httpx.HTTPError, ValueError):
            pass
        time.sleep(3)
    raise SystemExit("api did not become ready")


def wait_task(client: httpx.Client, task_id: str, timeout: float = 600.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = client.get(f"/api/v1/documents/tasks/{task_id}").json()
        if data["status"] in {"completed", "failed"}:
            return data["status"]
        time.sleep(2)
    return "timeout"


def main() -> None:
    if not TOKEN:
        print("DEMO_ADMIN_TOKEN is empty; skipping corpus bootstrap")
        return

    if not WHITELIST:
        print("DEMO_PUBLIC_VERSION_FILE is empty; skipping corpus bootstrap")
        return

    target = Path(WHITELIST)
    target.parent.mkdir(parents=True, exist_ok=True)

    existing: list[str] = []
    if target.exists():
        try:
            existing = [str(item) for item in json.loads(target.read_text(encoding="utf-8"))]
        except (OSError, ValueError):
            existing = []

    headers = {"Authorization": f"Bearer {TOKEN}"}

    with httpx.Client(base_url=API, headers=headers, timeout=120.0) as client:
        wait_ready(client)

        if len(existing) >= 15:
            print(f"whitelist already has {len(existing)} versions; nothing to do")
            return

        documents = sorted(KB_DIR.glob("*.txt"))
        if not documents:
            print(f"no corpus files under {KB_DIR}")
            return

        published = list(existing)

        for path in documents:
            with path.open("rb") as handle:
                response = client.post(
                    "/api/v1/documents/upload",
                    files={"file": (path.name, handle, "text/plain")},
                )
            response.raise_for_status()
            body = response.json()
            status = wait_task(client, body["task_id"])
            print(f"{path.name:38s} {status:10s} {body['version_id']}")
            if status != "completed":
                print(f"ingestion failed for {path.name}", file=sys.stderr)
                continue
            if body["version_id"] not in published:
                published.append(body["version_id"])

        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(published, indent=2), encoding="utf-8")
        temporary.replace(target)
        print(f"published {len(published)} versions to {target}")


if __name__ == "__main__":
    main()
