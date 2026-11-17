"""Fixed-point quota units; supplier prices and physical page counts are separate."""
from decimal import Decimal
from typing import Annotated
from pydantic import BeforeValidator, Field, PlainSerializer
from sqlalchemy import Numeric

QUOTA_TYPE = Numeric(18, 4)
MIN_QUOTA = Decimal('0.0001')


def quota_number(value):
    # Preserve existing integer request fingerprints when introducing decimal inputs.
    return int(value) if value == int(value) else float(value)


def numeric_quota(value):
    if type(value) not in (int, float, Decimal):
        raise ValueError('Quota must be a number')
    return value


def quota_schema(schema):
    schema.update(next(item for item in schema.pop('anyOf') if item['type'] == 'number'), multipleOf=0.0001)


QuotaAmount = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=4, json_schema_extra=quota_schema),
                        BeforeValidator(numeric_quota),
                        PlainSerializer(quota_number, return_type=int | float, when_used='json')]
