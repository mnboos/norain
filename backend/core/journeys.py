"""Journey planning: days, breaks, POI gaps and the ranking of a day's alternatives.

Pure functions over a sampled geometry (``weather._path_geometry``) and POI hits
(``core.pois.PoiHit``). The planning tasks in ``core/tasks.py`` do the I/O around them:
GraphHopper, PostGIS, the cell cache.

How a journey is planned (see ``tasks.plan_journey``):

1. One route for the whole journey, with the road preferences (LM handles 300 km easily).
2. ``split_days`` cuts it where the day's limit runs out, at the lodging nearest the route
   in the last part of the day. Each day is then routed on its own between those ends.
3. Per day, GraphHopper's alternatives (they only work between two points, and only for
   about a day's distance on this graph), each routed around rain and headwind zones when
   the day is close enough for that to mean anything (``core.weather_routing``).
4. ``gap_fixes`` finds legs where a wanted POI category is missing and picks the POI whose
   detour is smallest; the planner routes through it and checks again. GraphHopper cannot
   express "water once per leg": it weighs edges, and this is a rule about the whole path.
5. ``place_breaks`` puts the breaks where the most wanted categories are together.

The ranking (``rank_day``) is computed when the journey is read, from the stage forecasts
and never stored, like every ride-quality score.
"""

from dataclasses import dataclass
from datetime import datetime
from itertools import pairwise

from . import departures, ride_quality
from .geo import vertex_distances
from .pois import PoiHit

# A POI this close to the line is on the way: no detour worth mentioning.
NEAR_M = 250.0
# How far off the route the gap-fill looks for a POI, and the most it will detour to one.
FILL_CORRIDOR_M = 2500.0
MAX_FILL_OFFSET_M = 1500.0
MAX_FILL_ROUNDS = 3
# How far off the route a day may end at lodging.
LODGING_CORRIDOR_M = 2000.0
# A day ends at lodging found in this last share of its allowed length.
LODGING_WINDOW = 0.25
# A break is placed in this last share of a leg's allowed length.
BREAK_WINDOW = 0.4
# POIs this close together along the route count as one stop.
STOP_SPAN_M = 400.0
# The last day may run this much over the limit rather than leave a stub day of a few km.
LAST_DAY_SLACK = 0.1

# The ranking: weather dominates, then missing POIs, then extra time. Server-only, like the
# ride-quality weights.
POI_GAP_WEIGHT = 0.3
EXTRA_TIME_WEIGHT = 0.5


def along_limit(geometry: dict, seconds: float | None, meters: float | None) -> float:
    """How far along the route a limit reaches: the tighter of time and distance."""
    along = vertex_distances(geometry["polyline"])
    limit = along[-1]
    if meters:
        limit = min(limit, float(meters))
    if seconds:
        times = geometry["vertex_times"]
        for i, t in enumerate(times):
            if t > seconds:
                limit = min(limit, along[max(0, i - 1)])
                break
    return limit


def point_at(geometry: dict, along_m: float) -> tuple[list[float], float]:
    """The vertex at (or just before) ``along_m``, and the elapsed seconds there."""
    along = vertex_distances(geometry["polyline"])
    index = 0
    for i, value in enumerate(along):
        if value > along_m:
            break
        index = i
    lon, lat, *_ = geometry["polyline"][index]
    return [lon, lat], float(geometry["vertex_times"][index])


def elapsed_at(geometry: dict, along_m: float) -> float:
    return point_at(geometry, along_m)[1]


def lodging_candidates(hits: list[PoiHit], kinds: list[str], from_along_m: float = 0.0) -> list[PoiHit]:
    """Lodging of the wanted kinds (every kind when none are set), from ``from_along_m`` on."""
    return [hit for hit in hits if hit.along_m >= from_along_m and (not kinds or hit.tags.get("tourism") in kinds)]


@dataclass(frozen=True)
class DayCut:
    end_along_m: float
    end: list[float]  # [lon, lat]
    lodging: PoiHit | None


