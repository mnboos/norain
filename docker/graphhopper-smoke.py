#!/usr/bin/env python3
"""Exercise the NoRain routing contract against a running candidate graph."""

import argparse
import json
import math
import subprocess
import time
import urllib.error
import urllib.request


def request(url, body=None):
    req = urllib.request.Request(
        url,
        json.dumps(body).encode() if body is not None else None,
        {"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.load(response)


def validate_path(path):
    coords = path["points"]["coordinates"]
    if len(coords) < 2 or not all(
        len(p) == 3
        and math.isfinite(p[0])
        and math.isfinite(p[1])
        and (p[2] is None or math.isfinite(p[2]))
        for p in coords
    ):
        raise ValueError(
            "Route must have finite coordinates; elevation may be null in a terrain gap."
        )
    if not any(p[2] is not None and p[2] != 0 for p in coords):
        raise ValueError(
            "All route elevations are zero; verify terrain for this test route."
        )
    if path["distance"] <= 0 or path["time"] <= 0 or not path["details"]["time"]:
        raise ValueError("Route distance, time or time details are missing.")
    return coords


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "points", help="JSON [[lon,lat],[lon,lat]] inside the imported area"
    )
    parser.add_argument("--url", default="http://127.0.0.1:8989")
    parser.add_argument(
        "--artifact", help="Mark this candidate validated after all checks succeed"
    )
    args = parser.parse_args()
    points = json.loads(args.points)
    if len(points) != 2 or any(len(p) != 2 for p in points):
        raise ValueError("Supply two route endpoints as longitude/latitude pairs.")
    deadline = time.monotonic() + 900
    while True:
        try:
            request(args.url + "/info")
            break
        except (OSError, urllib.error.URLError):
            if time.monotonic() > deadline:
                raise RuntimeError("Candidate did not become healthy in 15 minutes.")
            time.sleep(2)
    heights = request(args.url + "/elevation", {"points": points}).get("elevation")
    if not isinstance(heights, list) or len(heights) != len(points) or not all(
        value is None or (isinstance(value, (int, float)) and math.isfinite(value))
        for value in heights
    ):
        raise ValueError("Saved-coordinate elevation lookup returned invalid data.")
    if any(value is None for value in heights):
        print("WARNING: coordinate elevation lookup contains a terrain gap.")
    prefs = {
        "priority": [
            {"if": "surface == GRAVEL", "multiply_by": "0.5"},
            {"if": "road_class == PRIMARY", "multiply_by": "0.6"},
            {"if": "average_slope > 6", "multiply_by": "0.4"},
            {"if": "bike_network == MISSING", "multiply_by": "0.7"},
            {"if": "urban_density == CITY", "multiply_by": "0.5"},
        ]
    }
    # Same area/heading constructs as journey wind routing; penalty-only for LM.
    w, e = sorted(p[0] for p in points)
    s, n = sorted(p[1] for p in points)
    area = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": "wind_test",
                "properties": {},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [w - 0.01, s - 0.01],
                            [e + 0.01, s - 0.01],
                            [e + 0.01, n + 0.01],
                            [w - 0.01, n + 0.01],
                            [w - 0.01, s - 0.01],
                        ]
                    ],
                },
            }
        ],
    }
    wind = {
        "areas": area,
        "priority": [
            {
                "if": "in_wind_test && orientation >= 0 && orientation < 180",
                "multiply_by": "0.8",
            }
        ],
    }
    for profile in ("bike", "ebike", "fast_ebike"):
        base = {
            "profile": profile,
            "points": points,
            "points_encoded": False,
            "elevation": True,
            "instructions": False,
            "details": ["time"],
        }
        coords = validate_path(request(args.url + "/route", base)["paths"][0])
        via = coords[len(coords) // 2][:2]
        variants = [
            dict(base, points=[points[0], via, points[1]]),
            dict(
                base,
                algorithm="alternative_route",
                **{"alternative_route.max_paths": 2},
            ),
            dict(base, custom_model=prefs),
            dict(base, custom_model=wind),
        ]
        for body in variants:
            for path in request(args.url + "/route", body)["paths"]:
                validate_path(path)
        # The existing application requests 2D; it must remain compatible until the chart step.
        plain = request(args.url + "/route", dict(base, elevation=False))["paths"][0]
        if not all(len(p) == 2 for p in plain["points"]["coordinates"]):
            raise ValueError("Existing 2D routing response changed.")
        print(
            f"{profile}: 3D elevations, timing, via points, alternatives, road and wind models passed.",
            flush=True,
        )
    if args.artifact:
        subprocess.run(
            ["python", "/graphhopper/artifact.py", "validate", args.artifact],
            check=True,
        )


if __name__ == "__main__":
    main()
