#!/usr/bin/env python3
"""Select a verified immutable graph path before stopping the active routing service."""

import os
import subprocess
import sys

engine = os.environ.get("CONTAINER", "docker")
mode = sys.argv[1]
if mode not in ("activate", "rollback"):
    raise SystemExit("Expected activate or rollback.")
env = dict(os.environ)
if mode == "rollback":
    env["GRAPHHOPPER_IMAGE"] = sys.argv[2]
compose = [engine, "compose"]
artifact = subprocess.check_output(
    [
        *compose,
        "run",
        "--rm",
        "--no-deps",
        "--entrypoint",
        "python",
        "graphhopper",
        "/graphhopper/artifact.py",
        "ready",
        "candidate" if mode == "activate" else "previous",
    ],
    text=True,
    env=env,
).strip()
# Capture the currently running image before replacing its container. This works even
# when a mutable local tag was rebuilt; inspect records the immutable image ID.
container = subprocess.check_output(
    [*compose, "ps", "-q", "graphhopper"], text=True
).strip()
if container:
    previous_image = subprocess.check_output(
        [engine, "inspect", "--format", "{{.Image}}", container], text=True
    ).strip()
    print(f"Previous routing image (retain for rollback): {previous_image}", flush=True)
subprocess.run([*compose, "stop", "graphhopper"], check=True)
# Explicit path avoids switching to a different candidate if an import finished meanwhile.
subprocess.run(
    [*compose, "run", "--rm", "--no-deps", "graphhopper", "activate", artifact],
    check=True,
    env=env,
)
subprocess.run(
    [*compose, "up", "-d", "--no-deps", "--force-recreate", "graphhopper"],
    check=True,
    env=env,
)
