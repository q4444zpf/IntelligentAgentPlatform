import json

from app.audit.redaction import redact_metadata, redact_summary


def test_redact_summary_escapes_html_and_truncates_stably():
    value = "<script>alert('x')</script>" + "a" * 100

    redacted = redact_summary(value, max_chars=40)

    assert redacted == "&lt;script&gt;alert(&#x27;x&#x27;)&lt..."
    assert len(redacted) == 40
    assert "<script" not in redacted


def test_redact_metadata_filters_and_recursively_redacts_without_mutation():
    value = {
        "nested": {
            "safe": "kept",
            "Authorization": "Bearer private",
            "path": r"C:\\customer\\basin\\input.csv",
        },
        "env": {"NORMAL": "visible", "TOKEN": "private"},
        "unknown": "discarded",
    }
    original = json.loads(json.dumps(value))

    redacted = redact_metadata(
        value,
        allowed_keys={"nested", "env"},
    )

    assert redacted == {
        "nested": {
            "safe": "kept",
            "Authorization": "[REDACTED]",
            "path": "input.csv",
        },
        "env": "[REDACTED]",
    }
    assert "customer" not in json.dumps(redacted)
    assert value == original


def test_redact_metadata_redacts_sensitive_top_level_keys_case_insensitively():
    redacted = redact_metadata(
        {"Token": "private", "safe": "visible"},
        allowed_keys={"Token", "safe"},
    )

    assert redacted == {"Token": "[REDACTED]", "safe": "visible"}


def test_redact_metadata_never_preserves_raw_content_even_when_allowed():
    value = {"raw_prompt": "private", "nested": {"headers": {}, "file_content": "private"}}

    redacted = redact_metadata(value, allowed_keys=value.keys())

    assert redacted["raw_prompt"] == "[REDACTED]"
    assert redacted["nested"] == {"headers": "[REDACTED]", "file_content": "[REDACTED]"}


def test_redact_metadata_limits_dictionary_key_strings():
    redacted = redact_metadata({"root": {"k" * 600: "value"}}, allowed_keys={"root"})

    assert len(next(iter(redacted["root"]))) == 512


def test_redact_metadata_applies_structural_and_string_limits():
    value = {
        "object": {f"key-{index:02}": index for index in range(60)},
        "array": list(range(30)),
        "text": "x" * 600,
        "deep": {"a": {"b": {"c": {"d": {"e": {"secret": "leak"}}}}}},
    }

    redacted = redact_metadata(value, allowed_keys=value.keys())

    assert len(redacted["object"]) == 50
    assert redacted["array"] == list(range(20))
    assert len(redacted["text"]) == 512
    assert redacted["deep"]["a"]["b"]["c"]["d"]["e"] == "[TRUNCATED]"


def test_redact_metadata_never_exceeds_serialized_byte_limit():
    redacted = redact_metadata(
        {"first": "水" * 500, "second": "z" * 500},
        allowed_keys={"first", "second"},
        max_bytes=100,
    )

    encoded = json.dumps(
        redacted, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    assert len(encoded) <= 100
    assert redacted


def test_redact_metadata_sanitizes_pathlike_values_after_string_conversion():
    from pathlib import Path, PurePosixPath, PureWindowsPath

    value = {
        "native": Path("/customer/basin/native.nc"),
        "windows": PureWindowsPath(r"C:\customer\basin\windows.nc"),
        "posix": PurePosixPath("/customer/basin/posix.nc"),
    }

    redacted = redact_metadata(value, allowed_keys=value.keys())

    assert redacted == {
        "native": "native.nc",
        "windows": "windows.nc",
        "posix": "posix.nc",
    }
    assert "customer" not in json.dumps(redacted)


def test_redact_metadata_sanitizes_absolute_path_dictionary_keys():
    from pathlib import PurePosixPath, PureWindowsPath

    windows_key = PureWindowsPath(r"C:\customer\basin\windows.nc")
    posix_key = PurePosixPath("/customer/basin/posix.nc")
    redacted = redact_metadata(
        {windows_key: "windows", posix_key: "posix"},
        allowed_keys={windows_key, posix_key},
    )

    assert redacted == {"windows.nc": "windows", "posix.nc": "posix"}


def test_path_key_sensitivity_is_checked_after_basename_sanitizing():
    from pathlib import PurePosixPath, PureWindowsPath

    windows_key = PureWindowsPath(r"C:\customer\private\token")
    posix_key = PurePosixPath("/customer/private/password")
    redacted = redact_metadata(
        {windows_key: "secret-1", posix_key: "secret-2"},
        allowed_keys={windows_key, posix_key},
    )
    assert redacted == {"token": "[REDACTED]", "password": "[REDACTED]"}


def test_sensitive_keys_are_checked_before_truncation_at_every_depth():
    sensitive_key = "a" * 512 + "password"
    redacted = redact_metadata(
        {sensitive_key: "top-secret", "nested": {sensitive_key: "nested-secret"}},
        allowed_keys={sensitive_key, "nested"},
    )

    assert redacted["a" * 512] == "[REDACTED]"
    assert redacted["nested"]["a" * 512] == "[REDACTED]"


def test_metadata_escapes_html_in_string_and_converted_values():
    class HtmlValue:
        def __str__(self):
            return '<img src=x onerror="alert(1)">'

    redacted = redact_metadata(
        {"string": "<script>alert(1)</script>", "object": HtmlValue()},
        allowed_keys={"string", "object"},
    )

    assert "<" not in redacted["string"]
    assert "<" not in redacted["object"]
