from __future__ import annotations

from copy import deepcopy
from typing import Any


class CredentialNotFoundError(Exception):
    def __init__(self) -> None:
        super().__init__("credential is not available")


class CredentialScopeError(Exception):
    def __init__(self) -> None:
        super().__init__("credential is not available")


class McpCredentialResolver:
    def __init__(self, records: dict[str, dict[str, Any]]):
        self._records = records

    def __repr__(self) -> str:
        return "McpCredentialResolver(<redacted>)"

    def resolve(self, credential_id: str, *, unit_id: str) -> dict[str, str]:
        record = self._records.get(credential_id)
        if record is None:
            raise CredentialNotFoundError()
        if record.get("unit_id") != unit_id:
            raise CredentialScopeError()
        headers = record.get("headers")
        if not isinstance(headers, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in headers.items()):
            raise CredentialNotFoundError()
        return deepcopy(headers)
