"""Journey I/O orchestration with local insertions and occurrence-based waypoint tracking."""

import asyncio
from dataclasses import dataclass, field, replace
from itertools import pairwise

from .geo import haversine_m
from .journey_geometry import (
    LineMeasure,
    check_limits,
    join_geometries,
    longest_visited_gaps,
    project_hits,
    repair_breaks,
    slice_geometry,
    visited_gaps,
)
from .journeys import FILL_CORRIDOR_M, MAX_FILL_ROUNDS
from .weather import ROUTING_ERRORS, SAMPLE_INTERVAL_DEFAULT_S

INSERT_SPAN_M = 2000
MAX_ROUTED_CANDIDATES = 6
# Allow minor map/entrance inaccuracies, never an unaccounted access journey.
MAX_POI_SNAP_M = 25.0
# The most a gap fix or break POI may add to the ride, routed (the old crow-fly cap, 1.5 km off
# the line, allowed about this much). Lodging has no cap: the day limit decides.
MAX_POI_DETOUR_M = 3000.0
MAX_PLAN_ROUTE_REQUESTS = 10_000


# Prefer useful progress before optimizing the access detour. Widen only after
# every evaluated candidate in the narrower band has failed.
STOP_WINDOWS = ((0.60, 0.85), (0.40, 0.95), (0.0, 1.0))
STOP_GROUP_SPAN_M = 400.0


def stop_windows(measure, hits, start, end, limits):
    values = measure.seconds if limits.seconds else measure.meters
    span = values[end] - values[start]
    seen = set()
    for low, high in STOP_WINDOWS:
        window = []
        for hit in hits:
            index = measure.index(hit.along_m)
            progress = (values[index] - values[start]) / span if span else 0
            if hit.osm_ref not in seen and low <= progress <= high:
                window.append(hit)
        seen.update(hit.osm_ref for hit in window)
        yield window


@dataclass
class RoutingBudget:
    used: int = 0
    essential: int = 0
    failures: int = 0
    exhausted: bool = False
    limiter: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(4))
    memo: dict = field(default_factory=dict)

    def reserve(self, count):
        if self.used + count > MAX_PLAN_ROUTE_REQUESTS:
            self.exhausted = True
            return False
        self.used += count
        return True


