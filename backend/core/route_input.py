"""Shared validation for planner and imported route geometry."""

from enum import StrEnum

from pydantic import Field, field_validator, model_validator

from .gpx import MAX_DURATION_SECONDS, validate_track
from .schemas import CamelSchema


class GeometrySource(StrEnum):
    GRAPHHOPPER = "graphhopper"
    IMPORTED = "imported"


class RoutingProfile(StrEnum):
    BIKE = "bike"
    EBIKE = "ebike"
    FAST_EBIKE = "fast_ebike"


class RoutePlanIn(CamelSchema):
    name: str = Field(default="NoRain", max_length=200)
    geometry_source: GeometrySource = GeometrySource.GRAPHHOPPER
    coordinates: list[list[float]] = Field(min_length=2, max_length=100000)
    duration_seconds: int | None = Field(default=None, ge=1, le=MAX_DURATION_SECONDS)
    profile: RoutingProfile = RoutingProfile.BIKE

    _coordinates = field_validator("coordinates")(validate_track)

    @model_validator(mode="after")
    def check_shape(self):
        if self.geometry_source == "imported" and self.duration_seconds is None:
            raise ValueError("Bitte eine Fahrzeit angeben.")
        if self.geometry_source == "graphhopper":
            if len(self.coordinates) > 17:
                raise ValueError("Höchstens 15 Zwischenpunkte sind erlaubt.")
            self.coordinates = [p[:2] for p in self.coordinates]
        return self
