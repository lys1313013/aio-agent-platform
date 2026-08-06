"""Generic ORM round-trip test.

For every model, build a fully-populated row (a sentinel value per column type),
insert it, read it back via a full-entity ``select(Model)``, and assert every
column round-trips (non-None). This is the core guarantee that a full-model
query returns every field the model declares — a column dropped from the model
but still selected, or a model column missing from the database (missing
migration), surfaces here automatically. New models/columns are covered without
any per-model test code.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import BigInteger, Boolean, Date, DateTime, Float, Integer, Numeric, String, Text
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.db.models import Base


def _all_models():
    seen = set()
    for mapper in Base.registry.mappers:
        cls = mapper.entity
        if cls is not None and getattr(cls, "__tablename__", None) and cls not in seen:
            seen.add(cls)
            yield cls


MODELS = list(_all_models())


def _sentinel(col):
    t = col.type
    if isinstance(t, PG_UUID):
        return uuid.uuid4()
    if isinstance(t, String):
        return f"t-{col.name}"[: (t.length or 128)]
    if isinstance(t, Text):
        return f"txt-{col.name}"
    if isinstance(t, Boolean):
        return True
    if isinstance(t, (Integer, BigInteger)):
        return 7
    if isinstance(t, (Float, Numeric)):
        return Decimal("1.5")
    if isinstance(t, Date):
        return date(2020, 1, 1)
    if isinstance(t, DateTime):
        return datetime(2020, 1, 1)
    if isinstance(t, JSONB):
        return {"t": col.name}
    raise TypeError(f"round-trip test has no sentinel for column {col.table.name}.{col.name}: {t}")


@pytest.mark.parametrize("model", MODELS, ids=[m.__name__ for m in MODELS])
@pytest.mark.asyncio
async def test_model_full_roundtrip(db_session: AsyncSession, model):
    """A full-entity select must return every column of the model, populated."""
    obj = model()
    # Database-generated PKs (autoincrement Integer/BigInteger) are left to the DB.
    # All other PKs (UUID with/without default, string, composite) get an explicit
    # sentinel so the insert carries every column.
    autogen = {
        col.name
        for col in model.__table__.primary_key.columns
        if isinstance(col.type, (Integer, BigInteger))
    }
    for col in model.__table__.columns:
        if col.key in autogen or col.name in autogen:
            continue
        setattr(obj, col.key, _sentinel(col))
    db_session.add(obj)
    await db_session.flush()

    pk = {col.key: getattr(obj, col.key) for col in model.__table__.primary_key.columns}
    row = (
        await db_session.execute(
            select(model).where(*[getattr(model, key) == val for key, val in pk.items()])
        )
    ).scalar_one()

    missing = [col.key for col in model.__table__.columns if getattr(row, col.key) is None]
    assert not missing, (
        f"{model.__name__} full-entity query returned NULL for columns: {missing}. "
        "The column was not persisted/loaded — check the model vs database schema."
    )
