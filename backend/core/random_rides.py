"""Random rides: a loop from the start, or the long way to a destination, of a given length.

A random ride is a ``Journey`` of kind ``RANDOM`` with one day. Planning generates candidates
and each becomes one stage of that day, so their weather (``JOURNEY_STAGE`` jobs), POI breaks
and ranking are the journey's own: the weather picks the recommended candidate.

- **A loop** is a GraphHopper round trip per candidate (``weather.build_round_trip``), each
  with its own seed and heading. GraphHopper chooses the waypoints and avoids riding a road
  twice, which generated via points alone would not.
- **Point to point** routes start → one generated via → destination. The via lies on an
  ellipse around both ends whose size is the length still missing, so every candidate leaves
  in another direction and still ends at the destination.

The length is the journey's day limit, time or distance. Time is GraphHopper's riding time for
the profile, so "two hours" means two hours at that profile's pace, the same pace every eta of
the forecast uses. GraphHopper's loop length is only approximate, and road distance over
crow-fly distance depends on the region, so each candidate is sized by routing, measuring and
scaling the request (``size``), a few times at most.
"""

import math
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .journey_geometry import Limits
from .weather import ROUTING_ERRORS, RoundTrip, RoutingPoints
from .weather_routing import LATTICE_STEP, CellKey

# The pace a profile's GraphHopper model gives on mixed roads (see "Ride speed" in CLAUDE.md),
# only for the first guess at how far a time target reaches. Sizing corrects it.
NOMINAL_SPEED_KMH = {"bike": 18.0, "ebike": 22.0, "fast_ebike": 32.0}
# A candidate within this share of its target is long enough.
LENGTH_TOLERANCE = 0.12
MAX_SIZING_ATTEMPTS = 3
# Road distance over crow-fly distance: the first guess for point-to-point sizing.
DETOUR_FACTOR = 1.3
# A request is never scaled by more than this per attempt: one odd reply (a ferry, a dead
# end) must not throw the next request across the map.
MAX_SCALE_STEP = 2.5
# Point to point with a known heading: candidates leave within this many degrees of it.
HEADING_SPREAD_DEG = 50.0
EARTH_RADIUS_M = 6_371_000.0
# A loop of length L rarely strays further than L / 4 from its start (a circle reaches L / 2π,
# a narrow loop L / 2), nor the long way round from the straight line between its ends. Weather
# routing warms this area, so it is bounded: a cell costs Open-Meteo budget.
AREA_REACH_SHARE = 0.25


@dataclass(frozen=True)
class RandomPrefs:
    """``Journey.random_prefs``: loop or not, the preferred direction and the dice."""

    round_trip: bool = True
    heading: int | None = None
    seed: int = 0

    @classmethod
    def from_json(cls, value: dict | None) -> RandomPrefs:
        value = value or {}
        heading = value.get("heading")
        return cls(
            round_trip=bool(value.get("round_trip", True)),
            heading=None if heading is None else int(heading) % 360,
            seed=int(value.get("seed", 0)),
        )

    def as_json(self) -> dict:
        return {"round_trip": self.round_trip, "heading": self.heading, "seed": self.seed}


def new_seed() -> int:
    return random.randrange(1, 2**31)  # noqa: S311 -- route variety, not secrecy


def first_guess_m(profile: str, target: Limits) -> float:
    """How far the target reaches before anything is routed: the distance, or the time at
    the profile's nominal pace; the tighter of both when both are set."""
    guesses = []
    if target.meters:
        guesses.append(float(target.meters))
    if target.seconds:
        guesses.append(target.seconds * NOMINAL_SPEED_KMH.get(profile, NOMINAL_SPEED_KMH["bike"]) / 3.6)
    return min(guesses)


def length_ratio(geometry: dict, target: Limits) -> float:
    """Actual over target, for the measure(s) the target sets; the larger when both are set."""
    ratios = []
    if target.seconds:
        ratios.append(geometry["total_seconds"] / target.seconds)
    if target.meters:
        ratios.append(geometry["total_distance_m"] / target.meters)
    return max(ratios) if ratios else 1.0


def headings(prefs: RandomPrefs, count: int) -> list[float]:
    """One heading per candidate, spread so candidates differ.

    Without a preference they share the circle evenly, rotated by the seed; with one they fan
    out around it. Deterministic in the seed, so a re-plan without a new seed repeats itself.
    """
    rng = random.Random(prefs.seed)  # noqa: S311 -- reproducible from the seed on purpose
    if prefs.heading is None:
        rotation = rng.uniform(0, 360)
        return [(rotation + 360 * i / count) % 360 for i in range(count)]
    if count == 1:
        return [float(prefs.heading)]
    return [(prefs.heading - HEADING_SPREAD_DEG + 2 * HEADING_SPREAD_DEG * i / (count - 1)) % 360 for i in range(count)]


def candidate_seed(prefs: RandomPrefs, index: int) -> int:
    return (prefs.seed * 7919 + index * 104_729) % (2**31)


def offset_point(lon: float, lat: float, bearing_deg: float, distance_m: float) -> tuple[float, float]:
    """The point ``distance_m`` from (lon, lat) along the great circle leaving at ``bearing_deg``."""
    delta = distance_m / EARTH_RADIUS_M
    theta = math.radians(bearing_deg)
    phi1, lambda1 = math.radians(lat), math.radians(lon)
    phi2 = math.asin(math.sin(phi1) * math.cos(delta) + math.cos(phi1) * math.sin(delta) * math.cos(theta))
    lambda2 = lambda1 + math.atan2(
        math.sin(theta) * math.sin(delta) * math.cos(phi1), math.cos(delta) - math.sin(phi1) * math.sin(phi2)
    )
    return round(math.degrees(lambda2), 6), round(math.degrees(phi2), 6)


