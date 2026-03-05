"""Environment variable expansion for ${VAR} references."""

from __future__ import annotations

import os
import re
from typing import Any


_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def expand_env_vars(value: str) -> str:
    """Replace ${VAR} references with values from os.environ.

    Returns the original string if the variable is not set.
    """

    def _replace(match: re.Match) -> str:
        var_name = match.group(1)
        return os.environ.get(var_name, match.group(0))

    return _ENV_PATTERN.sub(_replace, value)


def expand_env_in_dict(data: dict) -> dict:
    """Recursively expand ${VAR} references in all string values of a dict."""
    result: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, str):
            result[key] = expand_env_vars(value)
        elif isinstance(value, dict):
            result[key] = expand_env_in_dict(value)
        elif isinstance(value, list):
            result[key] = [
                expand_env_vars(v) if isinstance(v, str) else v for v in value
            ]
        else:
            result[key] = value
    return result
