"""Departure windows and comparison, with all ranking kept on the server."""

from datetime import UTC, datetime, timedelta
from math import isfinite

from . import ride_quality
from .schedule import LOCAL_TZ

STEP_MINUTES = 15
MAX_FLEX_MINUTES = 120
EQUIVALENT_SCORE = 0.02
SAMPLE_FIELDS = (
    "elapsed_s",
    "sample_index",
    "rain_rate_mm_h",
    "pop",
    "rain_if_wet",
    "temp",
    "felt_temp",
    "headwind",
    "wind_power_w",
    "weather_code",
    "wind_coverage",
)


def check_flexibility(value: int) -> int:
    if value < 0 or value > MAX_FLEX_MINUTES or value % STEP_MINUTES:
        raise ValueError("Flexibility must be 0–120 minutes in 15-minute steps.")
    return value


def instant(value: str | datetime) -> datetime:
    """Legacy naive inputs mean Swiss local time; arithmetic always uses UTC."""
    dt = datetime.fromisoformat(value) if isinstance(value, str) else value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=LOCAL_TZ)
        if dt.astimezone(UTC).astimezone(LOCAL_TZ).replace(tzinfo=None) != dt.replace(tzinfo=None):
            raise ValueError("Departure falls in a daylight-saving clock gap.")
    return dt.astimezone(UTC)


def local_iso(dt: datetime) -> str:
    return dt.astimezone(LOCAL_TZ).isoformat()


def enabled(params: dict) -> bool:
    return bool(params.get("departure_flex_before_minutes") or params.get("departure_flex_after_minutes"))


def candidate_times(params: dict) -> list[datetime]:
    departure = instant(params["departure_time"])
    before = check_flexibility(params.get("departure_flex_before_minutes", 0))
    after = check_flexibility(params.get("departure_flex_after_minutes", 0))
    return [departure + timedelta(minutes=n) for n in range(-before, after + 1, STEP_MINUTES)]


def route_job_params(route_id, departure_time: str, before: int, after: int) -> dict:
    """Params of a saved route's forecast job.

    The job key hashes this dict, so the endpoint and the background pre-build must build it
    the same way, down to the departure string. The flex keys are left out when both are 0.
    """
    params = {"route_id": str(route_id), "departure_time": departure_time}
    if before or after:
        params |= {"departure_flex_before_minutes": before, "departure_flex_after_minutes": after}
    return params


def fetch_windows(params: dict, sample_points: list[dict], today) -> list[tuple[str, int]]:
    """One shared horizon per departure date, including the ordinary baseline forecast."""
    from .weather import forecast_days_for

    times = candidate_times(params)
    days = max(forecast_days_for(t.astimezone(LOCAL_TZ), sample_points, today) for t in times)
    return [(day, days) for day in sorted({t.astimezone(LOCAL_TZ).date().isoformat() for t in times})]


def cell_covers(data: dict, eta: datetime, source: str) -> bool:
    """Comparison must not rank clamped or fabricated weather as a complete sample."""
    eta = instant(eta)
    if source == "openweathermap":
        hourly = data.get("hourly")
        if not isinstance(hourly, list) or not hourly:
            return False
        times = [datetime.fromtimestamp(row["dt"], UTC) for row in hourly]
        row = hourly[min(range(len(times)), key=lambda i: abs(times[i] - eta))]
        return times[0] <= eta <= times[-1] and _finite(row.get("temp"))
    wall = eta.astimezone(LOCAL_TZ).replace(tzinfo=None)
    for name in ("minutely_15", "hourly"):
        block = data.get(name)
        if not isinstance(block, dict) or not block.get("time"):
            continue
        times = [datetime.fromisoformat(t) for t in block["time"]]
        index = min(range(len(times)), key=lambda i: abs(times[i] - wall))
        if name == "minutely_15" and abs(times[index] - wall) > timedelta(hours=2):
            continue
        if not times[0] <= wall <= times[-1]:
            # The ordinary extractor uses this block even outside its actual coverage.
            return False
        for field in ("temperature_2m", "precipitation" if "precipitation" in block else "rain"):
            values = block.get(field) or []
            if index >= len(values) or not _finite(values[index]):
                return False
        return True
    return False


