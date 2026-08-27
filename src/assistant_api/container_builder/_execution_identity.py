from __future__ import annotations

from pathlib import Path

from assistant_api.models import ContainerSpec

from ._errors import ConfigurationError


def validate_execution_identity_mounts(container: ContainerSpec) -> None:
    identity = container.execution_identity
    if identity is None:
        return
    for mount in container.volumes.values():
        if mount.mode == "ro":
            continue
        if mount.type == "volume":
            raise ConfigurationError(
                "writable named volumes are incompatible with container execution "
                "identity; use a caller-owned bind directory"
            )
        error = identity.writable_directory_error(Path(mount.source))
        if error:
            raise ConfigurationError(
                "writable bind ownership or writability is incompatible with "
                f"container execution identity: source={mount.source}: {error}"
            )
