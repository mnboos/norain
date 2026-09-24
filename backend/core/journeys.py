"""Journey planning: lodging choice and the ranking of a day's alternatives.

The planning itself is ``core.journey_planner`` (routed insertions) over measured lines
(``core.journey_geometry``); the tasks in ``core/tasks.py`` drive it. How a journey is planned:

1. Day ends, one day at a time (``tasks._plan_day_ends``): route the remainder (through the
   user's via points still ahead), find where the day's time *and* distance limit runs out,
   take the wanted lodging in the last ``LODGING_WINDOW`` of it, and measure each candidate's
   real detour by routing a short insertion around it. Only candidates that routed are
   eligible. The next day starts at the lodging actually chosen.
2. Per day, GraphHopper's alternatives (two points only; a day with a via point gets one path),
   each routed around rain and headwind zones when the day is close enough.
3. Per alternative (``JourneyPlanner.stage``): gap fixes, then breaks, each a routed insertion.
   Every chosen POI is a via on the final line, so the way there and back is ridden and timed.
4. Limits are checked on that final line, from each break (``journey_geometry.check_limits``).
   An alternative over the day limit is dropped while another one keeps it.

The ranking (``rank_day``) is computed when the journey is read, from the stage forecasts
and never stored, like every ride-quality score.
"""

from datetime import datetime

from . import departures, ride_quality
from .pois import PoiHit

# How far off the route the gap-fill and break choice look for a POI.
FILL_CORRIDOR_M = 2500.0
MAX_FILL_ROUNDS = 3
# How far off the route a day may end at lodging.
LODGING_CORRIDOR_M = 2000.0
# A day ends at lodging found in this last share of its allowed length.
LODGING_WINDOW = 0.25
# A break is placed in this last share of a leg's allowed length.
BREAK_WINDOW = 0.4
# The last day may run this much over the limit rather than leave a stub day of a few km.
LAST_DAY_SLACK = 0.1

# The ranking: weather dominates, then missing POIs, then extra time. Server-only, like the
# ride-quality weights.
POI_GAP_WEIGHT = 0.3
EXTRA_TIME_WEIGHT = 0.5


def lodging_candidates(hits: list[PoiHit], kinds: list[str], from_along_m: float = 0.0) -> list[PoiHit]:
    """Lodging of the wanted kinds (every kind when none are set), from ``from_along_m`` on."""
    return [hit for hit in hits if hit.along_m >= from_along_m and (not kinds or hit.tags.get("tourism") in kinds)]


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


def rank_day(stages: list[dict], leg_m: float = 0) -> list[dict]:
    """Rank one day's alternatives. Each input: ``{id, total_seconds, gaps, detours, result}``.

    Returns per stage ``{id, ride_score, ride_label, departure, recommended, reasons}``; the
    combined ranking value itself stays here.
    """
    fastest = min((s["total_seconds"] for s in stages), default=0) or 1
    rows = []
    for stage in stages:
        score, label, departure = stage_weather(stage.get("result"))
        stage_m = stage.get("leg_m", leg_m)
        stage_s = stage.get("leg_seconds", 0)
        gaps = stage.get("gaps") or {}
        penalties = []
        visited_categories = {p["category"] for p in stage.get("detours") or []}
        for category, gap in gaps.items():
            seconds, meters = (gap.get("s", 0), gap.get("m", 0)) if isinstance(gap, dict) else (0, gap)
            penalties.append(
                max(
                    1 if isinstance(gap, dict) and category not in visited_categories else 0,
                    max(0, seconds / stage_s - 1) if stage_s else 0,
                    max(0, meters / stage_m - 1) if stage_m else 0,
                )
            )
        penalty = min(1, sum(penalties) / len(penalties)) if penalties else 0
        extra = max(0.0, stage["total_seconds"] / fastest - 1)
        combined = (score if score is not None else 0.5) + POI_GAP_WEIGHT * penalty + EXTRA_TIME_WEIGHT * extra
        reasons = []
        for category, gap in sorted((stage.get("gaps") or {}).items()):
            if isinstance(gap, dict) and category not in visited_categories:
                reasons.append(f"Kein erreichbarer Stopp für {CATEGORY_LABELS.get(category, category)} gefunden.")
            seconds, meters = (gap.get("s", 0), gap.get("m", 0)) if isinstance(gap, dict) else (0, gap)
            parts = []
            if stage_s and seconds > stage_s:
                parts.append(f"{seconds / 60:.0f} min")
            if stage_m and meters > stage_m:
                parts.append(f"{meters / 1000:.0f} km")
            if parts:
                reasons.append(f"{CATEGORY_LABELS.get(category, category)}: {' / '.join(parts)} ohne")
        for detour in stage.get("detours") or []:
            name = detour.get("name") or CATEGORY_LABELS.get(detour["category"], detour["category"])
            meters = detour.get("detour_m")
            if meters is None:
                meters = 2 * detour["offset_m"]
            reasons.append(f"Umweg ~{meters / 1000:.1f} km zu {name}")
        if extra >= 0.05:
            reasons.append(f"{round(extra * 100)} % länger als die schnellste Variante")
        overruns = stage.get("limit_overruns") or {}
        for title, over in [
            ("Tageslimit", overruns.get("day", {})),
            *((f"Etappe {leg['leg']}", leg) for leg in overruns.get("legs", [])),
        ]:
            if over.get("over_s", 0) > 0:
                reasons.append(f"{title}: ~{max(1, round(over['over_s'] / 60))} min zu lang")
            if over.get("over_m", 0) > 0:
                reasons.append(f"{title}: ~{over['over_m'] / 1000:.1f} km zu weit")
        rows.append(
            {
                "id": stage["id"],
                "ride_score": round(score, 4) if score is not None else None,
                "ride_label": label,
                "departure": departure,
                # Two unnamed toilets read the same; say it once.
                "reasons": list(dict.fromkeys(reasons)),
                "_combined": (bool(overruns.get("day")), bool(overruns.get("legs")), combined),
            }
        )
    best = min(rows, key=lambda row: row["_combined"], default=None)
    for row in rows:
        row["recommended"] = row is best
        del row["_combined"]
    return rows
