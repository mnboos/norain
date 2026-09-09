#!/usr/bin/env python3
"""
Filter a Swiss GTFS ZIP to keep only rail, tram, metro, ferry, and funicular routes.
Removes bus routes (PostAuto, city buses, etc.) to reduce OTP memory requirements.
Also trims the calendar window to --days days from today (default: 90).

Usage:
    python filter-gtfs.py <input.zip> <output.zip> [--days N]

After running, feed the output ZIP to OTP for graph building.
"""
import argparse
import csv
import datetime
import io
import sys
import zipfile

# Route types actually present in the Swiss GTFS (gtfs_fp2026_20260408.zip).
# Excludes all bus variants (700, 702, 705, 710, 715) and taxi (1500).
# Types 116 and 117 are non-standard but appear in the data and are not buses — kept.
# See https://gtfs.org/documentation/schedule/reference/#routestxt
KEEP_ROUTE_TYPES = {
    # Rail
    "106",  # Regional rail                (382 routes)
    "109",  # Suburban railway / S-Bahn    (210)
    "102",  # Long distance rail            (47)
    "103",  # Inter regional rail           (46)
    "101",  # High speed rail               (44)
    "117",  # Unknown (non-bus)             (84)
    "116",  # Unknown (non-bus)             (12)
    "107",  # Tourist railway               (10)
    "105",  # Sleeper rail                   (4)
    # Tram / Metro
    "900",  # Tram                          (49)
    "401",  # Metro                           (2)
    # Aerial lift / funicular
    # "1300", # Aerial lift / gondola         (290)
    # "1400", # Funicular                      (53)
    # "1303", # Aerial tramway (subtype)        (2)
    # Water
    # "1000", # Water transport / ferry       (103)
}

# Files that depend on route/trip/stop filtering
FILTERED_FILES = {
    "routes.txt", "trips.txt", "stop_times.txt",
    "stops.txt", "transfers.txt", "calendar.txt", "calendar_dates.txt",
    "frequencies.txt", "pathways.txt",
}


