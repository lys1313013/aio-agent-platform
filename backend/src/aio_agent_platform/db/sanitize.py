"""Strip characters Postgres JSONB/text columns cannot store.

Postgres text (and therefore JSONB) cannot hold the NUL byte ``\\u0000``; an
INSERT carrying one fails with asyncpg's ``UntranslatableCharacterError`` and
rolls back the whole row. Binary file content (e.g. PDF bytes read by file
tools) routinely leaks NUL bytes into tool results, so we scrub values on the
way into the DB.
"""

from __future__ import annotations

from typing import Any

_NUL = "\x00"


def sanitize_pg_text(value: Any) -> Any:
    """Recursively remove NUL bytes from strings within a JSON-like value.

    Walks dicts, lists, and tuples; leaves non-string scalars untouched. Returns
    a new structure (does not mutate the input).
    """
    if isinstance(value, str):
        return value.replace(_NUL, "") if _NUL in value else value
    if isinstance(value, dict):
        return {k: sanitize_pg_text(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_pg_text(v) for v in value]
    return value
