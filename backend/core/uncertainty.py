"""Member-wise ensemble statistics. Missing members never become dry/calm observations."""

import math
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from .weather_schemas import EnsembleModelStatistics, EnsembleRange, ForecastUncertainty

ENSEMBLE_VARIABLES = (
    "precipitation",
    "temperature_2m",
    "wind_speed_10m",
    "wind_direction_10m",
    "wind_gusts_10m",
)
METRICS = ("precipitation", "temperature", "windSpeed", "windGust", "headwind", "crosswind")
WET_THRESHOLD_MM = 0.1


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


def extract_uncertainty(
    data: dict,
    eta: datetime,
    travel_bearing: float,
    fetched_at: datetime,
    requested_models: list[str],
) -> ForecastUncertainty | None:
    hourly = data.get("hourly")
    if not isinstance(hourly, dict) or not hourly.get("time"):
        return None
    times = [datetime.fromisoformat(t) for t in hourly["time"]]
    local_eta = eta.astimezone(ZoneInfo("Europe/Zurich")).replace(tzinfo=None) if eta.tzinfo else eta
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
            if "wind_speed_10m" in entry and "wind_direction_10m" in entry:
                relative = math.radians(entry["wind_direction_10m"] - travel_bearing)
                values["headwind"].append(entry["wind_speed_10m"] * math.cos(relative))
                values["crosswind"].append(abs(entry["wind_speed_10m"] * math.sin(relative)))
        models.append(EnsembleModelStatistics(model=model, **_statistics(values)))
        for metric in METRICS:
            pooled[metric].extend(values[metric])
    if not models:
        return None
    return ForecastUncertainty(
        **_statistics(pooled),
        models=models,
        requested_models=requested_models,
        forecast_time=times[i].replace(tzinfo=ZoneInfo("Europe/Zurich")).isoformat(),
        fetched_at=fetched_at.isoformat(),
    )
