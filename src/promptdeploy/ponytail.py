"""Pinned Ponytail adapter contract shared by catalog and transforms."""

from __future__ import annotations

PONYTAIL_REVISION = "16f29800fd2681bdf24f3eb4ccffe38be3baec6b"
PONYTAIL_VERSION = "4.8.4"
PONYTAIL_NAMES = (
    "ponytail",
    "ponytail-review",
    "ponytail-audit",
    "ponytail-debt",
    "ponytail-gain",
    "ponytail-help",
)

# OpenCode is deliberately absent: its final integration is owned by the
# later native bundle/plugin slice, which must not coexist with generic skills.
PONYTAIL_SKILL_TARGET_TYPES = frozenset({"claude", "codex", "droid"})
PONYTAIL_PROMPT_TARGET_TYPES = frozenset({"gptel"})
PONYTAIL_ALL_TARGET_TYPES = frozenset({"claude", "codex", "droid", "opencode", "gptel"})
GPTEL_PRESET_TRANSFORM = "gptel-preset-v1"
