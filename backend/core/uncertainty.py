"""Member-wise ensemble statistics. Missing members never become dry/calm observations."""

import math
import re
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from core.forecast_schemas import EnsembleModelStatistics, EnsembleRange, ForecastUncertainty
from core.wind import WeightedDirection, WindVector, normalize_wind, project_support, project_wind

ENSEMBLE_VARIABLES = (
    "precipitation",
    "temperature_2m",
    "wind_speed_10m",
    "wind_direction_10m",
    "wind_gusts_10m",
)
METRICS = ("precipitation", "temperature", "windSpeed", "windGust", "headwind", "crosswind")
WET_THRESHOLD_MM = 0.1
LOCAL_TZ = ZoneInfo("Europe/Zurich")

# Past about two days the ensemble's central value verifies better than the single run; closer
# in, the single run is as good and is one consistent weather situation. The share of the
# ensemble in temperature and wind ramps linearly between the two lead times.
ENSEMBLE_BLEND_START = timedelta(hours=48)
ENSEMBLE_BLEND_FULL = timedelta(hours=72)
# Members agree on a wind direction when their mean vector keeps at least this share of their
# mean speed. Below it the direction is noise and the single run's wind stays.
WIND_DIRECTION_AGREEMENT = 0.2


def _range(values: list[float]) -> EnsembleRange:
    if len(values) < 2:
        return EnsembleRange(member_count=len(values))
    ordered = sorted(values)

    def percentile(q: float) -> float:
        position = (len(ordered) - 1) * q
        lo, hi = math.floor(position), math.ceil(position)
        return round(ordered[lo] + (ordered[hi] - ordered[lo]) * (position - lo), 3)

    return EnsembleRange(member_count=len(values), p10=percentile(0.1), median=percentile(0.5), p90=percentile(0.9))


def _statistics(values: dict[str, list[float]]) -> dict:
    rain = values["precipitation"]
    wet = [v for v in rain if v >= WET_THRESHOLD_MM]
    enough = len(rain) >= 2
    return {
        "metrics": {key: _range(series) for key, series in values.items()},
        "pop": len(wet) / len(rain) if enough else None,
        "rain_if_wet": (sum(wet) / len(wet) if wet else 0.0) if enough else None,
    }


def _members(
    data: dict, eta: datetime, requested_models: list[str]
) -> tuple[dict[str, dict[str, dict[str, float]]], datetime] | None:
    """Every member's values at the hour nearest ``eta``, by model and member, and that hour."""
    hourly = data.get("hourly")
    if not isinstance(hourly, dict) or not hourly.get("time"):
        return None
    times = [datetime.fromisoformat(t) for t in hourly["time"]]
    local_eta = eta.astimezone(LOCAL_TZ).replace(tzinfo=None) if eta.tzinfo else eta
    # Unlike the deterministic fallback, uncertainty must not be extrapolated.
    if local_eta < times[0] or local_eta > times[-1]:
        return None
    i = min(range(len(times)), key=lambda index: abs((times[index] - local_eta).total_seconds()))
    members: dict[str, dict[str, dict[str, float]]] = {}
    for key, series in hourly.items():
        variable = next((v for v in ENSEMBLE_VARIABLES if key == v or key.startswith(v + "_")), None)
        if variable is None or not isinstance(series, list) or i >= len(series):
            continue
        value = series[i]
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
            continue
        suffix = key[len(variable) :].lstrip("_")
        match = re.match(r"member(\d+)(?:_(.*))?$", suffix)
        member = str(int(match[1])) if match else "0"
        model = (match[2] if match else suffix) or (requested_models[0] if len(requested_models) == 1 else "unknown")
        # A named control and member00 refer to one member, not two votes.
        members.setdefault(model, {}).setdefault(member, {})[variable] = float(value)
    return members, times[i]