def detour_via(
    start: tuple[float, float], dest: tuple[float, float], crow_sum_m: float, heading: float
) -> tuple[float, float] | None:
    """A via point V with |start V| + |V dest| = ``crow_sum_m``, on the side ``heading`` points to.

    The points with that sum form an ellipse with both ends as foci. ``heading`` is the bearing
    from the midpoint of the two ends. None when the sum is no longer than the direct distance:
    the direct route is already long enough.
    """
    # A local flat projection around the midpoint is plenty for a day's ride.
    mid_lon, mid_lat = (start[0] + dest[0]) / 2, (start[1] + dest[1]) / 2
    kx = EARTH_RADIUS_M * math.cos(math.radians(mid_lat)) * math.pi / 180
    ky = EARTH_RADIUS_M * math.pi / 180
    dx, dy = (dest[0] - start[0]) * kx, (dest[1] - start[1]) * ky
    focal = math.hypot(dx, dy) / 2
    a = crow_sum_m / 2
    if a <= focal * 1.01:
        return None
    b = math.sqrt(a * a - focal * focal)
    # Axis unit vectors: u from start to destination, v perpendicular to it.
    ux, uy = (dx / (2 * focal), dy / (2 * focal)) if focal else (0.0, 1.0)
    vx, vy = -uy, ux
    # The ellipse point in the direction of the heading, seen from the centre.
    hx, hy = math.sin(math.radians(heading)), math.cos(math.radians(heading))
    cu, cv = hx * ux + hy * uy, hx * vx + hy * vy
    scale = 1 / math.sqrt((cu / a) ** 2 + (cv / b) ** 2)
    x, y = scale * (cu * ux + cv * vx), scale * (cu * uy + cv * vy)
    return round(mid_lon + x / kx, 6), round(mid_lat + y / ky, 6)


def ride_seconds(profile: str, target: Limits) -> float:
    """How long the ride will take, for the weather field's hours."""
    return float(target.seconds or first_guess_m(profile, target) / (NOMINAL_SPEED_KMH.get(profile, 18.0) / 3.6))


def area_cells(points: RoutingPoints, profile: str, target: Limits) -> list[CellKey]:
    """The weather lattice cells a candidate may ride through: the box around both ends,
    widened by ``AREA_REACH_SHARE`` of the length. Only these are warmed and sent."""
    reach_m = first_guess_m(profile, target) * AREA_REACH_SHARE
    lats, lons = [p[1] for p in points], [p[0] for p in points]
    dlat = reach_m / 111_320
    dlon = reach_m / (111_320 * max(0.2, math.cos(math.radians(sum(lats) / len(lats)))))

    def lattice(value: float) -> int:
        return math.floor(value / LATTICE_STEP + 0.5)

    rows = range(lattice(min(lats) - dlat), lattice(max(lats) + dlat) + 1)
    cols = range(lattice(min(lons) - dlon), lattice(max(lons) + dlon) + 1)
    return [(round(r * LATTICE_STEP, 2), round(c * LATTICE_STEP, 2)) for r in rows for c in cols]


async def size(route: Callable[[float], Awaitable[dict]], target: Limits, guess: float) -> dict:
    """Route with ``guess``, scale it by how far the result missed the target, repeat.

    ``route`` takes the size parameter (a loop's requested distance, the crow-fly sum of a
    detour) and returns a geometry. Returns the candidate closest to the target; routing
    errors of the first attempt propagate, later ones end the sizing with the best so far.
    """
    best, best_miss = None, math.inf
    for attempt in range(MAX_SIZING_ATTEMPTS):
        try:
            geometry = await route(guess)
        except ROUTING_ERRORS:
            if best is None:
                raise
            break
        ratio = length_ratio(geometry, target)
        miss = abs(math.log(ratio)) if ratio > 0 else math.inf
        if miss < best_miss:
            best, best_miss = geometry, miss
        if abs(ratio - 1) <= LENGTH_TOLERANCE or ratio <= 0 or attempt == MAX_SIZING_ATTEMPTS - 1:
            break
        guess /= min(MAX_SCALE_STEP, max(1 / MAX_SCALE_STEP, ratio))
    return best


async def generate_candidate(
    *,
    profile: str,
    points: RoutingPoints,
    prefs: RandomPrefs,
    target: Limits,
    index: int,
    heading: float,
    build_round_trip: Callable[..., Awaitable[dict]],
    build_geometry: Callable[..., Awaitable[dict]],
    model: dict | None,
    interval_seconds: int,
    weather: dict | None = None,
) -> dict:
    """One sized candidate: a loop from ``points[0]``, or ``points[0]`` → via → ``points[-1]``.

    The routers are passed in, as ``JourneyPlanner`` takes them, so tests need no GraphHopper.
    ``weather`` (Plus, when the rider chose it) makes GraphHopper route around it.
    """
    extra = {"weather": weather} if weather is not None else {}
    start, dest = tuple(points[0]), tuple(points[-1])
    guess = first_guess_m(profile, target)
    if prefs.round_trip:
        seed = candidate_seed(prefs, index)

        async def loop(distance_m: float) -> dict:
            return await build_round_trip(
                profile, start, RoundTrip(distance_m, seed, heading), interval_seconds, model, **extra
            )

        return await size(loop, target, guess)

    async def detour(crow_sum_m: float) -> dict:
        via = detour_via(start, dest, crow_sum_m, heading)
        routed = (start, via, dest) if via else (start, dest)
        return await build_geometry(profile, routed, interval_seconds, model, **extra)

    return await size(detour, target, guess / DETOUR_FACTOR)
