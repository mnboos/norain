#!/usr/bin/env python3
"""Graph artifacts keep the exact configuration and source revision that built them."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil

ROOT = Path(os.environ.get("GRAPH_ROOT", "/graph-cache"))
REVISION = Path("/graphhopper/revision")


def jar_digest():
    with Path("/graphhopper/graphhopper.jar").open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def fingerprint(directory):
    paths = [directory / "config.yaml", *sorted((directory / "models").glob("*.json"))]
    h = hashlib.sha256()
    for path in paths:
        h.update(path.name.encode())
        h.update(path.read_bytes())
    return h.hexdigest()


def selected(name="current"):
    path = ROOT / name
    resolved = path.resolve(strict=True)
    if ROOT.resolve() not in resolved.parents:
        raise ValueError("Graph artifact must be a release inside the graph directory.")
    return resolved


def read(directory):
    data = json.loads((directory / "artifact.json").read_text())
    # The jar checksum is provenance; source-identical CI builds may differ in ZIP timestamps.
    if data["revision"] != REVISION.read_text().strip():
        raise ValueError(
            "Graph was built by a different GraphHopper image. Use its matching image or rebuild."
        )
    if not (directory / "graph" / "properties").is_file():
        raise ValueError("Graph files are missing. Build a new candidate.")
    if data["config_sha256"] != fingerprint(directory):
        raise ValueError(
            "Graph configuration changed after import. Build a new artifact."
        )
    return data


def write_manifest(directory, data):
    temporary = directory / f".artifact-{os.getpid()}.json"
    temporary.write_text(json.dumps(data, indent=2) + "\n")
    temporary.replace(directory / "artifact.json")


def link(name, directory):
    temporary = ROOT / f".{name}-{os.getpid()}"
    try:
        temporary.symlink_to(
            directory.relative_to(ROOT.resolve()), target_is_directory=True
        )
        temporary.replace(ROOT / name)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=(
            "begin",
            "finish",
            "check",
            "validate",
            "activate",
            "rollback",
            "ready",
        ),
    )
    parser.add_argument("artifact", nargs="?", default="candidate")
    parser.add_argument("--terrain")
    args = parser.parse_args()
    if args.command == "begin":
        root = ROOT / "releases"
        root.mkdir(parents=True, exist_ok=True)
        name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + f"-{os.getpid()}"
        directory = root / name
        directory.mkdir()
        shutil.copyfile("/config.yaml", directory / "config.yaml")
        shutil.copytree("/custom_models", directory / "models")
        terrain = Path(args.terrain).resolve(strict=True)
        data = {
            "revision": REVISION.read_text().strip(),
            "jar_sha256": jar_digest(),
            "config_sha256": fingerprint(directory),
            "terrain": str(terrain),
            "terrain_manifest": json.loads((terrain / "manifest.json").read_text()),
            "status": "building",
        }
        write_manifest(directory, data)
        print(directory)
        return
    directory = selected(args.artifact)
    data = read(directory)
    if args.command == "finish":
        if not (directory / "graph" / "properties").is_file():
            raise ValueError("Graph import did not produce a graph.")
        data["status"] = "built"
        write_manifest(directory, data)
        link("candidate", directory)
        return
    elif args.command == "validate":
        if data["status"] not in ("built", "validated"):
            raise ValueError("Import is incomplete.")
        data["status"] = "validated"
    elif args.command == "ready":
        if data["status"] != "validated":
            raise ValueError("Validate the candidate before activation.")
        print(directory)
        return
    elif args.command == "activate":
        if data["status"] != "validated":
            raise ValueError("Validate the candidate before activation.")
        if (ROOT / "current").is_symlink():
            old = selected("current")
            if old != directory:
                link("previous", old)
        link("current", directory)
        return
    elif args.command == "rollback":
        directory = selected("previous")
        read(directory)
        link("current", directory)
        return
    elif args.command == "check":
        if data["status"] not in ("built", "validated"):
            raise ValueError("Import is incomplete.")
        print(directory)
        return
    write_manifest(directory, data)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError) as exc:
        raise SystemExit(f"Graph artifact error: {exc}") from exc
