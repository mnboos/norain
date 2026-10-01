#!/usr/bin/env python3
"""Exercise the MeteoLane routing contract against a running candidate graph."""

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


def cell_centre(x, y, zoom):
    n = 2**zoom
    lon = (x + 0.5) / n * 360 - 180
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 0.5) / n))))
    return [lon, lat]


def check_fallback(url, coverage):
    """Heights where zoom 15 has no tiles come only from the zoom-12 fallback. GraphHopper drops
    a -Ddw. override for a key its yaml lacks without a word, and Tuscany read 0 m that way."""
    manifest = coverage.get("manifest") or {}
    cell_coverage = coverage.get("cell_coverage") or {}
    cells = cell_coverage.get("cells") or []
    if "fallback_sha256" not in manifest:
        return
    # [x, y, zoom-15 tiles present, nodata]: cells without any zoom-15 tile.
    bare = [row for row in cells if row[2] == 0]
    if not bare:
        if not cells:
            print("WARNING: no cell_coverage; the zoom-12 fallback was not checked.")
        return
    step = max(1, len(bare) // 20)
    points = [cell_centre(row[0], row[1], cell_coverage["mask_zoom"]) for row in bare[::step][:20]]
    heights = request(url + "/elevation", {"points": points}).get("elevation") or []
    if not any(isinstance(h, (int, float)) and math.isfinite(h) for h in heights):
        raise ValueError(
            f"The zoom-12 fallback answers no height in {len(points)} cells without zoom 15 "
            f"(e.g. {points[0]}); is graph.elevation.pmtiles.fallback.location in the config?"
        )


def weather_field(points, rain, east_wind):
    """A ``weather`` hint (core/weather_routing.py) over the test route: the same rain
    multiplier and wind (km/h, blowing east) everywhere, for four hours from now."""
    step = 0.05
    lat0 = min(p[1] for p in points) - step
    lon0 = min(p[0] for p in points) - step
    rows = round((max(p[1] for p in points) + step - lat0) / step) + 1
    cols = round((max(p[0] for p in points) + step - lon0) / step) + 1
    hours = 4
    size = rows * cols * hours
    now = int(time.time() * 1000)
    return {
        "departure": now,
        "lat0": lat0,
        "lon0": lon0,
        "step": step,
        "rows": rows,
        "cols": cols,
        "t0": now,
        "dt": 3_600_000,
        "hours": hours,
        "rain": [rain] * size,
        "wind_u": [east_wind] * size,
        "wind_v": [0.0] * size,
        "headwind": [[0, 1], [18, 1.18], [30, 1.67]],
    }


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
    if (
        not isinstance(heights, list)
        or len(heights) != len(points)
        or not all(
            value is None or (isinstance(value, (int, float)) and math.isfinite(value))
            for value in heights
        )
    ):
        raise ValueError("Saved-coordinate elevation lookup returned invalid data.")
    if any(value is None for value in heights):
        print("WARNING: coordinate elevation lookup contains a terrain gap.")
    coverage = request(args.url + "/coverage")
    if not coverage.get("artifact") or not coverage.get("manifest"):
        raise ValueError("Coverage report lacks the release or its terrain.")
    if coverage.get("cells_source") != "graph":
        print("WARNING: the graph kept no road cells; see graph-cells-backfill.")
    if coverage.get("cell_coverage") is None:
        print("WARNING: terrain has no cell_coverage.json; run the terrain coverage backfill.")
    check_fallback(args.url, coverage)

    def prefs(network):
        return {
            "priority": [
                {"if": "surface == GRAVEL", "multiply_by": "0.5"},
                {"if": "road_class == PRIMARY", "multiply_by": "0.6"},
                {"if": "average_slope > 6", "multiply_by": "0.4"},
                {"if": f"{network} == MISSING", "multiply_by": "0.7"},
                {"if": "urban_density == CITY", "multiply_by": "0.5"},
            ]
        }

    dry, wet = weather_field(points, 1.0, 0.0), weather_field(points, 4.0, 15.0)
    # hike has no landmarks: this also checks that flexible routing serves every variant.
    for profile in ("bike", "ebike", "fast_ebike", "hike"):
        model = prefs("foot_network" if profile == "hike" else "bike_network")
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
            dict(base, custom_model=model),
            dict(base, weather=wet),
            dict(base, weather=wet, custom_model=model),
            dict(base, points=[points[0], via, points[1]], weather=wet),
        ]
        for body in variants:
            for path in request(args.url + "/route", body)["paths"]:
                validate_path(path)
        # Weather routing (docker/graphhopper/weather): a dry field changes nothing, and a
        # bidirectional search, which cannot know the time, is refused rather than guessed.
        if (
            request(args.url + "/route", dict(base, weather=dry))["paths"][0]["points"]
            != (request(args.url + "/route", base)["paths"][0]["points"])
        ):
            raise ValueError("A dry weather field changed the route.")
        try:
            request(
                args.url + "/route",
                dict(base, weather=wet, algorithm="alternative_route"),
            )
            raise ValueError("Weather with alternative_route was not refused.")
        except urllib.error.HTTPError as error:
            if error.code != 400:
                raise
        # The existing application requests 2D; it must remain compatible until the chart step.
        plain = request(args.url + "/route", dict(base, elevation=False))["paths"][0]
        if not all(len(p) == 2 for p in plain["points"]["coordinates"]):
            raise ValueError("Existing 2D routing response changed.")
        print(
            f"{profile}: 3D elevations, timing, via points, alternatives, road models and weather passed.",
            flush=True,
        )
    if args.artifact:
        subprocess.run(
            ["python", "/graphhopper/artifact.py", "validate", args.artifact],
            check=True,
        )


if __name__ == "__main__":
    main()
