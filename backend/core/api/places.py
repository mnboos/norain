"""Photon place-search API."""

import os
from collections import defaultdict

import httpx
from async_lru import alru_cache
from django.http import HttpRequest
from ninja import Router

from ..schemas import CamelSchema

router = Router(tags=["Places"])


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
    places = await retrieve_places(query=query, zoom=zoom, lat=lat, lon=lon)
    return process_features_for_ambiguity(places)


@alru_cache(maxsize=32)
async def retrieve_places(*, query: str, lat: float, lon: float, zoom: float) -> list:
    assert query
    async with httpx.AsyncClient() as client:
        response = await client.get(
            os.environ.get("GEOCODER_API_URL"),
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