def split_days(total_m: float, day_limit_m: float, lodgings: list[PoiHit], geometry: dict) -> list[DayCut]:
    """Where each day ends, on the whole-journey route. The last day ends at the destination.

    ``total_m`` and ``day_limit_m`` must be measured alike (``vertex_distances``): GraphHopper's
    own distance is a little longer, and mixing the two cuts a stub day off the end.

    A day ends at the lodging nearest the route among those in the last ``LODGING_WINDOW`` of
    the day's allowed length (the later one on a tie). With none there, it ends where the
    limit runs out, and the day says so (``lodging`` is None).
    """
    if day_limit_m <= 0:
        raise ValueError("A day must allow some riding.")
    cuts: list[DayCut] = []
    start = 0.0
    while total_m - start > day_limit_m * (1 + LAST_DAY_SLACK):
        window_lo = start + day_limit_m * (1 - LODGING_WINDOW)
        window_hi = start + day_limit_m
        candidates = [hit for hit in lodgings if window_lo <= hit.along_m <= window_hi]
        if candidates:
            best = min(candidates, key=lambda hit: (hit.offset_m, -hit.along_m))
            cuts.append(DayCut(best.along_m, [best.lon, best.lat], best))
            start = best.along_m
        else:
            end, _ = point_at(geometry, window_hi)
            cuts.append(DayCut(window_hi, end, None))
            start = window_hi
    last_lon, last_lat, *_ = geometry["polyline"][-1]
    cuts.append(DayCut(total_m, [last_lon, last_lat], None))
    return cuts


def category_gaps(hits: list[PoiHit], total_m: float, wanted: list[str]) -> dict[str, list[tuple[float, float]]]:
    """For each wanted category, the stretches (from, to) along the route between its POIs,
    counting start and end as the stretch's bounds."""
    gaps = {}
    for category in wanted:
        marks = sorted(hit.along_m for hit in hits if hit.category == category and hit.offset_m <= NEAR_M)
        bounds = [0.0, *marks, total_m]
        gaps[category] = [(a, b) for a, b in pairwise(bounds) if b > a]
    return gaps


def longest_gaps(hits: list[PoiHit], total_m: float, wanted: list[str]) -> dict[str, float]:
    return {
        category: round(max((b - a for a, b in stretches), default=0.0), 1)
        for category, stretches in category_gaps(hits, total_m, wanted).items()
    }


def gap_fixes(near: list[PoiHit], wide: list[PoiHit], total_m: float, wanted: list[str], leg_m: float) -> list[PoiHit]:
    """The POIs to route through so no wanted category is missing for longer than a leg.

    For each category, its longest stretch over ``leg_m`` gets one fix: a POI of that category
    from the wide corridor, preferably where one stop splits the stretch into two legal
    halves, with the smallest offset. POIs further off than ``MAX_FILL_OFFSET_M`` are never
    worth the detour; such a gap stays and is reported.
    """
    fixes: list[PoiHit] = []
    for category, stretches in category_gaps(near, total_m, wanted).items():
        too_long = [(a, b) for a, b in stretches if b - a > leg_m]
        if not too_long:
            continue
        a, b = max(too_long, key=lambda stretch: stretch[1] - stretch[0])
        pool = [
            hit
            for hit in wide
            if hit.category == category and a < hit.along_m < b and NEAR_M < hit.offset_m <= MAX_FILL_OFFSET_M
        ]
        splitting = [hit for hit in pool if b - leg_m <= hit.along_m <= a + leg_m]
        pick = min(splitting or pool, key=lambda hit: hit.offset_m, default=None)
        if pick is not None and all(pick.osm_ref != fix.osm_ref for fix in fixes):
            fixes.append(pick)
    return fixes


def place_breaks(geometry: dict, total_m: float, leg_m: float, hits: list[PoiHit], wanted: list[str]) -> list[dict]:
    """Breaks at most ``leg_m`` apart, each at the stop offering the most wanted categories.

    A stop is POIs within ``STOP_SPAN_M`` of each other along the route. In each leg's last
    ``BREAK_WINDOW`` the stop with the most distinct wanted categories wins (the nearer one
    to the route on a tie); with no stop there the break falls at the limit, without POIs.
    """
    near = [hit for hit in hits if hit.offset_m <= NEAR_M and hit.category in wanted]
    breaks = []
    last = 0.0
    while total_m - last > leg_m:
        lo, hi = last + leg_m * (1 - BREAK_WINDOW), last + leg_m
        in_window = [hit for hit in near if lo <= hit.along_m <= hi]
        best: list[PoiHit] = []
        best_key = None
        for anchor in in_window:
            stop = [hit for hit in in_window if abs(hit.along_m - anchor.along_m) <= STOP_SPAN_M]
            key = (len({hit.category for hit in stop}), -min(hit.offset_m for hit in stop), anchor.along_m)
            if best_key is None or key > best_key:
                best, best_key = stop, key
        at = min(hit.along_m for hit in best) if best else hi
        point, elapsed = point_at(geometry, at)
        # One POI per category is enough to say what the stop has: the one nearest the route.
        nearest: dict[str, PoiHit] = {}
        for hit in best:
            if hit.category not in nearest or hit.offset_m < nearest[hit.category].offset_m:
                nearest[hit.category] = hit
        breaks.append(
            {
                "along_m": round(at, 1),
                "elapsed_s": int(elapsed),
                "lon": point[0],
                "lat": point[1],
                "pois": [hit.as_json() for hit in sorted(nearest.values(), key=lambda hit: hit.along_m)],
            }
        )
        last = at
    return breaks


