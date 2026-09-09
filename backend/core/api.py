import os
from collections import defaultdict

from async_lru import alru_cache
from django.http import HttpRequest
from ninja import NinjaAPI
import httpx

from .routes_api import router as routes_router
from .schemas import PlacesSearchResult
from .weather import router as weather_router

api = NinjaAPI()
api.add_router("", weather_router)
api.add_router("", routes_router)


def process_features_for_ambiguity(features: list[dict]) -> list[dict]:
    """
    Analyzes a list of Photon features to determine if the canton
    should be shown to resolve ambiguity.

    This function adds a `show_canton: bool` flag to each feature's
    properties.
    """
    if not features:
        return []

    # Step 1: Group all features by their city name.
    city_groups = defaultdict(list)
    for feature in features:
        properties = feature.get("properties", {})
        # Use the city, but fall back to the feature's name if city is missing.
        city_name = properties.get("city") or properties.get("name")
        if city_name:
            city_groups[city_name].append(feature)

    # Step 2: Identify which city names are ambiguous (appear in multiple cantons).
    ambiguous_city_names = set()
    for city_name, features_in_group in city_groups.items():
        # Create a set of unique canton names for this city group.
        # We discard None in case a feature is missing a state.
        cantons_in_group = {f.get("properties", {}).get("state") for f in features_in_group}
        cantons_in_group.discard(None)

        if len(cantons_in_group) > 1:
            ambiguous_city_names.add(city_name)

    # Step 3: Augment the original features with the `show_canton` flag.
    for feature in features:
        properties = feature.get("properties", {})
        city_name = properties.get("city") or properties.get("name")

        # The canton should be shown if its city name is in our ambiguous set.
        show_canton = city_name in ambiguous_city_names
        properties["show_canton"] = show_canton

    return features


@api.get("/search", response=list[PlacesSearchResult])
async def search(request: HttpRequest, query: str, zoom: float, lat: float, lon: float):
    raw_places = await retrieve_places(query=query, zoom=zoom, lat=lat, lon=lon)
    return process_features_for_ambiguity(raw_places)


@alru_cache(maxsize=32)
async def retrieve_places(*, query: str, lat: float, lon: float, zoom: float) -> list:
    assert query
    async with httpx.AsyncClient() as client:
        url = os.environ.get("GEOCODER_API_URL")
        resp = await client.get(
            url,
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

    if resp.status_code != 200:
        raise Exception(f"Error retrieving places: {resp.text}")

    data = resp.raise_for_status().json()
    return data.get("features", [])
