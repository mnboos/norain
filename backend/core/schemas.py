from ninja import Schema
from pydantic import ConfigDict
from pydantic.alias_generators import to_camel


class CamelSchema(Schema):
    """Base schema that emits camelCase in JSON/OpenAPI (no dual snake_case/camelCase in generated client)."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )
