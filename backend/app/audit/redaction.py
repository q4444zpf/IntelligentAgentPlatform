import html
import json
import ntpath
import posixpath
import re
from collections.abc import Collection, Mapping
from typing import Any


_REDACTED = "[REDACTED]"
_TRUNCATED = "[TRUNCATED]"
_SENSITIVE_KEYS = {
    "api_key", "authorization", "token", "password", "secret", "cookie",
}
_WINDOWS_ABSOLUTE_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")


def _is_sensitive_key(value: str) -> bool:
    normalized = value.casefold()
    return (
        normalized == "env"
        or normalized in _SENSITIVE_KEYS
        or any(marker in normalized for marker in ("prompt", "response", "header", "file_content"))
    )


def redact_summary(value: str, *, max_chars: int = 500) -> str:
    if max_chars < 0:
        raise ValueError("max_chars must be non-negative")
    escaped = html.escape(str(value), quote=True)
    if len(escaped) <= max_chars:
        return escaped
    if max_chars <= 3:
        return "." * max_chars
    return escaped[: max_chars - 3] + "..."


def _basename_if_absolute(value: str) -> str:
    if _WINDOWS_ABSOLUTE_PATH.match(value):
        return ntpath.basename(value.rstrip("\\/"))
    if value.startswith("/"):
        return posixpath.basename(value.rstrip("/"))
    return value


def _sanitize(value: Any, *, depth: int) -> Any:
    if depth > 5:
        return _TRUNCATED
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in list(value.items())[:50]:
            string_key = str(key)[:512]
            normalized_key = string_key.casefold()
            if _is_sensitive_key(normalized_key):
                result[string_key] = _REDACTED
            else:
                result[string_key] = _sanitize(item, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        return [_sanitize(item, depth=depth + 1) for item in value[:20]]
    if isinstance(value, str):
        return _basename_if_absolute(value)[:512]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:512]


def _encoded_size(value: Mapping[str, Any]) -> int:
    return len(json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
    ).encode("utf-8"))


def _string_locations(value: Any, path: tuple[Any, ...] = ()):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _string_locations(item, path + (key,))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _string_locations(item, path + (index,))
    elif isinstance(value, str) and value and value not in {_REDACTED, _TRUNCATED}:
        yield path, value


def _replace_at_path(value: Any, path: tuple[Any, ...], replacement: str) -> None:
    target = value
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = replacement


def _remove_last_container_item(value: Any) -> bool:
    if isinstance(value, dict):
        for item in reversed(list(value.values())):
            if _remove_last_container_item(item):
                return True
        if value:
            value.pop(next(reversed(value)))
            return True
    elif isinstance(value, list):
        for item in reversed(value):
            if _remove_last_container_item(item):
                return True
        if value:
            value.pop()
            return True
    return False


def redact_metadata(
    value: Mapping[str, Any],
    *,
    allowed_keys: Collection[str],
    max_bytes: int = 4096,
) -> dict[str, Any]:
    if max_bytes < 2:
        raise ValueError("max_bytes must be at least 2")
    allowed = set(allowed_keys)
    result = {
        str(key)[:512]: (
            _REDACTED
            if _is_sensitive_key(str(key))
            else _sanitize(item, depth=1)
        )
        for key, item in value.items()
        if key in allowed
    }
    while _encoded_size(result) > max_bytes:
        strings = list(_string_locations(result))
        if strings:
            path, current = max(
                strings, key=lambda item: len(item[1].encode("utf-8"))
            )
            encoded = current.encode("utf-8")
            shortened = encoded[: max(0, len(encoded) // 2)].decode(
                "utf-8", "ignore"
            )
            _replace_at_path(result, path, shortened)
            continue
        if not _remove_last_container_item(result):
            return {}
    return result
