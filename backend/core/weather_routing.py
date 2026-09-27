"""Weather-aware routing: the weather field GraphHopper routes through natively.

GraphHopper is patched (``docker/graphhopper/weather``) with a time-dependent A*: a request
that carries a ``weather`` hint costs every road its weight times the weather there *at the
moment the rider would be on it*, rain by the cell and headwind along the road's own bearing.
Via legs start where the previous one ended, in time as well as space. GraphHopper never
fetches weather: this module builds the field from the cache and sends it with the request.

The field is a lattice of cells (``LATTICE_STEP``) by hour, around the day's route
(``corridor_cells``). Lattice points are also valid 0.01° cell keys, so the existing
``refresh_forecast_cell`` task fetches them, and this module only ever reads the cache
(``get_cached_forecast_cell``): it runs on the planning path, which must not fetch. A cold or
missing cell is a null in the field, which GraphHopper treats as no weather.

What the weather costs is the app's judgement, ``ride_quality.ROUTING_*``, turned into
multipliers here. GraphHopper only interpolates, so the judgement stays in one place. Every
multiplier is at least 1: weather only makes a road more expensive, which LM needs.
"""

import math
from collections.abc import Iterable
from datetime import datetime, timedelta
from itertools import pairwise

from .grid import extract_sample, get_cached_forecast_cell
from .ride_quality import ROUTING_RAIN_ZONES, ROUTING_WIND_ZONES, rain_impact

LATTICE_STEP = 0.05  # degrees, ~5 km: a shower is bigger than this
CORRIDOR = 0.1  # degrees either side of the route
HOUR = timedelta(hours=1)
# Slices beyond the plain route's riding time: a detour takes longer, and a later leg of a
# multi-leg day starts later than its plain eta.
EXTRA_HOURS = 2
RIDE_TIME_SLACK = 1.5
MAX_HOURS = 24

CellKey = tuple[float, float]


def _lattice(value: float) -> float:
    return round(round(value / LATTICE_STEP) * LATTICE_STEP, 2)


def corridor_cells(sample_points: list[dict]) -> dict[CellKey, int]:
    """Lattice cells within ``CORRIDOR`` of the route, each with the elapsed seconds of the
    route point nearest to it (the moment the rider is closest)."""
    steps = round(CORRIDOR / LATTICE_STEP)
    cells: dict[CellKey, tuple[float, int]] = {}
    for sp in sample_points:
        base_lat, base_lon = _lattice(sp["lat"]), _lattice(sp["lon"])
        for i in range(-steps, steps + 1):
            for j in range(-steps, steps + 1):
                key = (round(base_lat + i * LATTICE_STEP, 2), round(base_lon + j * LATTICE_STEP, 2))
                distance = (key[0] - sp["lat"]) ** 2 + (key[1] - sp["lon"]) ** 2
                if key not in cells or distance < cells[key][0]:
                    cells[key] = (distance, sp["elapsed_s"])
    return {key: elapsed for key, (_, elapsed) in cells.items()}


def _piecewise(value: float, points: list[tuple[float, float]]) -> float:
    """Linear between ``points`` (ascending x), flat beyond the last one."""
    if value <= points[0][0]:
        return points[0][1]
    for (x0, y0), (x1, y1) in pairwise(points):
        if value <= x1:
            return y0 + (value - x0) / (x1 - x0) * (y1 - y0)
    return points[-1][1]


def rain_curve() -> list[tuple[float, float]]:
    """Weight multiplier over ``rain_impact``: the rain zones' priorities, as one continuous
    curve from dry (1) through each zone's threshold."""
    return [(0.0, 1.0), *sorted((impact, 1 / priority) for impact, priority in ROUTING_RAIN_ZONES)]


def headwind_table() -> list[list[float]]:
    """Weight multiplier over the headwind component (km/h) for GraphHopper to interpolate: each
    wind zone's priority reached at its wind speed ridden straight into."""
    return [[0.0, 1.0], *sorted([kmh, round(1 / priority, 4)] for kmh, priority in ROUTING_WIND_ZONES)]


def rain_multiplier(sample: dict) -> float | None:
    impact = rain_impact(sample)
    return None if impact is None else round(_piecewise(impact, rain_curve()), 3)


def wind_vector(sample: dict) -> tuple[float, float] | None:
    """The wind as (east, north) in km/h, pointing where it blows *to*; ``wind_dir`` is where it
    comes from."""
    speed, direction = sample.get("wind_speed"), sample.get("wind_dir")
    if speed is None or direction is None:
        return None
    radians = math.radians(direction)
    return round(-speed * math.sin(radians), 2), round(-speed * math.cos(radians), 2)


def field_hours(departure: datetime, ride_seconds: float) -> tuple[datetime, int]:
    """The first slice (the departure's hour) and how many hours the field covers."""
    t0 = departure.replace(minute=0, second=0, microsecond=0)
    covered = (departure - t0).total_seconds() + ride_seconds * RIDE_TIME_SLACK
    return t0, min(MAX_HOURS, math.ceil(covered / 3600) + EXTRA_HOURS)


def _epoch_ms(value: datetime) -> int:
    return round(value.timestamp() * 1000)


async def weather_field(
    cells: Iterable[CellKey],
    departure: datetime,
    ride_seconds: float,
    day_key: str,
    forecast_days: int,
    *,
    avoid_rain: bool,
    avoid_headwind: bool,
) -> dict | None:
    """The ``weather`` hint for a GraphHopper request (see ``WeatherField.java`` for the layout).

    None when nothing is to be avoided or no cell is warm: the request then routes plainly.
    """
    keys = list(cells)
    if not keys or not (avoid_rain or avoid_headwind):
        return None
    t0, hours = field_hours(departure, ride_seconds)
    lat0, lon0 = min(k[0] for k in keys), min(k[1] for k in keys)
    rows = round((max(k[0] for k in keys) - lat0) / LATTICE_STEP) + 1
    cols = round((max(k[1] for k in keys) - lon0) / LATTICE_STEP) + 1
    size = rows * cols * hours
    rain: list[float | None] = [None] * size
    wind_u: list[float | None] = [None] * size
    wind_v: list[float | None] = [None] * size
    warm = 0
    for key in keys:
        cell = await get_cached_forecast_cell(key[0], key[1], day_key, forecast_days)
        if cell is None:
            continue
        warm += 1
        row, col = round((key[0] - lat0) / LATTICE_STEP), round((key[1] - lon0) / LATTICE_STEP)
        for hour in range(hours):
            sample = extract_sample(cell.data, t0 + hour * HOUR, cell.source)
            if sample is None:
                continue
            at = (hour * rows + row) * cols + col
            if avoid_rain:
                rain[at] = rain_multiplier(sample)
            if avoid_headwind and (vector := wind_vector(sample)) is not None:
                wind_u[at], wind_v[at] = vector
    if not warm:
        return None
    field = {
        "departure": _epoch_ms(departure),
        "lat0": lat0,
        "lon0": lon0,
        "step": LATTICE_STEP,
        "rows": rows,
        "cols": cols,
        "t0": _epoch_ms(t0),
        "dt": _epoch_ms(t0 + HOUR) - _epoch_ms(t0),
        "hours": hours,
    }
    if avoid_rain:
        field["rain"] = rain
    if avoid_headwind:
        field |= {"wind_u": wind_u, "wind_v": wind_v, "headwind": headwind_table()}
    return field
