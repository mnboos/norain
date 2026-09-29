"""Road preferences a journey asks for, as a GraphHopper request custom model.

GraphHopper runs with LM (landmarks) and no CH, so a request may bring its own custom model,
but LM only stays correct when that model makes edges *more* expensive, never cheaper. Every
preference here is therefore a penalty (``multiply_by`` <= 1 on ``priority``). "Prefer the
cycle network" cannot be a boost, so it is written, and named, as what it does: roads off the
network cost more (``avoid_off_network``). For the hike profile the network is the hiking one
(``foot_network``).

The encoded values used here must be in ``graph.encoded_values``
(data/graphhopper/graphhopper-config.yaml): surface, road_class, average_slope and
bike_network were there already; urban_density was added for journeys, foot_network for hikes.
Ferries are ``road_environment == FERRY``; the bike filter keeps ``route=ferry`` ways.
"""

import json
from dataclasses import asdict, dataclass
from typing import Literal

Surface = Literal["any", "avoid_unpaved", "paved_only"]
# "avoid": flat preferred, climbs cost more. "hilly": hills preferred, so flat roads cost more;
# a boost for climbs would break LM, a penalty on the flat does the same job.
Climbing = Literal["neutral", "avoid", "hilly"]
Traffic = Literal["neutral", "avoid_main", "avoid_off_network"]
Towns = Literal["neutral", "avoid"]
# A strong penalty, not an exclusion: a ride that cannot go round by land still plans.
Ferries = Literal["neutral", "avoid"]

UNPAVED = ("UNPAVED", "COMPACTED", "FINE_GRAVEL", "GRAVEL", "GROUND", "DIRT", "GRASS", "SAND")


@dataclass(frozen=True)
class RoadPrefs:
    surface: Surface = "any"
    climbing: Climbing = "neutral"
    traffic: Traffic = "neutral"
    towns: Towns = "neutral"
    ferries: Ferries = "neutral"

    @classmethod
    def from_json(cls, data: dict | None) -> RoadPrefs:
        data = data or {}
        return cls(**{k: data[k] for k in ("surface", "climbing", "traffic", "towns", "ferries") if k in data})

    def as_json(self) -> dict:
        return asdict(self)


def _either(field: str, values: tuple[str, ...]) -> str:
    return " || ".join(f"{field} == {value}" for value in values)


def road_prefs_model(prefs: RoadPrefs, profile: str = "bike") -> dict:
    """The penalty-only custom model for these preferences; ``{}`` when there are none."""
    priority: list[dict] = []
    if prefs.surface == "avoid_unpaved":
        priority.append({"if": _either("surface", UNPAVED), "multiply_by": "0.5"})
    elif prefs.surface == "paved_only":
        priority.append({"if": _either("surface", UNPAVED), "multiply_by": "0.1"})
    if prefs.climbing == "avoid":
        # average_slope is signed in the direction of travel: only climbing costs more.
        priority.append({"if": "average_slope > 6", "multiply_by": "0.4"})
        priority.append({"else_if": "average_slope > 3", "multiply_by": "0.7"})
    elif prefs.climbing == "hilly":
        # Both directions: a hilly ride climbs and descends, and a loop has to do both.
        priority.append({"if": "average_slope < 2 && average_slope > -2", "multiply_by": "0.6"})
        priority.append({"else_if": "average_slope < 4 && average_slope > -4", "multiply_by": "0.85"})
    if prefs.traffic == "avoid_main":
        priority.append({"if": "road_class == TRUNK || road_class == PRIMARY", "multiply_by": "0.3"})
        priority.append({"else_if": "road_class == SECONDARY", "multiply_by": "0.6"})
    elif prefs.traffic == "avoid_off_network":
        network = "foot_network" if profile == "hike" else "bike_network"
        priority.append({"if": f"{network} == MISSING", "multiply_by": "0.7"})
    if prefs.towns == "avoid":
        priority.append({"if": "urban_density == CITY", "multiply_by": "0.5"})
        priority.append({"else_if": "urban_density == RESIDENTIAL", "multiply_by": "0.8"})
    if prefs.ferries == "avoid":
        priority.append({"if": "road_environment == FERRY", "multiply_by": "0.05"})
    return {"priority": priority} if priority else {}


def merge_models(*models: dict) -> dict:
    """One custom model from several: priority statements in order, areas side by side.

    Each part starts its own ``if`` chain, so concatenating keeps every part's meaning. Area
    ids must be unique across the parts; each part prefixes its own.
    """
    priority: list[dict] = []
    features: list[dict] = []
    for model in models:
        priority.extend(model.get("priority", ()))
        features.extend((model.get("areas") or {}).get("features", ()))
    merged: dict = {}
    if priority:
        merged["priority"] = priority
    if features:
        merged["areas"] = {"type": "FeatureCollection", "features": features}
    return merged


def is_penalty_only(model: dict) -> bool:
    """Whether every statement only raises weights, the condition LM needs."""
    for statement in [*model.get("priority", ()), *model.get("speed", ())]:
        if "multiply_by" in statement and float(statement["multiply_by"]) > 1:
            return False
        if "limit_to" in statement:
            continue
        if not {"multiply_by", "limit_to"} & statement.keys():
            return False
    return not model.get("distance_influence")


def model_key(model: dict | None) -> str:
    """Canonical JSON: the hashable form the routing LRU keys on."""
    return json.dumps(model or {}, sort_keys=True, separators=(",", ":"))
