import tempfile
from pathlib import Path

from .base import Target
from .claude import ClaudeTarget
from .droid import DroidTarget
from .gptel import GptelTarget
from .opencode import OpenCodeTarget
from .remote import RemoteTarget


def create_target(target_config, *, global_model=None):
    """Create a Target instance from a TargetConfig.

    When the config has a ``host`` field that does not match the current
    machine's hostname, the inner target operates on a local staging
    directory and is wrapped in :class:`RemoteTarget` which handles
    rsync-based sync to/from the remote host.  When ``host`` matches the
    current machine, the target is treated as local so the same
    ``deploy.yaml`` works correctly when run from any host in the fleet.

    For ``claude``-type targets, ``global_model`` supplies the default model
    to inject into deployed agents and skills when the target does not have
    its own ``model`` override. The per-target ``TargetConfig.model`` wins
    when both are set. ``None`` disables injection entirely.
    """
    from ..config import current_host
    from .claude import ClaudeTarget
    from .droid import DroidTarget
    from .gptel import GptelTarget
    from .opencode import OpenCodeTarget

    is_remote = target_config.host is not None and target_config.host != current_host()
    if is_remote:
        staging_path = Path(
            tempfile.mkdtemp(prefix=f"promptdeploy-{target_config.id}-")
        )
    else:
        staging_path = target_config.path

    effective_model = target_config.model or global_model

    factories = {
        "claude": lambda tc, p: ClaudeTarget(tc.id, p, model=effective_model),
        "droid": lambda tc, p: DroidTarget(tc.id, p),
        "opencode": lambda tc, p: OpenCodeTarget(tc.id, p),
        "gptel": lambda tc, p: GptelTarget(tc.id, p),
    }
    factory = factories.get(target_config.type)
    if factory is None:
        raise ValueError(f"Unknown target type: {target_config.type}")

    inner = factory(target_config, staging_path)

    if is_remote:
        assert target_config.host is not None  # narrowed by is_remote check
        return RemoteTarget(inner, target_config.host, target_config.path, staging_path)

    return inner


__all__ = [
    "Target",
    "ClaudeTarget",
    "DroidTarget",
    "GptelTarget",
    "OpenCodeTarget",
    "RemoteTarget",
    "create_target",
]
