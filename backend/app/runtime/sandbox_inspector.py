from __future__ import annotations

from typing import Any

from .container_policy import (
    RUNNER_BASE_ENVIRONMENT_KEYS,
    RUNNER_ENVIRONMENT_KEYS,
    RUNNER_GATEWAY_NETWORK,
)
from .sandbox_readiness import SandboxReadiness


class SandboxInspector:
    @staticmethod
    def _environment_names(config: dict[str, Any]) -> set[str]:
        raw = config.get("Env") or []
        if isinstance(raw, dict):
            return {str(key) for key in raw}
        return {
            str(item).partition("=")[0]
            for item in raw
            if isinstance(item, str) and item.partition("=")[0]
        }

    @staticmethod
    def _mounts(info: dict[str, Any], host: dict[str, Any]) -> set[tuple[str, str]]:
        mounts = {
            (str(item.get("Source", "")), str(item.get("Destination", "")))
            for item in (info.get("Mounts") or [])
            if isinstance(item, dict)
        }
        for bind in host.get("Binds") or []:
            source, separator, remainder = str(bind).partition(":")
            destination = remainder.partition(":")[0] if separator else ""
            mounts.add((source, destination))
        return mounts

    def inspect(self, info: dict[str, Any]) -> SandboxReadiness:
        config = info.get("Config") or {}
        host = info.get("HostConfig") or {}
        labels = (config.get("Labels") or info.get("Labels") or {})
        image = str(config.get("Image", ""))
        user = str(config.get("User", ""))
        cap_drop = {str(value).upper() for value in (host.get("CapDrop") or [])}
        memory = int(host.get("Memory") or 0)
        pids = host.get("PidsLimit")
        nano_cpus = int(host.get("NanoCpus") or 0)
        networks = set(
            ((info.get("NetworkSettings") or {}).get("Networks") or {}).keys()
        )
        mounts = self._mounts(info, host)
        docker_socket_absent = all(
            "/var/run/docker.sock" not in {source, destination}
            for source, destination in mounts
        )
        mounts_allowlisted = (
            len(mounts) == 1
            and next(iter(mounts))[0].startswith("/workspace/")
            and next(iter(mounts))[1] == "/workspace"
        )
        environment_names = self._environment_names(config)
        return SandboxReadiness(
            image_trusted=image.startswith("iap/"),
            non_root=user not in {"", "0", "root"},
            read_only_root=bool(config.get("ReadonlyRootfs") or host.get("ReadonlyRootfs")),
            runner_gateway_network=(
                str(host.get("NetworkMode", "")) == RUNNER_GATEWAY_NETWORK
                and networks == {RUNNER_GATEWAY_NETWORK}
            ),
            resource_limits=memory > 0 and isinstance(pids, int) and pids > 0 and nano_cpus > 0,
            cleanup_guaranteed=str(labels.get("iap.cleanup_guaranteed", "")).lower() == "true",
            non_privileged=host.get("Privileged") is False,
            capabilities_dropped="ALL" in cap_drop,
            docker_socket_absent=docker_socket_absent,
            environment_allowlisted=(
                RUNNER_ENVIRONMENT_KEYS <= environment_names
                and environment_names
                <= RUNNER_ENVIRONMENT_KEYS | RUNNER_BASE_ENVIRONMENT_KEYS
            ),
            mounts_allowlisted=mounts_allowlisted,
        )
