import pytest

from app.mcp.credential_resolver import (
    CredentialNotFoundError,
    CredentialScopeError,
    McpCredentialResolver,
)
from app.mcp.schemas import McpClientCreate


def test_resolves_credential_reference_for_current_unit_without_exposing_secret():
    resolver = McpCredentialResolver({"cred-1": {"unit_id": "unit-1", "headers": {"Authorization": "Bearer secret"}}})

    headers = resolver.resolve("cred-1", unit_id="unit-1")

    assert headers == {"Authorization": "Bearer secret"}
    assert "secret" not in repr(resolver)


def test_rejects_credential_from_another_unit_without_revealing_id_or_secret():
    resolver = McpCredentialResolver({"cred-1": {"unit_id": "unit-2", "headers": {"Authorization": "Bearer secret"}}})

    with pytest.raises(CredentialScopeError, match="credential is not available") as error:
        resolver.resolve("cred-1", unit_id="unit-1")
    assert "secret" not in str(error.value)
    assert "cred-1" not in str(error.value)


def test_missing_credential_is_a_safe_error():
    with pytest.raises(CredentialNotFoundError, match="credential is not available"):
        McpCredentialResolver({}).resolve("missing", unit_id="unit-1")


def test_mcp_configuration_carries_only_credential_reference():
    request = McpClientCreate.model_validate({
        "key": "water",
        "name": "Water MCP",
        "transport": "streamable_http",
        "url": "https://example.test/mcp",
        "credential_id": "cred-1",
        "headers": {},
    })
    assert request.credential_id == "cred-1"
