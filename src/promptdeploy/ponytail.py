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
STRICT_CANONICAL_INSTRUCTIONS_TRANSFORM = "strict-canonical-instructions-v1"
ONE_SHOT_REVIEW_TRANSFORM = "one-shot-review-v1"
CLAUDE_CODEX_RUNTIME_PAYLOAD = "claude-codex-runtime-v1"
OPENCODE_PLUGIN_PAYLOAD = "opencode-plugin-v1"
CLAUDE_CODEX_RUNTIME_TREE_SHA256 = (
    "sha256:a2f4bbac93ba0359f7325621b1a7c7fb049c5b1244c21d9c0c37a89b47bc9894"
)
OPENCODE_PLUGIN_TREE_SHA256 = (
    "sha256:70becde0867bbe3f293b28a56744e60950c62b8758cf837dfeb82f780d29a15b"
)