def read_csv(zf: zipfile.ZipFile, name: str) -> list[dict]:
    try:
        with zf.open(name) as f:
            return list(csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig")))
    except KeyError:
        return []


def write_csv(zf: zipfile.ZipFile, name: str, rows: list[dict], fieldnames: list[str]) -> None:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore", lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(rows)
    zf.writestr(name, buf.getvalue().encode("utf-8"))


def first_keys(rows: list[dict]) -> list[str]:
    return list(next(iter(rows), {}).keys())


def main(input_path: str, output_path: str, days: int) -> None:
    cutoff = (datetime.date.today() + datetime.timedelta(days=days)).strftime("%Y%m%d")
    print(f"Reading {input_path} ...")
    print(f"  Calendar cutoff: {cutoff} (today + {days} days)")
    with zipfile.ZipFile(input_path, "r") as zin:
        all_files = set(zin.namelist())

        # 1. Filter routes — keep only non-bus modes
        routes = read_csv(zin, "routes.txt")
        kept_route_ids = {r["route_id"] for r in routes if r.get("route_type") in KEEP_ROUTE_TYPES}
        kept_routes = [r for r in routes if r["route_id"] in kept_route_ids]
        print(f"  routes:      {len(routes):>8,} → {len(kept_routes):>8,}")

        # 2. Filter calendar_dates and calendar by date window, then derive active service_ids
        calendar_dates = read_csv(zin, "calendar_dates.txt")
        kept_calendar_dates = [c for c in calendar_dates if c.get("date", "") <= cutoff]
        print(f"  cal_dates:   {len(calendar_dates):>8,} → {len(kept_calendar_dates):>8,}")

        calendar = read_csv(zin, "calendar.txt")
        kept_calendar = [c for c in calendar if c.get("start_date", "") <= cutoff]
        print(f"  calendar:    {len(calendar):>8,} → {len(kept_calendar):>8,}")

        active_service_ids = (
            {c["service_id"] for c in kept_calendar}
            | {c["service_id"] for c in kept_calendar_dates}
        )

        # 3. Filter trips — must match route AND active service_id
        trips = read_csv(zin, "trips.txt")
        kept_trip_ids = {
            t["trip_id"] for t in trips
            if t["route_id"] in kept_route_ids and t.get("service_id") in active_service_ids
        }
        kept_trips = [t for t in trips if t["trip_id"] in kept_trip_ids]
        print(f"  trips:       {len(trips):>8,} → {len(kept_trips):>8,}")

        # 4. Filter stop_times — largest file, keep only rows for kept trips
        print("  stop_times:  filtering (this may take a while) ...")
        stop_times_all = read_csv(zin, "stop_times.txt")
        kept_stop_times = [st for st in stop_times_all if st["trip_id"] in kept_trip_ids]
        print(f"  stop_times:  {len(stop_times_all):>8,} → {len(kept_stop_times):>8,}")
        del stop_times_all  # free memory

        # 4. Filter stops — keep only those that appear in filtered stop_times
        used_stop_ids = {st["stop_id"] for st in kept_stop_times}
        stops = read_csv(zin, "stops.txt")
        # Also keep parent stations (location_type=1) if any child is kept
        parent_ids = {s["parent_station"] for s in stops if s.get("parent_station") and s["stop_id"] in used_stop_ids}
        kept_stops = [s for s in stops if s["stop_id"] in used_stop_ids or s["stop_id"] in parent_ids]
        all_kept_stop_ids = {s["stop_id"] for s in kept_stops}
        print(f"  stops:       {len(stops):>8,} → {len(kept_stops):>8,}")

        # 5. Filter transfers — check stop, route, and trip references
        # Swiss GTFS uses from_route_id/to_route_id/from_trip_id/to_trip_id on transfers;
        # any reference to a filtered-out entity causes OTP to crash at build time.
        transfers = read_csv(zin, "transfers.txt")
        kept_transfers = [
            t for t in transfers
            if t.get("from_stop_id") in all_kept_stop_ids
            and t.get("to_stop_id") in all_kept_stop_ids
            and (not t.get("from_route_id") or t["from_route_id"] in kept_route_ids)
            and (not t.get("to_route_id") or t["to_route_id"] in kept_route_ids)
            and (not t.get("from_trip_id") or t["from_trip_id"] in kept_trip_ids)
            and (not t.get("to_trip_id") or t["to_trip_id"] in kept_trip_ids)
        ]
        print(f"  transfers:   {len(transfers):>8,} → {len(kept_transfers):>8,}")

        # 6. Further restrict calendar / calendar_dates to service_ids still referenced by kept trips
        kept_service_ids = {t["service_id"] for t in kept_trips}
        kept_calendar = [c for c in kept_calendar if c.get("service_id") in kept_service_ids]
        kept_calendar_dates = [c for c in kept_calendar_dates if c.get("service_id") in kept_service_ids]

        # 7. Filter frequencies (optional file)
        frequencies = read_csv(zin, "frequencies.txt")
        kept_frequencies = [f for f in frequencies if f.get("trip_id") in kept_trip_ids]

        # 8. Filter pathways (optional file)
        pathways = read_csv(zin, "pathways.txt")
        kept_pathways = [p for p in pathways
                         if p.get("from_stop_id") in all_kept_stop_ids
                         and p.get("to_stop_id") in all_kept_stop_ids]

        print(f"\nWriting {output_path} ...")
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zout:
            # Pass through all files we don't filter
            for name in all_files:
                if name not in FILTERED_FILES:
                    zout.writestr(name, zin.read(name))

            write_csv(zout, "routes.txt",       kept_routes,       first_keys(routes))
            write_csv(zout, "trips.txt",         kept_trips,        first_keys(trips))
            write_csv(zout, "stop_times.txt",    kept_stop_times,   first_keys(kept_stop_times))
            write_csv(zout, "stops.txt",         kept_stops,        first_keys(stops))
            if transfers:
                write_csv(zout, "transfers.txt", kept_transfers,    first_keys(transfers))
            if calendar:
                write_csv(zout, "calendar.txt",  kept_calendar,     first_keys(calendar))
            if calendar_dates:
                write_csv(zout, "calendar_dates.txt", kept_calendar_dates, first_keys(calendar_dates))
            if frequencies:
                write_csv(zout, "frequencies.txt", kept_frequencies, first_keys(frequencies))
            if pathways:
                write_csv(zout, "pathways.txt",  kept_pathways,     first_keys(pathways))

    print("\nDone.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", help="Input GTFS ZIP")
    parser.add_argument("output", help="Output (filtered) GTFS ZIP")
    parser.add_argument("--days", type=int, default=90,
                        help="Keep only calendar entries within this many days from today (default: 90)")
    args = parser.parse_args()
    main(args.input, args.output, args.days)
