"""Weather-aware routing: rain and headwind zones as a GraphHopper request custom model.

No Java: a request custom model may carry GeoJSON ``areas`` and test ``in_<area id>``, and the
graph encodes ``orientation`` (each edge's bearing, both directions). A rain zone makes every
road in it cost more; a wind zone makes the roads that head *into* its wind cost more. Both
are penalties only, which is what LM needs (see ``core.road_prefs``).

The weather comes from ordinary ``ForecastCell``s on a coarse lattice (``LATTICE_STEP``) in a
corridor around the day's route. Lattice points are also valid 0.01° cell keys, so the
existing ``refresh_forecast_cell`` task fetches them, and this module only ever reads the
cache (``get_cached_forecast_cell``): it runs on the planning path, which must not fetch.

Weather depends on *when* the rider is there. Each lattice cell takes the eta of the nearest
point of the day's route; the planner routes, re-reads the etas and routes again.

The multipliers are ``ride_quality.ROUTING_*``: the app's judgement, kept on the server.
"""

from datetime import datetime, timedelta

from shapely.geometry import box, mapping
from shapely.ops import unary_union

from .grid import extract_sample, get_cached_forecast_cell
from .ride_quality import ROUTING_RAIN_ZONES, ROUTING_WIND_ZONES, rain_impact

LATTICE_STEP = 0.05  # degrees, ~5 km: the zones are coarse, a shower is bigger than this
CORRIDOR = 0.1  # degrees either side of the route
SIMPLIFY = 0.005  # degrees; keeps the request small
SECTORS = 8  # wind directions, 45° each

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


async def cell_weather(
    cells: dict[CellKey, int], departure: datetime, day_key: str, forecast_days: int
) -> list[tuple[CellKey, dict]]:
    """The cached weather of each corridor cell at its eta. Cold cells are left out."""
    found = []
    for key, elapsed in cells.items():
        cell = await get_cached_forecast_cell(key[0], key[1], day_key, forecast_days)
        if cell is None:
            continue
        sample = extract_sample(cell.data, departure + timedelta(seconds=elapsed), cell.source)
        if sample is not None:
            found.append((key, sample))
    return found


def _polygons(keys: list[CellKey]) -> list[dict]:
    """The union of the cells' squares, simplified, as GeoJSON polygons."""
    half = LATTICE_STEP / 2
    shape = unary_union([box(lon - half, lat - half, lon + half, lat + half) for lat, lon in keys])
    shape = shape.simplify(SIMPLIFY, preserve_topology=True)
    parts = list(shape.geoms) if shape.geom_type == "MultiPolygon" else [shape]
    return [mapping(part) for part in parts if not part.is_empty]


def _area_statement(prefix: str, keys: list[CellKey], features: list[dict]) -> str:
    """Add the areas for these cells and return the condition that tests them."""
    ids = []
    for n, polygon in enumerate(_polygons(keys)):
        area_id = f"{prefix}_{n}"
        features.append({"type": "Feature", "id": area_id, "properties": {}, "geometry": polygon})
        ids.append(f"in_{area_id}")
    return " || ".join(ids)


def headwind_condition(sector: int) -> str:
    """Roads whose bearing points into wind from this sector (±45° around it).

    ``orientation`` is the edge's azimuth in degrees, 0 = north, clockwise; wind direction is
    where the wind comes *from*, so riding towards it is riding into it.
    """
    centre = sector * (360 / SECTORS)
    lo, hi = centre - 45, centre + 45
    if lo < 0:
        return f"(orientation >= {lo + 360:g} || orientation < {hi:g})"
    if hi > 360:
        return f"(orientation >= {lo:g} || orientation < {hi - 360:g})"
    return f"(orientation >= {lo:g} && orientation < {hi:g})"


def zone_model(weather: list[tuple[CellKey, dict]], *, avoid_rain: bool, avoid_headwind: bool) -> dict:
    """The custom model for these cells: rain zones first, then one statement per wind group."""
    features: list[dict] = []
    priority: list[dict] = []

    if avoid_rain:
        by_level: list[list[CellKey]] = [[] for _ in ROUTING_RAIN_ZONES]
        for key, sample in weather:
            impact = rain_impact(sample)
            if impact is None:
                continue
            level = next((n for n, (minimum, _) in enumerate(ROUTING_RAIN_ZONES) if impact >= minimum), None)
            if level is not None:
                by_level[level].append(key)
        keyword = "if"
        for n, keys in enumerate(by_level):
            if keys:
                condition = _area_statement(f"rain{n}", keys, features)
                priority.append({keyword: condition, "multiply_by": str(ROUTING_RAIN_ZONES[n][1])})
                keyword = "else_if"

    if avoid_headwind:
        groups: dict[tuple[int, int], list[CellKey]] = {}
        for key, sample in weather:
            speed, direction = sample.get("wind_speed"), sample.get("wind_dir")
            if speed is None or direction is None:
                continue
            level = next((n for n, (minimum, _) in enumerate(ROUTING_WIND_ZONES) if speed >= minimum), None)
            if level is not None:
                sector = round(direction / (360 / SECTORS)) % SECTORS
                groups.setdefault((level, sector), []).append(key)
        for (level, sector), keys in sorted(groups.items()):
            areas = _area_statement(f"wind{level}s{sector}", keys, features)
            priority.append(
                {"if": f"({areas}) && {headwind_condition(sector)}", "multiply_by": str(ROUTING_WIND_ZONES[level][1])}
            )

    if not priority:
        return {}
    return {"priority": priority, "areas": {"type": "FeatureCollection", "features": features}}
