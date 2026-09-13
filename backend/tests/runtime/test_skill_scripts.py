import json
import sys
import time
from pathlib import Path
from threading import Event

import pytest
import httpx

from app.runtime.execution_contract import RunExecutionRequest
from app.runtime.runner_gateway_client import RunnerGatewayClient

from app.runtime.execution_snapshot import SnapshotSkill
from app.runtime.skill_scripts import (
    SkillScriptError,
    execute_script,
    load_script_specs,
)
from app.runtime.gateway_tools import build_skill_script_tools


def _skill(metadata):
    return SnapshotSkill(name="forecast", metadata=metadata)


def _write_script(root: Path, name: str, source: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def _declaration(**overrides):
    value = {
        "name": "normalize",
        "path": "scripts/normalize.py",
        "input_schema": {"type": "object", "properties": {"value": {"type": "integer"}}, "required": ["value"]},
        "output_schema": {"type": "object", "required": ["value"], "properties": {"value": {"type": "integer"}}},
        "timeout_seconds": 5,
    }
    value.update(overrides)
    return value


def test_load_script_specs_rejects_invalid_and_duplicate_declarations():
    with pytest.raises(SkillScriptError, match="duplicate"):
        load_script_specs(_skill({"scripts": [_declaration(), _declaration()]}))

    with pytest.raises(SkillScriptError, match="relative"):
        load_script_specs(_skill({"scripts": [_declaration(path="../escape.py")]}))
    with pytest.raises(SkillScriptError, match="relative"):
        load_script_specs(_skill({"scripts": [_declaration(path="C:/escape.py")]}))

    with pytest.raises(SkillScriptError, match="timeout"):
        load_script_specs(_skill({"scripts": [_declaration(timeout_seconds=121)]}))


def test_load_script_specs_uses_deterministic_tool_name_and_rejects_arbitrary_command():
    spec = load_script_specs(_skill({"scripts": [_declaration()]}))[0]
    assert spec.tool_name == "skill.forecast.script.normalize"
    assert spec.path == "scripts/normalize.py"

    with pytest.raises(SkillScriptError, match="command"):
        load_script_specs(_skill({"scripts": [_declaration(command="rm -rf /")]}))


def test_execute_script_uses_json_protocol_and_validates_input_output(tmp_path, monkeypatch):
    _write_script(
        tmp_path,
        "scripts/normalize.py",
        "import json, os, sys\n"
        "value = json.load(sys.stdin)['value']\n"
        "assert 'SECRET_TOKEN' not in os.environ\n"
        "json.dump({'value': value + 1}, sys.stdout)\n",
    )
    spec = load_script_specs(_skill({"scripts": [_declaration()]}))[0]
    monkeypatch.setenv("SECRET_TOKEN", "should-not-pass")
    assert execute_script(spec, tmp_path, {"value": 4}) == {"value": 5}

    with pytest.raises(SkillScriptError, match="input"):
        execute_script(spec, tmp_path, {"value": "bad"})

    _write_script(tmp_path, "scripts/normalize.py", "import json,sys; print('diagnostic', file=sys.stderr); json.dump({'value': 7}, sys.stdout)")
    assert execute_script(spec, tmp_path, {"value": 1}) == {"value": 7}


def test_execute_script_rejects_traversal_missing_nonzero_and_oversized_output(tmp_path):
    _write_script(tmp_path, "scripts/normalize.py", "import json,sys; json.dump({'value': 1},sys.stdout)")
    spec = load_script_specs(_skill({"scripts": [_declaration()]}))[0]
    with pytest.raises(SkillScriptError, match="path"):
        execute_script(spec, tmp_path / "other", {"value": 1})

    _write_script(tmp_path, "scripts/fail.py", "import sys; sys.exit(3)")
    fail_spec = load_script_specs(_skill({"scripts": [_declaration(name="fail", path="scripts/fail.py")]}))[0]
    with pytest.raises(SkillScriptError, match="exit"):
        execute_script(fail_spec, tmp_path, {"value": 1})

    _write_script(tmp_path, "scripts/huge.py", "import sys; sys.stdout.write('x' * 2000000)")
    huge_spec = load_script_specs(_skill({"scripts": [_declaration(name="huge", path="scripts/huge.py")]}))[0]
    with pytest.raises(SkillScriptError, match="output"):
        execute_script(huge_spec, tmp_path, {"value": 1})

    _write_script(tmp_path, "scripts/huge-error.py", "import sys; sys.stderr.write('x' * 2000000); sys.stdout.write('{\"value\": 1}')")
    huge_error_spec = load_script_specs(_skill({"scripts": [_declaration(name="huge-error", path="scripts/huge-error.py")]}))[0]
    with pytest.raises(SkillScriptError, match="output"):
        execute_script(huge_error_spec, tmp_path, {"value": 1})


def test_execute_script_supports_timeout_and_cancellation(tmp_path):
    _write_script(tmp_path, "scripts/sleep.py", "import time; time.sleep(2)")
    spec = load_script_specs(_skill({"scripts": [_declaration(name="sleep", path="scripts/sleep.py", timeout_seconds=1)]}))[0]
    with pytest.raises(SkillScriptError, match="timeout"):
        execute_script(spec, tmp_path, {"value": 1})

    cancel = Event()
    cancel.set()
    with pytest.raises(SkillScriptError, match="cancel"):
        execute_script(spec, tmp_path, {"value": 1}, cancel_event=cancel)
    with pytest.raises(SkillScriptError, match="timeout"):
        execute_script(spec, tmp_path, {"value": 1}, deadline_monotonic=time.monotonic() + 0.05)


def test_declared_script_is_exposed_as_model_tool(tmp_path):
    _write_script(tmp_path, "skills/forecast/scripts/normalize.py", "import json,sys; json.dump({'value': json.load(sys.stdin)['value'] + 1},sys.stdout)")
    snapshot = type("Snapshot", (), {"skills": (_skill({"scripts": [_declaration()]}),)})()
    tools = build_skill_script_tools(snapshot, tmp_path)
    assert [tool.name for tool in tools] == ["skill.forecast.script.normalize"]
    assert tools[0].invoke({"value": 2}) == {"value": 3}


def test_script_tool_requires_valid_lease_and_reports_completion(tmp_path):
    _write_script(tmp_path, "skills/forecast/scripts/normalize.py", "import json,sys; json.dump({'value': 3},sys.stdout)")
    snapshot = type("Snapshot", (), {"skills": (_skill({"scripts": [_declaration()]}),)})()

    class Client:
        def __init__(self, lease):
            self.lease = lease
            self.completed = []
        def execute_script(self, **_request):
            return self.lease
        def complete_script(self, **request):
            self.completed.append(request)

    invalid = Client({"status": "leased", "lease_id": "lease-1", "script_name": "wrong"})
    with pytest.raises(Exception, match="工具执行失败"):
        build_skill_script_tools(snapshot, tmp_path, client=invalid)[0].invoke({"value": 2})

    valid = Client({"status": "leased", "lease_id": "lease-1", "script_name": "skill.forecast.script.normalize"})
    assert build_skill_script_tools(snapshot, tmp_path, client=valid)[0].invoke({"value": 2}) == {"value": 3}
    assert valid.completed[0]["status"] == "completed"


def test_script_tool_reports_unexpected_executor_failure(tmp_path, monkeypatch):
    import app.runtime.gateway_tools as gateway_tools

    snapshot = type("Snapshot", (), {"skills": (_skill({"scripts": [_declaration()]}),)})()
    class Client:
        completed = []
        def execute_script(self, **_request):
            return {"status": "leased", "lease_id": "lease-2", "script_name": "skill.forecast.script.normalize"}
        def complete_script(self, **request):
            self.completed.append(request)
    client = Client()
    monkeypatch.setattr(gateway_tools, "execute_script", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("spawn failed")))
    with pytest.raises(Exception, match="工具执行失败"):
        build_skill_script_tools(snapshot, tmp_path, client=client)[0].invoke({"value": 2})
    assert client.completed[0]["status"] == "failed"


def test_runner_client_uses_script_execution_gateway_action():
    seen = {}

    class Transport(httpx.BaseTransport):
        def handle_request(self, request):
            seen["path"] = request.url.path
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"lease_id": "lease-1", "script_name": "skill.forecast.script.normalize", "status": "leased"})

    client = RunnerGatewayClient("http://gateway", "run-1", "token", execution_deadline_at=None, transport=Transport())
    result = client.execute_script(
        script_name="skill.forecast.script.normalize",
        arguments={"value": 2},
        tool_call_id="call-1",
        invocation_sequence=0,
        idempotency_key="script:call-1",
    )
    assert result["lease_id"] == "lease-1"
    assert seen["path"].endswith("/runs/run-1/script-invocations")
    assert seen["body"]["script_name"].startswith("skill.forecast.script.")
