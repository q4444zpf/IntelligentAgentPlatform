from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from threading import Event, Thread
from typing import Any

from jsonschema import Draft202012Validator, SchemaError

from .execution_snapshot import SnapshotSkill

MAX_SCRIPT_OUTPUT_BYTES = 1_048_576


class SkillScriptError(ValueError):
    """A declared Skill script is invalid or failed its bounded protocol."""


@dataclass(frozen=True)
class SkillScriptSpec:
    skill_name: str
    name: str
    path: str
    input_schema: dict[str, object]
    output_schema: dict[str, object]
    timeout_seconds: int
    requires_approval: bool

    @property
    def tool_name(self) -> str:
        return f"skill.{self.skill_name}.script.{self.name}"


def _validate_schema(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict) or value.get("type") != "object":
        raise SkillScriptError(f"{field} schema must be an object schema")
    try:
        Draft202012Validator.check_schema(value)
    except SchemaError as error:
        raise SkillScriptError(f"{field} schema is invalid") from error
    return dict(value)


def _validate_path(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise SkillScriptError("script path must be relative")
    path = PurePosixPath(value)
    if path.is_absolute() or re.match(r"^[A-Za-z]:", value) or ".." in path.parts or path.as_posix() != value:
        raise SkillScriptError("script path must be relative")
    return value


def load_script_specs(skill: SnapshotSkill) -> tuple[SkillScriptSpec, ...]:
    declarations = skill.metadata.get("scripts", [])
    if declarations is None:
        return ()
    if not isinstance(declarations, list):
        raise SkillScriptError("scripts must be a list")
    specs: list[SkillScriptSpec] = []
    names: set[str] = set()
    for declaration in declarations:
        if not isinstance(declaration, dict):
            raise SkillScriptError("script declaration must be an object")
        allowed = {"name", "path", "input_schema", "output_schema", "timeout_seconds", "requires_approval"}
        unknown = set(declaration) - allowed
        if unknown:
            key = sorted(unknown)[0]
            raise SkillScriptError(f"script declaration field {key!r} is not allowed (no command field)")
        name = declaration.get("name")
        if not isinstance(name, str) or not name or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in name):
            raise SkillScriptError("script name is invalid")
        if name in names:
            raise SkillScriptError("duplicate script name")
        names.add(name)
        timeout = declaration.get("timeout_seconds")
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 120:
            raise SkillScriptError("script timeout must be between 1 and 120 seconds")
        requires_approval = declaration.get("requires_approval", False)
        if not isinstance(requires_approval, bool):
            raise SkillScriptError("requires_approval must be boolean")
        specs.append(SkillScriptSpec(
            skill_name=skill.name,
            name=name,
            path=_validate_path(declaration.get("path")),
            input_schema=_validate_schema(declaration.get("input_schema"), "input"),
            output_schema=_validate_schema(declaration.get("output_schema"), "output"),
            timeout_seconds=timeout,
            requires_approval=requires_approval,
        ))
    return tuple(specs)


def _validate_instance(schema: dict[str, object], value: object, field: str) -> None:
    errors = sorted(Draft202012Validator(schema).iter_errors(value), key=lambda item: item.path)
    if errors:
        raise SkillScriptError(f"{field} JSON does not match schema")


def execute_script(
    spec: SkillScriptSpec,
    root: Path,
    arguments: dict[str, object],
    *,
    cancel_event: Event | None = None,
    deadline_monotonic: float | None = None,
) -> dict[str, object]:
    if cancel_event is not None and cancel_event.is_set():
        raise SkillScriptError("script cancelled")
    _validate_instance(spec.input_schema, arguments, "input")
    root_resolved = root.resolve()
    script_path = (root_resolved / PurePosixPath(spec.path)).resolve()
    try:
        script_path.relative_to(root_resolved)
    except ValueError as error:
        raise SkillScriptError("script path escapes Skill root") from error
    if not script_path.is_file():
        raise SkillScriptError("script path is not a regular file")
    env = {
        "PATH": os.defpath,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUNBUFFERED": "1",
    }
    output_file = tempfile.TemporaryFile()
    error_file = tempfile.TemporaryFile()
    process = subprocess.Popen(
        [sys.executable, str(script_path)], cwd=str(root_resolved), stdin=subprocess.PIPE,
        stdout=output_file, stderr=error_file, env=env, shell=False,
    )
    payload = json.dumps(arguments, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    deadline = time.monotonic() + spec.timeout_seconds
    if deadline_monotonic is not None:
        deadline = min(deadline, deadline_monotonic)
    cancelled = Event()
    overflow = Event()
    def watch_process() -> None:
        while process.poll() is None:
            if cancel_event is not None and cancel_event.is_set():
                cancelled.set()
                process.kill()
                return
            if os.fstat(output_file.fileno()).st_size > MAX_SCRIPT_OUTPUT_BYTES or os.fstat(error_file.fileno()).st_size > MAX_SCRIPT_OUTPUT_BYTES:
                overflow.set()
                process.kill()
                return
            time.sleep(0.01)
    watcher = Thread(target=watch_process, daemon=True)
    watcher.start()
    try:
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(str(script_path), 0)
            process.communicate(input=payload, timeout=remaining)
        except subprocess.TimeoutExpired as error:
            process.kill()
            process.communicate()
            raise SkillScriptError("script timeout") from error
        if cancelled.is_set() or (cancel_event is not None and cancel_event.is_set()):
            raise SkillScriptError("script cancelled")
        if overflow.is_set():
            raise SkillScriptError("script output exceeds limit")
        output_file.seek(0, 2)
        if output_file.tell() > MAX_SCRIPT_OUTPUT_BYTES:
            raise SkillScriptError("script output exceeds limit")
        if os.fstat(error_file.fileno()).st_size > MAX_SCRIPT_OUTPUT_BYTES:
            raise SkillScriptError("script output exceeds limit")
        output_file.seek(0)
        stdout = output_file.read(MAX_SCRIPT_OUTPUT_BYTES + 1)
        if process.returncode != 0:
            raise SkillScriptError("script exited nonzero")
        try:
            result = json.loads(stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise SkillScriptError("script output is not valid JSON") from error
        if not isinstance(result, dict):
            raise SkillScriptError("script output must be a JSON object")
        _validate_instance(spec.output_schema, result, "output")
        return result
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        output_file.close()
        error_file.close()


__all__ = ["SkillScriptError", "SkillScriptSpec", "execute_script", "load_script_specs"]
