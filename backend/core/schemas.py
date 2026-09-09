from pydantic import ConfigDict
from pydantic.alias_generators import to_camel
from ninja import Schema


class CamelSchema(Schema):
    """Base schema that emits camelCase in JSON/OpenAPI (no dual snake_case/camelCase in generated client)."""

    model_config = ConfigDict(

        alias_generator=to_camel,
        populate_by_name=True,
    )


class GeometrySchema(CamelSchema):
    """Defines the geographical point of the feature."""

    type: str = "Point"
    coordinates: list[float]  # [longitude, latitude]


class PropertiesSchema(CamelSchema):
    """Defines the descriptive properties of a feature."""

    name: str
    city: str | None = None
    state: str = None
    countrycode: str = None
    show_canton: bool


class PlacesSearchResult(CamelSchema):
    """Represents a single, augmented search result feature."""

    type: str = "Feature"
    properties: PropertiesSchema
    geometry: GeometrySchema
