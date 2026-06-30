"""Template engine for remote tools — URL and body variable interpolation."""

from __future__ import annotations

import re
from typing import Any


def render_url(url_template: str, arguments: dict) -> tuple[str, dict]:
    """Replace {variable} placeholders in URL template.

    Returns (rendered_url, remaining_args) where remaining_args are
    arguments not consumed by the URL template.
    """
    consumed_keys: set[str] = set()
    pattern = re.compile(r"\{(\w+)\}")

    def replacer(match: re.Match) -> str:
        key = match.group(1)
        if key in arguments:
            consumed_keys.add(key)
            return str(arguments[key])
        return match.group(0)  # leave unreplaced

    url = pattern.sub(replacer, url_template)
    remaining = {k: v for k, v in arguments.items() if k not in consumed_keys}
    return url, remaining


def render_body(body_template: dict | list | str | Any, arguments: dict) -> Any:
    """Recursively interpolate {{variable}} in a JSON template.

    - String values: replace {{var}} with the argument value.
      If the entire string is a single {{var}} and the argument is non-string
      (e.g. array, object, number), the raw value is returned to preserve types.
    - Dict values: recurse into each value.
    - List values: recurse into each element.
    - Other types: returned as-is.
    """
    if isinstance(body_template, str):
        return _interpolate_string(body_template, arguments)
    elif isinstance(body_template, dict):
        return {k: render_body(v, arguments) for k, v in body_template.items()}
    elif isinstance(body_template, list):
        return [render_body(item, arguments) for item in body_template]
    return body_template


# Matches {{variable_name}}
_VAR_PATTERN = re.compile(r"\{\{(\w+)\}\}")
_FULL_VAR_PATTERN = re.compile(r"^\{\{(\w+)\}\}$")


def _interpolate_string(value: str, arguments: dict) -> Any:
    """Interpolate a single string value."""
    # Check if the entire string is a single variable — preserve type
    full_match = _FULL_VAR_PATTERN.match(value)
    if full_match:
        var_name = full_match.group(1)
        if var_name in arguments:
            return arguments[var_name]
        return value

    # Otherwise do string replacement
    def replacer(match: re.Match) -> str:
        var_name = match.group(1)
        if var_name in arguments:
            return str(arguments[var_name])
        return match.group(0)  # leave unreplaced

    return _VAR_PATTERN.sub(replacer, value)
