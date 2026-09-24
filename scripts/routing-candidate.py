#!/usr/bin/env python3
"""Validate a candidate in an isolated container, without publishing ports or stopping routing."""

import json
import os
import subprocess
import sys

engine = os.environ.get("CONTAINER", "docker")
points = json.loads(sys.argv[1])
if len(points) != 2 or any(len(point) != 2 for point in points):
    raise SystemExit("Supply two endpoints as JSON [[lon,lat],[lon,lat]].")
artifact = subprocess.check_output(
    [
        engine,
        "compose",
        "run",
        "--rm",
        "--no-deps",
        "--entrypoint",
        "python",
        "graphhopper",
        "/graphhopper/artifact.py",
        "check",
        "candidate",
    ],
    text=True,
).strip()
container = (
    subprocess.check_output(
        [engine, "compose", "run", "-d", "--no-deps", "graphhopper", "serve", artifact],
        text=True,
    )
    .strip()
    .splitlines()[-1]
)
try:
    subprocess.run(
        [
            engine,
            "exec",
            container,
            "python",
            "/graphhopper/smoke.py",
            json.dumps(points),
            "--artifact",
            artifact,
        ],
        check=True,
    )
except subprocess.CalledProcessError:
    subprocess.run([engine, "logs", "--tail", "100", container], check=False)
    raise
finally:
    subprocess.run([engine, "rm", "-f", container], check=False)
