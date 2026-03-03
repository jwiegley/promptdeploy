from .base import Target
from .claude import ClaudeTarget
from .droid import DroidTarget
from .opencode import OpenCodeTarget


def create_target(target_config):
    """Create a Target instance from a TargetConfig."""
    from .claude import ClaudeTarget
    from .droid import DroidTarget
    from .opencode import OpenCodeTarget

    factories = {
        "claude": lambda tc: ClaudeTarget(tc.id, tc.path),
        "droid": lambda tc: DroidTarget(tc.id, tc.path),
        "opencode": lambda tc: OpenCodeTarget(tc.id, tc.path),
    }
    factory = factories.get(target_config.type)
    if factory is None:
        raise ValueError(f"Unknown target type: {target_config.type}")
    return factory(target_config)


__all__ = ["Target", "ClaudeTarget", "DroidTarget", "OpenCodeTarget", "create_target"]