def poi_penalty(gaps: dict[str, float], leg_m: float) -> float:
    """0 when every wanted category turns up once per leg, growing with each missing stretch.
    Without a leg limit (``leg_m`` 0) there is nothing to miss."""
    if not gaps or leg_m <= 0:
        return 0.0
    return min(1.0, sum(max(0.0, gap - leg_m) / leg_m for gap in gaps.values()) / len(gaps))


# --------------------------------------------------------------------------- ranking on read
CATEGORY_LABELS = {
    "toilets": "Toilette",
    "bbq": "Grillstelle",
    "drinking_water": "Trinkwasser",
    "vending_food": "Automat: Essen",
    "vending_drinks": "Automat: Getränke",
    "vending_sweets": "Automat: Süsses",
    "vending_coffee": "Automat: Kaffee",
    "shelter": "Unterstand",
    "bike_repair": "Veloreparatur",
    "food": "Essen",
    "groceries": "Einkauf",
    "ebike_charging": "E-Bike-Laden",
    "train_station": "Bahnhof",
    "lodging": "Unterkunft",
}


def stage_weather(result: dict | None, now: datetime | None = None) -> tuple[float | None, str | None, str | None]:
    """(score, label, recommended departure) of a finished stage forecast.

    With a departure window the comparison's recommended time and its score; otherwise the
    ride at the planned time, aggregated the same way (``departures.aggregate``).
    """
    if not result:
        return None, None, None
    inputs = result.get("departure_inputs")
    if inputs:
        view = departures.comparison_view(inputs, now)
        chosen = next((c for c in view["candidates"] if c["departure_time"] == view["recommended_time"]), None)
        if chosen and chosen["ride_score"] is not None:
            return chosen["ride_score"], chosen["ride_label"], view["recommended_time"]
    samples = result.get("samples") or []
    scores = [ride_quality.ride_score(sample) for sample in samples]
    if not samples or any(score is None for score in scores):
        return None, None, result.get("departure_time")
    score = departures.aggregate([s.score for s in scores], samples)
    worst = ride_quality.worst_ride_score(samples)
    label = worst.label if worst else ride_quality.BAND_LABELS[ride_quality.score_band(score)]
    return score, label, result.get("departure_time")


def rank_day(stages: list[dict], leg_m: float) -> list[dict]:
    """Rank one day's alternatives. Each input: ``{id, total_seconds, gaps, detours, result}``.

    Returns per stage ``{id, ride_score, ride_label, departure, recommended, reasons}``; the
    combined ranking value itself stays here.
    """
    fastest = min((s["total_seconds"] for s in stages), default=0) or 1
    rows = []
    for stage in stages:
        score, label, departure = stage_weather(stage.get("result"))
        penalty = poi_penalty(stage.get("gaps") or {}, leg_m)
        extra = max(0.0, stage["total_seconds"] / fastest - 1)
        combined = (score if score is not None else 0.5) + POI_GAP_WEIGHT * penalty + EXTRA_TIME_WEIGHT * extra
        reasons = []
        for category, gap in sorted((stage.get("gaps") or {}).items()):
            if leg_m and gap > leg_m:
                reasons.append(f"{CATEGORY_LABELS.get(category, category)}: {gap / 1000:.0f} km ohne")
        for detour in stage.get("detours") or []:
            name = detour.get("name") or CATEGORY_LABELS.get(detour["category"], detour["category"])
            reasons.append(f"Umweg ~{2 * detour['offset_m'] / 1000:.1f} km zu {name}")
        if extra >= 0.05:
            reasons.append(f"{round(extra * 100)} % länger als die schnellste Variante")
        rows.append(
            {
                "id": stage["id"],
                "ride_score": round(score, 4) if score is not None else None,
                "ride_label": label,
                "departure": departure,
                # Two unnamed toilets read the same; say it once.
                "reasons": list(dict.fromkeys(reasons)),
                "_combined": combined,
            }
        )
    best = min(rows, key=lambda row: row["_combined"], default=None)
    for row in rows:
        row["recommended"] = row is best
        del row["_combined"]
    return rows
