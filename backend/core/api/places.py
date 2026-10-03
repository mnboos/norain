"""Photon place-search API."""

import os
from collections import defaultdict
from time import perf_counter

import httpx
from asgiref.sync import sync_to_async
from async_lru import alru_cache
from django.http import HttpRequest
from django.utils.translation import gettext
from ninja import Router
from ninja.errors import HttpError

from .. import telemetry
from ..auth.backend import optional_session_auth
from ..schemas import CamelSchema
from .gpx import limit_request

# Place search serves the planner, which plans routes without weather for visitors too.
router = Router(auth=optional_session_auth, tags=["Places"])

# Search runs as the user types, so this is per keystroke burst, not per place picked.
SEARCH_LIMIT_PER_MINUTE = 120


class GeometrySchema(CamelSchema):
    type: str = "Point"
    coordinates: list[float]


class PropertiesSchema(CamelSchema):
    name: str
    city: str | None = None
    state: str | None = None
    countrycode: str | None = None
    show_canton: bool


class PlacesSearchResult(CamelSchema):
    type: str = "Feature"
    properties: PropertiesSchema
    geometry: GeometrySchema


def process_features_for_ambiguity(features: list[dict]) -> list[dict]:
    """Mark place names that need a canton to disambiguate them."""
    city_groups = defaultdict(list)
    for feature in features:
        properties = feature.get("properties", {})
        city_name = properties.get("city") or properties.get("name")
        if city_name:
            city_groups[city_name].append(feature)

    ambiguous_names = {
        city_name
        for city_name, matches in city_groups.items()
        if len({match.get("properties", {}).get("state") for match in matches} - {None}) > 1
    }
    for feature in features:
        properties = feature.get("properties", {})
        city_name = properties.get("city") or properties.get("name")
        properties["show_canton"] = city_name in ambiguous_names
    return features


@router.get("/search", response=list[PlacesSearchResult])
async def search(request: HttpRequest, query: str, zoom: float, lat: float, lon: float):
    limit_request(request, "search", SEARCH_LIMIT_PER_MINUTE)
    started, outcome = perf_counter(), "success"
    user = await request.auser()
    context = {
        **await sync_to_async(telemetry.user_context)(user),
        "search.query": query,
        "search.lat": lat,
        "search.lon": lon,
        "search.zoom": zoom,
    }
    try:
        places = await retrieve_places(query=query, zoom=zoom, lat=lat, lon=lon)
        outcome = "success" if places else "empty"
        telemetry.emit("distribution", "search.results", len(places), **context)
        return process_features_for_ambiguity(places)
    except Exception:
        outcome = "error"
        raise
    finally:
        telemetry.event("search.completed", outcome=outcome, **context)
        telemetry.emit(
            "distribution", "search.duration", perf_counter() - started, unit="second", outcome=outcome, **context
        )


@router.get("/reverse", response=PlacesSearchResult)
async def reverse(request: HttpRequest, lat: float, lon: float):
    """The place at a point, for "current location": a name to show and save instead of coordinates."""
    limit_request(request, "reverse")
    features = await retrieve_reverse(lat=round(lat, 5), lon=round(lon, 5))
    if not features:
        raise HttpError(404, gettext("Kein Ort an dieser Stelle."))
    properties = features[0].get("properties", {})
    street = " ".join(p for p in (properties.get("street"), properties.get("housenumber")) if p)
    name = properties.get("name") or street or properties.get("city")
    if not name:
        raise HttpError(404, gettext("Kein Ort an dieser Stelle."))
    # The point the user stands on, not the address Photon snapped it to.
    return {
        "properties": {**properties, "name": name, "show_canton": False},
        "geometry": {"coordinates": [lon, lat]},
    }


def _geocoder_url() -> str:
    # Required, with no default: without it there is nothing to search against, and the
    # named error is far easier to act on than whatever httpx makes of None.
    geocoder_url = os.environ.get("GEOCODER_API_URL")
    if not geocoder_url:
        raise RuntimeError("GEOCODER_API_URL is not set; place search is unavailable.")
    return geocoder_url


def reverse_url(geocoder_url: str) -> str:
    """Photon serves reverse geocoding at /reverse, beside the /api search endpoint."""
    return geocoder_url.rstrip("/").removesuffix("/api") + "/reverse"


@alru_cache(maxsize=32)
@telemetry.provider("photon")
async def retrieve_reverse(*, lat: float, lon: float) -> list:
    async with httpx.AsyncClient() as client:
        response = await client.get(
            reverse_url(_geocoder_url()), params={"lat": lat, "lon": lon, "limit": 1}, timeout=30
        )
    response.raise_for_status()
    return response.json().get("features", [])


@alru_cache(maxsize=32)
@telemetry.provider("photon")
async def retrieve_places(*, query: str, lat: float, lon: float, zoom: float) -> list:
    assert query
    geocoder_url = _geocoder_url()
    async with httpx.AsyncClient() as client:
        response = await client.get(
            geocoder_url,
            params={
                "q": query,
                "limit": 5,
                "lat": lat,
                "lon": lon,
                "location_bias_scale": 0.2,
                "zoom": round(zoom),
                "layer": ["city", "locality"],
            },
            timeout=30000,
        )
    response.raise_for_status()
    return response.json().get("features", [])
