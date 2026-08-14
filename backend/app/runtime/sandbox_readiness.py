from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SandboxReadiness:
    image_trusted: bool
    non_root: bool
    read_only_root: bool
    runner_gateway_network: bool
    resource_limits: bool
    cleanup_guaranteed: bool
    non_privileged: bool = True
    capabilities_dropped: bool = True
    docker_socket_absent: bool = True
    environment_allowlisted: bool = True
    mounts_allowlisted: bool = True

    def missing(self) -> list[str]:
        return [
            name for name, value in (
                ("image_trusted", self.image_trusted),
                ("non_root", self.non_root),
                ("read_only_root", self.read_only_root),
                ("runner_gateway_network", self.runner_gateway_network),
                ("resource_limits", self.resource_limits),
                ("cleanup_guaranteed", self.cleanup_guaranteed),
                ("non_privileged", self.non_privileged),
                ("capabilities_dropped", self.capabilities_dropped),
                ("docker_socket_absent", self.docker_socket_absent),
                ("environment_allowlisted", self.environment_allowlisted),
                ("mounts_allowlisted", self.mounts_allowlisted),
            ) if not value
        ]

    def is_ready(self) -> bool:
        return not self.missing()