class JourneyPlanner:
    def __init__(self, profile, model, budget, build_geometry, route_legs, pois_along):
        self.profile, self.model, self.budget = profile, model, budget
        self.build_geometry, self.route_legs, self.pois_along = build_geometry, route_legs, pois_along

    async def route(self, points, *, optional=False):
        """Read exact GH visit indices, falling back to separate legs when unavailable."""
        points = tuple(tuple(p) for p in points)
        if optional and not self.budget.reserve(1):
            return None
        if not optional:
            self.budget.essential += 1
        geometry = await self.build_geometry(self.profile, points, SAMPLE_INTERVAL_DEFAULT_S, self.model)
        indices = geometry.get("waypoint_indices", [])
        if len(points) == 2:
            return geometry, [0, len(geometry["polyline"]) - 1]
        if (
            len(indices) == len(points)
            and indices[0] == 0
            and indices[-1] == len(geometry["polyline"]) - 1
            and all(a <= b for a, b in pairwise(indices))
        ):
            return geometry, indices
        if optional and not self.budget.reserve(len(points) - 1):
            return None
        parts = []
        for a, b in pairwise(points):
            if not optional:
                self.budget.essential += 1
            parts.append(await self.build_geometry(self.profile, (a, b), SAMPLE_INTERVAL_DEFAULT_S, self.model))
        return join_geometries(parts)

    async def window_hits(self, geometry, start, end, categories, corridor):
        """Query and project on this occurrence of the search window, not the whole ride."""
        if end <= start or not categories:
            return []
        line = geometry["polyline"][start : end + 1]
        hits = await self.pois_along(line, categories, corridor)
        offset = LineMeasure(geometry).meters[start]
        return [replace(hit, along_m=along + offset) for hit, along in project_hits(line, hits)]

    async def candidates(
        self,
        geometry,
        events,
        hits,
        start,
        end,
        limits,
        *,
        categories_first=False,
        max_detour_m=None,
        group_stops=False,
    ):
        """Only routed candidates are returned; nearby places never count as visits."""
        measure = LineMeasure(geometry)
        grouped = {}
        for hit in hits:
            k = measure.index(hit.along_m)
            if not start < k <= end:
                continue
            # A committed visit at this occurrence needs no duplicate insertion.
            if any(e["index"] == k and any(p["osm_ref"] == hit.osm_ref for p in e.get("pois", [])) for e in events):
                continue
            grouped.setdefault(hit.osm_ref, []).append(hit)

        def near_stop(group):
            return group_stops and any(
                e.get("pois")
                and start < e["index"] <= end
                and abs(measure.meters[e["index"]] - group[0].along_m) <= STOP_GROUP_SPAN_M
                for e in events
            )

        groups = sorted(
            grouped.values(), key=lambda group: (not near_stop(group), group[0].offset_m, -group[0].along_m)
        )[:MAX_ROUTED_CANDIDATES]
        evaluations = []
        for group in groups:
            hit = group[0]
            k = measure.index(hit.along_m)
            a = max(start, measure.index(measure.meters[k] - INSERT_SPAN_M))
            b = min(len(measure.meters) - 1, measure.index(measure.meters[k] + INSERT_SPAN_M) + 1)
            inside = sorted((e for e in events if a < e["index"] < b), key=lambda e: e["index"])
            before = [e for e in inside if e["index"] <= k]
            after = [e for e in inside if e["index"] > k]
            point = [hit.lon, hit.lat]
            left = [geometry["polyline"][a], *(e["point"] for e in before), point]
            right = [point, *(e["point"] for e in after), geometry["polyline"][b]]
            if not self.budget.reserve(2):
                break
            evaluations.append((group, k, a, b, before, after, left, right))
        if not evaluations:
            return []
        replies = await self.route_legs(
            self.profile,
            [seq for item in evaluations for seq in item[-2:]],
            self.model,
            limiter=self.budget.limiter,
            memo=self.budget.memo,
        )
        choices = []
        for item, outgoing, onward in zip(evaluations, replies[::2], replies[1::2], strict=True):
            if outgoing is None or onward is None:
                self.budget.failures += 1
                continue
            group, k, a, b, before, after, left, right = item
            prefix = measure.between(start, a)
            arrival = tuple(x + y for x, y in zip(prefix, outgoing, strict=True))
            if not limits.allows(*arrival):
                continue
            baseline = measure.between(a, b)
            detour = tuple(max(0, x + y - z) for x, y, z in zip(outgoing, onward, baseline, strict=True))
            if max_detour_m is not None and detour[1] > max_detour_m:
                continue
            choices.append(
                {
                    "group": group,
                    "k": k,
                    "a": a,
                    "b": b,
                    "before": before,
                    "after": after,
                    "left": left,
                    "right": right,
                    "detour": detour,
                }
            )
        return sorted(
            choices,
            key=lambda c: (
                -len({h.category for h in c["group"]}) if categories_first else 0,
                not near_stop(c["group"]),
                c["detour"][0],
                -c["k"],
            ),
        )

    async def insert(self, geometry, events, choice, start, limits, *, is_break=False):
        """Commit only after full geometry passes; preserve everything outside the anchors."""
        a, b = choice["a"], choice["b"]
        points = [*choice["left"], *choice["right"][1:]]
        try:
            reply = await self.route(points, optional=True)
            if reply is None:
                return None
            insertion, indices = reply
            position = len(choice["left"]) - 1
            # GH may successfully route to a different road hundreds of metres away.
            # Check the new POI and all existing POI visits being rerouted before commit.
            poi_positions = [position]
            poi_positions.extend(n for n, e in enumerate(choice["before"], 1) if e.get("pois"))
            poi_positions.extend(n for n, e in enumerate(choice["after"], position + 1) if e.get("pois"))
            if any(
                haversine_m(*insertion["polyline"][indices[n]][:2], *points[n][:2]) > MAX_POI_SNAP_M
                for n in poi_positions
            ):
                self.budget.failures += 1
                return None
            poi_index = a + indices[position]
            final, _ = join_geometries(
                [slice_geometry(geometry, 0, a), insertion, slice_geometry(geometry, b, len(geometry["polyline"]) - 1)]
            )
            measure = LineMeasure(final)
            if not limits.allows(*measure.between(start, poi_index)):
                return None
        except ROUTING_ERRORS:
            self.budget.failures += 1
            return None
        shift = len(insertion["polyline"]) - 1 - (b - a)
        mapped = []
        for event in events:
            if event["index"] <= a:
                mapped.append(dict(event))
            elif event["index"] >= b:
                mapped.append({**event, "index": event["index"] + shift})
        for n, event in enumerate(choice["before"], 1):
            mapped.append({**event, "index": a + indices[n]})
        for n, event in enumerate(choice["after"], position + 1):
            mapped.append({**event, "index": a + indices[n]})
        old_s, old_m = LineMeasure(geometry).between(a, b)
        detour_s = max(0, insertion["total_seconds"] - old_s)
        detour_m = max(0, insertion["total_distance_m"] - old_m)
        pois = [{**h.as_json(), "detour_s": round(detour_s), "detour_m": round(detour_m, 1)} for h in choice["group"]]
        event = {
            "index": poi_index,
            "point": [choice["group"][0].lon, choice["group"][0].lat],
            "pois": pois,
            "break": is_break,
        }
        mapped.append(event)
        mapped.sort(key=lambda e: e["index"])
        return final, mapped, event

    async def stage(self, path, events, wanted, leg_limits, day_limits, *, last_day=False):
        geometry = path
        # Gap fixes count only committed visits: a POI near the line is not a visit until routed through.
        for _ in range(MAX_FILL_ROUNDS if wanted else 0):
            changed = False
            for category in wanted:
                gaps = visited_gaps(geometry, events, [category])[category]
                over = [g for g in gaps if not leg_limits.allows(g[2], g[3])]
                visited = any(any(p["category"] == category for p in e.get("pois", [])) for e in events)
                # Categories are requested visits, even when the entire day is shorter
                # than the leg allowance (or there is no leg allowance at all).
                if not over and visited:
                    continue
                start, end, _, _ = max(
                    over or gaps,
                    key=lambda g: max(
                        g[2] / leg_limits.seconds if leg_limits.seconds else 0,
                        g[3] / leg_limits.meters if leg_limits.meters else 0,
                    ),
                )
                search_end = LineMeasure(geometry).boundary(start, leg_limits)
                hits = await self.window_hits(geometry, start, min(end, search_end), wanted, FILL_CORRIDOR_M)
                hits = [
                    h
                    for h in hits
                    if h.category == category
                    or any(other.osm_ref == h.osm_ref and other.category == category for other in hits)
                ]
                inserted = None
                for window in stop_windows(LineMeasure(geometry), hits, start, min(end, search_end), leg_limits):
                    choices = await self.candidates(
                        geometry,
                        events,
                        window,
                        start,
                        end,
                        leg_limits,
                        max_detour_m=MAX_POI_DETOUR_M,
                        group_stops=True,
                    )
                    for choice in choices:
                        inserted = await self.insert(geometry, events, choice, start, leg_limits, is_break=True)
                        if inserted:
                            geometry, events, _ = inserted
                            changed = True
                            break
                    if inserted:
                        break
            if not changed:
                break

        start = 0
        while start < len(geometry["polyline"]) - 1:
            measure = LineMeasure(geometry)
            boundary = measure.boundary(start, leg_limits)
            if boundary == len(measure.meters) - 1:
                break
            if boundary <= start:
                # The final check records the unavoidable edge overrun.
                start += 1
                continue
            existing = [e for e in events if start < e["index"] <= boundary and e.get("pois")]
            hits = await self.window_hits(geometry, start, boundary, wanted, FILL_CORRIDOR_M)
            inserted, best = None, None
            # Existing visits participate in the same location bands as new candidates.
            values = measure.seconds if leg_limits.seconds else measure.meters
            span = values[boundary] - values[start]
            for window, (low, high) in zip(
                stop_windows(measure, hits, start, boundary, leg_limits), STOP_WINDOWS, strict=True
            ):
                eligible = [
                    e for e in existing if low <= ((values[e["index"]] - values[start]) / span if span else 0) <= high
                ]
                best = min(
                    eligible, key=lambda e: (-len({p["category"] for p in e["pois"]}), -e["index"]), default=None
                )
                choices = await self.candidates(
                    geometry,
                    events,
                    window,
                    start,
                    boundary,
                    leg_limits,
                    categories_first=True,
                    max_detour_m=MAX_POI_DETOUR_M,
                    group_stops=True,
                )
                for choice in choices:
                    if best and len({p["category"] for p in best["pois"]}) >= len(
                        {h.category for h in choice["group"]}
                    ):
                        break
                    inserted = await self.insert(geometry, events, choice, start, leg_limits, is_break=True)
                    if inserted:
                        break
                if inserted or best:
                    break
            if inserted:
                geometry, events, event = inserted
                start = event["index"]
            elif best:
                best["break"] = True
                start = best["index"]
            else:
                events.append({"index": boundary, "point": geometry["polyline"][boundary], "pois": [], "break": True})
                start = boundary
        breaks = repair_breaks(geometry, events, leg_limits)
        detours = [p for e in events for p in e.get("pois", [])]
        return {
            "geometry": geometry,
            "via_points": [
                e["point"] for e in sorted(events, key=lambda e: e["index"]) if e.get("pois") or e.get("mandatory")
            ],
            "gaps": longest_visited_gaps(geometry, events, wanted),
            "breaks": breaks,
            "detours": detours,
            "detour_m": round(max(0, geometry["total_distance_m"] - path["total_distance_m"]), 1),
            "leg_m": leg_limits.meters or 0,
            "leg_seconds": leg_limits.seconds or 0,
            "limit_overruns": check_limits(geometry, breaks, day_limits, leg_limits, last_day=last_day),
        }
