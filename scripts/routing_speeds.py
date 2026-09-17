"""Print the average speed each bike profile rides on a few reference routes.

Run it after changing a profile's `speed` block in data/graphhopper/models/ and
rebuilding the graph (`just routing-build`), to see what the change did:

    just routing-speeds

The routes lie inside the default Switzerland extract. With another extract, pass
your own pairs: `--route "Name=lat,lon>lat,lon"`, repeatable.
"""

import argparse
import json
import os
import urllib.error
import urllib.request

DEFAULT_ROUTES = {
    "Zürich->Luzern (53 km, mixed)": ((47.3769, 8.5417), (47.0502, 8.3093)),
    "Zürich city (5 km, urban)": ((47.3769, 8.5417), (47.3900, 8.5150)),
    "Chur->Arosa (climb)": ((46.8499, 9.5329), (46.7833, 9.6800)),
    "Bern->Thun (30 km, flat)": ((46.9480, 7.4474), (46.7580, 7.6280)),
}

# What the checked-in models aim for on a mixed road route; see docs/reference/configuration.md.
TARGET_KMH = {"bike": 18, "ebike": 22, "fast_ebike": 32}


def average_speed(base_url: str, profile: str, start: tuple, dest: tuple) -> float | None:
    """km/h over the whole route, or None when GraphHopper cannot route it."""
    body = {
        "profile": profile,
        "points": [[start[1], start[0]], [dest[1], dest[0]]],
        "calc_points": False,
        "instructions": False,
    }
    request = urllib.request.Request(
        f"{base_url}/route", json.dumps(body).encode(), {"Content-Type": "application/json"}
    )
    try:
        path = json.load(urllib.request.urlopen(request, timeout=60))["paths"][0]
    except (urllib.error.URLError, KeyError, IndexError, ValueError) as e:
        print(f"  {profile}: no route ({e})")
        return None
    if not path["time"]:
        return None
    return path["distance"] / 1000 / (path["time"] / 3.6e6)


def parse_route(spec: str) -> tuple[str, tuple, tuple]:
    name, _, points = spec.partition("=")
    start, _, dest = points.partition(">")
    to_pair = lambda p: tuple(float(v) for v in p.split(","))  # noqa: E731
    return name, to_pair(start), to_pair(dest)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.environ.get("GRAPHHOPPER_API_URL", "http://localhost:8989"))
    parser.add_argument("--profile", action="append", dest="profiles", choices=list(TARGET_KMH))
    parser.add_argument("--route", action="append", dest="routes", metavar="NAME=lat,lon>lat,lon")
    args = parser.parse_args()

    profiles = args.profiles or list(TARGET_KMH)
    routes = [parse_route(spec) for spec in args.routes] if args.routes else [
        (name, start, dest) for name, (start, dest) in DEFAULT_ROUTES.items()
    ]

    print(f"{args.url}\n")
    width = max(len(name) for name, _, _ in routes) + 2
    print(" " * width + "".join(f"{p:>14}" for p in profiles))
    print(" " * width + "".join(f"{'(~' + str(TARGET_KMH[p]) + ' km/h)':>14}" for p in profiles))
    for name, start, dest in routes:
        speeds = [average_speed(args.url, p, start, dest) for p in profiles]
        cells = "".join(f"{s:>14.1f}" if s is not None else f"{'-':>14}" for s in speeds)
        print(f"{name:{width}}{cells}")


if __name__ == "__main__":
    main()