def _finite(value) -> bool:
    return isinstance(value, (float, int)) and not isinstance(value, bool) and isfinite(value)


def compact_candidate(departure: datetime, forecast, expected_count: int) -> dict:
    samples = [{k: getattr(s, k) for k in SAMPLE_FIELDS} for s in forecast.samples]
    return {
        "departure_time": local_iso(departure),
        "arrival_time": local_iso(departure + timedelta(seconds=forecast.total_seconds)),
        "complete": bool(samples)
        and len(samples) == expected_count
        and all(s["sample_index"] == i and (s["wind_coverage"] or 0) >= 1 - 1e-9 for i, s in enumerate(samples)),
        "samples": samples,
    }


def aggregate(values: list[float], samples: list[dict]) -> float:
    duration = samples[-1]["elapsed_s"] - samples[0]["elapsed_s"]
    average = (
        sum(
            (a + b) / 2 * (end["elapsed_s"] - start["elapsed_s"])
            for a, b, start, end in zip(values, values[1:], samples, samples[1:], strict=False)
        )
        / duration
        if duration > 0
        else values[0]
    )
    return 0.75 * average + 0.25 * max(values)


def comparison_view(stored: dict, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    requested = instant(stored["requested_time"])
    candidates, factors = [], {}
    for item in stored["candidates"]:
        samples = item.get("samples", [])
        scores = [ride_quality.ride_score(s, ride_quality.RIDE_QUALITY) for s in samples]
        available = (
            item.get("complete", False)
            and instant(item["departure_time"]) >= now
            and bool(scores)
            and all(s is not None for s in scores)
        )
        score = aggregate([s.score for s in scores], samples) if available else None
        if available:
            factors[item["departure_time"]] = {
                factor: aggregate([getattr(s, factor) for s in scores], samples)
                * ride_quality.RIDE_QUALITY.weights.get(factor, 0)
                for factor in ride_quality.FACTORS
            }
        candidates.append(
            {
                "departure_time": item["departure_time"],
                "arrival_time": item["arrival_time"],
                "available": bool(available),
                "ride_score": score,
                "ride_label": ride_quality.BAND_LABELS[ride_quality.score_band(score)]
                if score is not None
                else "Keine vollständigen Wetterdaten",
            }
        )
    valid = [c for c in candidates if c["available"]]
    recommended = None
    explanation = "Nicht genügend Wetterdaten zum Vergleichen der Abfahrtszeiten."
    if valid:
        best = min(c["ride_score"] for c in valid)
        tied = [c for c in valid if c["ride_score"] <= best + EQUIVALENT_SCORE]
        recommended = min(
            tied, key=lambda c: (abs(instant(c["departure_time"]) - requested), instant(c["departure_time"]))
        )
        baseline = next((c for c in valid if instant(c["departure_time"]) == requested), None)
        explanation = "Voraussichtlich die günstigsten Bedingungen im gewählten Zeitfenster."
        if baseline and baseline in tied:
            explanation = (
                "Ähnliche Bedingungen – deine gewünschte Abfahrtszeit passt bereits."
                if len(tied) > 1
                else "Deine gewünschte Abfahrtszeit bietet bereits die günstigsten Bedingungen."
            )
        elif baseline:
            improvements = {
                f: factors[baseline["departure_time"]][f] - factors[recommended["departure_time"]][f]
                for f in ride_quality.FACTORS
            }
            factor = max(improvements, key=lambda name: improvements[name])
            positive = sum(max(0, x) for x in improvements.values())
            if positive and improvements[factor] / positive >= ride_quality.MIN_WORST_SHARE:
                explanation = {
                    "rain": "Weniger Regen während deiner Fahrt.",
                    "wind": "Weniger Gegenwind während deiner Fahrt.",
                    "temp": "Angenehmere Temperaturen während deiner Fahrt.",
                    "frost": "Geringeres Frostrisiko während deiner Fahrt.",
                }[factor]
    return {
        "requested_time": stored["requested_time"],
        "window_start": stored["window_start"],
        "window_end": stored["window_end"],
        "candidates": candidates,
        "recommended_time": recommended["departure_time"] if recommended else None,
        "explanation": explanation,
    }
