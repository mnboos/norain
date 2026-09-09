#!/usr/bin/env python3
"""Print a summary of a GTFS ZIP file: routes per type, trips, stops, etc.

Usage:
    python gtfs-stats.py <gtfs.zip>
"""
import csv
import io
import sys
import zipfile
from collections import Counter

ROUTE_TYPE_NAMES = {
    "0":    "Tram / Light rail",
    "1":    "Subway / Metro",
    "2":    "Rail",
    "3":    "Bus",
    "4":    "Ferry",
    "5":    "Cable tram",
    "6":    "Aerial lift / Gondola",
    "7":    "Funicular",
    "11":   "Trolleybus",
    "12":   "Monorail",
    # Extended
    "100":  "Railway",
    "101":  "High speed rail",
    "102":  "Long distance rail",
    "103":  "Inter regional rail",
    "104":  "Car transport rail",
    "105":  "Sleeper rail",
    "106":  "Regional rail",
    "107":  "Tourist railway",
    "108":  "Rail shuttle",
    "109":  "Suburban railway (S-Bahn)",
    "400":  "Urban rail",
    "401":  "Metro",
    "402":  "Underground",
    "700":  "Bus (general)",
    "701":  "Regional bus",
    "702":  "Express bus",
    "703":  "Stopping bus",
    "704":  "Local bus",
    "705":  "Night bus",
    "706":  "Post bus",
    "707":  "Special needs bus",
    "708":  "Mobility bus",
    "709":  "Sightseeing bus",
    "710":  "Shuttle bus",
    "711":  "School bus",
    "712":  "School and public bus",
    "713":  "Rail replacement bus",
    "714":  "Demand and response bus",
    "715":  "Airport link bus",
    "800":  "Trolleybus",
    "900":  "Tram",
    "1000": "Water transport",
    "1100": "Air",
    "1200": "Ferry",
    "1300": "Aerial lift",
    "1400": "Funicular",
    "1500": "Taxi",
}


def read_csv(zf: zipfile.ZipFile, name: str) -> list[dict]:
    try:
        with zf.open(name) as f:
            return list(csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig")))
    except KeyError:
        return []


def count_rows(zf: zipfile.ZipFile, name: str) -> int:
    try:
        with zf.open(name) as f:
            return sum(1 for _ in f) - 1  # subtract header
    except KeyError:
        return 0


def main(path: str) -> None:
    print(f"GTFS stats: {path}\n")
    with zipfile.ZipFile(path, "r") as zf:
        files = sorted(zf.namelist())
        print("Files in ZIP:")
        for name in files:
            size = zf.getinfo(name).file_size
            print(f"  {name:<35} {size / 1_000_000:>8.1f} MB (uncompressed)")

        print()

        # Routes per type
        routes = read_csv(zf, "routes.txt")
        type_counts: Counter = Counter(r.get("route_type", "?") for r in routes)
        print(f"Routes: {len(routes):,} total")
        print(f"  {'type_id':>7}   {'count':>6}  name")
        print(f"  {'-'*7}   {'-'*6}  {'-'*25}")
        for rtype, count in sorted(type_counts.items(), key=lambda x: -x[1]):
            name = ROUTE_TYPE_NAMES.get(rtype, "unknown")
            print(f"  {rtype:>7}   {count:>6,}  {name}")

        print()

        # Trips
        trip_count = count_rows(zf, "trips.txt")
        print(f"Trips:        {trip_count:>10,}")

        # Stops
        stop_count = count_rows(zf, "stops.txt")
        print(f"Stops:        {stop_count:>10,}")

        # Stop times (can be huge — count without loading into RAM)
        st_count = count_rows(zf, "stop_times.txt")
        print(f"Stop times:   {st_count:>10,}")

        # Transfers
        tf_count = count_rows(zf, "transfers.txt")
        print(f"Transfers:    {tf_count:>10,}")

        # Calendar
        cal_count = count_rows(zf, "calendar.txt")
        cal_dates_count = count_rows(zf, "calendar_dates.txt")
        print(f"Calendar:     {cal_count:>10,}")
        print(f"Cal. dates:   {cal_dates_count:>10,}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <gtfs.zip>")
        sys.exit(1)
    main(sys.argv[1])