def extract_uncertainty(
    data: dict,
    eta: datetime,
    travel_bearing: float | None,
    fetched_at: datetime,
    requested_models: list[str],
    *,
    wind_support: list[WeightedDirection] | None = None,
) -> ForecastUncertainty | None:
    parsed = _members(data, eta, requested_models)
    if parsed is None:
        return None
    members, forecast_time = parsed

    pooled = {metric: [] for metric in METRICS}
    models = []
    for model, entries in sorted(members.items()):
        values = {metric: [] for metric in METRICS}
        for entry in entries.values():
            for variable, metric in (
                ("precipitation", "precipitation"),
                ("temperature_2m", "temperature"),
                ("wind_speed_10m", "windSpeed"),
                ("wind_gusts_10m", "windGust"),
            ):
                if variable in entry:
                    values[metric].append(entry[variable])
            wind = normalize_wind(entry.get("wind_speed_10m"), entry.get("wind_direction_10m"))
            if wind is not None:
                if wind_support is not None:
                    head, cross = project_support(wind, wind_support)
                elif travel_bearing is not None:
                    head, cross = project_wind(wind, travel_bearing)
                    cross = abs(cross)
                else:
                    head, cross = None, None
                if head is not None:
                    values["headwind"].append(head)
                    values["crosswind"].append(cross)
        models.append(EnsembleModelStatistics(model=model, **_statistics(values)))
        for metric in METRICS:
            pooled[metric].extend(values[metric])
    if not models:
        return None
    return ForecastUncertainty(
        **_statistics(pooled),
        models=models,
        requested_models=requested_models,
        forecast_time=forecast_time.replace(tzinfo=LOCAL_TZ).isoformat(),
        fetched_at=fetched_at.isoformat(),
    )


@dataclass(frozen=True)
class EnsembleCentral:
    """The ensemble's central estimate at one hour; a field is None with fewer than two members."""

    temp: float | None
    wind: WindVector | None
    wind_gust: float | None


def ensemble_weight(eta: datetime, reference: datetime) -> float:
    """The ensemble's share in temperature and wind: 0 up to 48 h lead, 1 from 72 h."""
    lead = (eta if eta.tzinfo is not None else eta.replace(tzinfo=LOCAL_TZ)) - reference
    return min(1.0, max(0.0, (lead - ENSEMBLE_BLEND_START) / (ENSEMBLE_BLEND_FULL - ENSEMBLE_BLEND_START)))


def _central_wind(winds: list[WindVector]) -> WindVector | None:
    speeds = [math.hypot(w.east, w.north) for w in winds]
    east, north = sum(w.east for w in winds) / len(winds), sum(w.north for w in winds) / len(winds)
    resultant = math.hypot(east, north)
    if resultant == 0 or resultant < WIND_DIRECTION_AGREEMENT * statistics.fmean(speeds):
        return None
    speed = statistics.median(speeds)
    return WindVector(east / resultant * speed, north / resultant * speed)


def ensemble_central(data: dict, eta: datetime, requested_models: list[str]) -> EnsembleCentral | None:
    """Pooled member median of temperature, gusts and wind speed; the wind direction of their mean vector.

    The direction comes from the mean vector because a median of directions is not defined. The
    speed does not: members that disagree on direction would cancel into a calm none of them
    forecast. Without an agreed direction there is no central wind at all.
    """
    parsed = _members(data, eta, requested_models)
    if parsed is None:
        return None
    entries = [entry for model in parsed[0].values() for entry in model.values()]
    temps = [e["temperature_2m"] for e in entries if "temperature_2m" in e]
    gusts = [e["wind_gusts_10m"] for e in entries if "wind_gusts_10m" in e]
    winds = [
        w for e in entries if (w := normalize_wind(e.get("wind_speed_10m"), e.get("wind_direction_10m"))) is not None
    ]
    central = EnsembleCentral(
        temp=statistics.median(temps) if len(temps) >= 2 else None,
        wind=_central_wind(winds) if len(winds) >= 2 else None,
        wind_gust=statistics.median(gusts) if len(gusts) >= 2 else None,
    )
    return None if central == EnsembleCentral(None, None, None) else central
