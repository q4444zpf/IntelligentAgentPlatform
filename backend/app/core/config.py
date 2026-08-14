from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class RunnerTokenSettings:
    signing_key: bytes
    issuer: str = "iap-api"
    audience: str = "iap-runner-gateway"
    grace_seconds: int = 30

    @classmethod
    def from_env(cls) -> "RunnerTokenSettings":
        raw_key = os.getenv("IAP_RUNNER_TOKEN_SIGNING_KEY", "")
        signing_key = raw_key.encode("utf-8")
        if len(signing_key) < 32:
            raise ValueError("IAP_RUNNER_TOKEN_SIGNING_KEY must be at least 32 bytes")
        issuer = os.getenv("IAP_RUNNER_TOKEN_ISSUER", "iap-api").strip()
        audience = os.getenv(
            "IAP_RUNNER_TOKEN_AUDIENCE", "iap-runner-gateway"
        ).strip()
        if not issuer or not audience:
            raise ValueError("Runner token issuer and audience are required")
        grace_seconds = int(os.getenv("IAP_RUNNER_TOKEN_GRACE_SECONDS", "30"))
        if grace_seconds < 0:
            raise ValueError("IAP_RUNNER_TOKEN_GRACE_SECONDS must not be negative")
        return cls(signing_key, issuer, audience, grace_seconds)
