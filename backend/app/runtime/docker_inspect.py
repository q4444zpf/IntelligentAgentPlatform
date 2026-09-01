from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class DockerInspectTransport:
    client: Any

    def inspect(self, container_name: str) -> dict[str, Any] | None:
        try:
            container = self.client.containers.get(container_name)
            attrs = getattr(container, "attrs", None)
            return dict(attrs) if isinstance(attrs, dict) else None
        except Exception:
            return None
