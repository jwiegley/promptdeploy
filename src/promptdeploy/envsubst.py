"""Environment variable expansion for ${VAR} references."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path


_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def load_dotenv(path: Path) -> None:
    """Load KEY=value pairs from a .env file into os.environ.

    Skips blank lines and comments (#). Does not overwrite existing
    environment variables so that the real environment takes precedence.
    """
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Accept shell-style ``export KEY=value`` lines.
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # Strip optional surrounding quotes
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


def find_env_refs(data: object) -> set[str]:
    """Collect every ``${VAR}`` variable name referenced in nested data.

    Recurses through strings, dict values, and list elements; any other
    scalar (int, bool, None, ...) contributes nothing.
    """
    refs: set[str] = set()
    if isinstance(data, str):
        refs.update(_ENV_PATTERN.findall(data))
    elif isinstance(data, dict):
        for value in data.values():
            refs |= find_env_refs(value)
    elif isinstance(data, list):
        for element in data:
            refs |= find_env_refs(element)
    return refs


def read_env_example_keys(path: Path) -> set[str] | None:
    """Return the variable names declared in a ``.env``-style file.

    Mirrors the line rules of :func:`load_dotenv` (blank lines, ``#``
    comments, shell-style ``export`` prefixes, lines without ``=``)
    without touching ``os.environ``.  Returns ``None`` when the file
    does not exist so callers can distinguish "no declarations file"
    from "declares nothing".
    """
    if not path.is_file():
        return None
    keys: set[str] = set()
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            continue
        key, _, _ = line.partition("=")
        key = key.strip()
        if key:
            keys.add(key)
    return keys


def expand_env_vars(value: str) -> str:
    """Replace ${VAR} references with values from os.environ.

    Unset variables are left as literal ``${VAR}`` text, but a warning is
    printed to stderr so a typo'd or missing variable does not silently
    ship a broken config.
    """
    missing: list[str] = []

    def _replace(match: re.Match) -> str:
        var_name = match.group(1)
        resolved = os.environ.get(var_name)
        if resolved is None:
            missing.append(var_name)
            return match.group(0)
        return resolved

    result = _ENV_PATTERN.sub(_replace, value)
    if missing:
        names = ", ".join(sorted(set(missing)))
        print(
            f"WARNING: environment variable(s) not set: {names}; "
            f"leaving ${{VAR}} reference(s) unexpanded",
            file=sys.stderr,
        )
    return result


class EnvVarError(Exception):
    """Raised when a referenced ``${VAR}`` cannot be resolved."""


def expand_env_vars_strict(value: str, *, context: str = "") -> str:
    """Expand ``${VAR}`` references; raise ``EnvVarError`` if any are unset.

    Unlike :func:`expand_env_vars`, this never silently leaves a literal
    ``${VAR}`` in the output.  Use this for deploy targets that bake
    secrets into config files at deploy time -- the runtime tool will
    not expand variables itself, so a missing value would produce a
    broken config.

    ``context`` (e.g. ``"models.litellm.api_key"``) is included in the
    error message to help locate the offending reference.
    """
    missing: list[str] = []

    def _replace(match: re.Match) -> str:
        var_name = match.group(1)
        resolved = os.environ.get(var_name)
        if resolved is None:
            missing.append(var_name)
            return match.group(0)
        return resolved

    result = _ENV_PATTERN.sub(_replace, value)
    if missing:
        names = ", ".join(sorted(set(missing)))
        where = f" (in {context})" if context else ""
        raise EnvVarError(
            f"Environment variable(s) not set: {names}{where}. "
            f"Export them in your shell or add to .env before deploying."
        )
    return result
