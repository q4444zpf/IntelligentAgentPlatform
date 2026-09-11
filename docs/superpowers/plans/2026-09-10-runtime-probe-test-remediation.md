# Runtime Probe Test Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove whole-test-module startup cost from the real worker deadline probe, format the branch-owned Project Skill startup module, and restore green backend and branch-owned static gates.

**Architecture:** The parent runtime test serializes its already-built snapshot and request into its pytest temporary directory, then launches a focused test-only child script. After imports, JSON validation, and test wiring, the child binds the same five-second overall and 0.75-second execution windows used by the old inline probe, executes the real `run_worker.main()`, and writes the existing ready marker only from the selected slow graph. Production runtime code and all existing deadline budgets remain unchanged.

**Tech Stack:** Python 3.12, pytest, Pydantic 2 model JSON, subprocess, FastAPI backend test infrastructure, Black, Ruff, Docker Compose.

## Global Constraints

- Implement the confirmed design in `docs/superpowers/specs/2026-09-10-runtime-probe-test-remediation-design.md`.
- Continue in the linked worktree and branch `codex/project-skill-production-activation`; do not write implementation code on `main`.
- Do not change any file under `backend/app/runtime/` or any other production runtime behavior.
- Preserve the 10-second selected-stage ready budget, the 0.75-second production execution window started after child bootstrap, the 2.5-second post-ready exit budget, exit code 4 assertion, and bounded child cleanup.
- The new child process must not import or execute `test_sandbox_runtime.py`.
- The child ready marker may be written only when the selected planning, member, or synthesis graph begins its slow `invoke()` call.
- Format only `backend/app/skills/project_startup.py`; do not reformat the four files whose Black/import-order failures already exist at plan base `4930f67`.
- Do not change APIs, database schemas, MinIO behavior, frontend, Agent, Team, collaboration, deployment configuration, or remote servers.
- Run process-timing tests sequentially. Do not widen timeouts to make failures disappear.
- Before each commit, use the approved TLS-verifying proxy override to fetch `origin`, merge `origin/main`, and rerun affected gates. Do not modify global Git configuration.
- Use only the existing dedicated test databases and UUID-owned MinIO buckets for real integrations. Never reset or downgrade database `iap` and never enumerate or clean shared buckets.
- Do not push, merge to `main`, deploy, or remove the worktree until `finishing-a-development-branch` presents the final user choice.

---

## Status and File Map

- [x] Root cause measured: child `runpy.run_path(test_sandbox_runtime.py)` takes 42-44 seconds before the ready marker; the test budget is 10 seconds.
- [x] User approved the lightweight child-probe design.
- [ ] Task 1: replace full test-module execution with a focused worker deadline probe.
- [ ] Task 2: format the branch-owned Project Skill startup module.
- [ ] Task 3: run final gates and update durable evidence.

| File | Responsibility |
| --- | --- |
| `backend/tests/runtime/worker_deadline_probe.py` | Test-only child entrypoint with minimal gateway/graphs and real worker wiring |
| `backend/tests/runtime/test_sandbox_runtime.py` | Parent process fixture serialization, child lifecycle, timing and exit assertions |
| `backend/app/skills/project_startup.py` | Black-only formatting; behavior must remain bytecode-equivalent in intent |
| `docs/superpowers/plans/2026-09-09-project-skill-production-activation-verification.md` | Durable final gate commands, counts, residual baseline debt and integration status |

## Task 1: Lightweight Real-Worker Deadline Probe

**Files:**
- Create: `backend/tests/runtime/worker_deadline_probe.py`
- Modify: `backend/tests/runtime/test_sandbox_runtime.py:2925-3041`

**Interfaces:**
- Consumes: `SnapshotResponse.model_validate_json`, `RunExecutionRequest.model_validate_json`, `SandboxRuntime`, `run_worker.main()`, a stage value in `{planning, member, synthesis}`, and three filesystem paths for ready marker, snapshot JSON and request JSON.
- Produces: CLI `python backend/tests/runtime/worker_deadline_probe.py <stage> <ready-path> <snapshot-json-path> <request-json-path>`; ready marker content equals the selected stage; process exits with the real worker result.

- [ ] **Step 1: Record the existing behavioral RED.**

Run sequentially:

```powershell
python -m pytest "backend/tests/runtime/test_sandbox_runtime.py::test_real_worker_process_does_not_join_abandoned_team_deadline_work" -q -p no:cacheprovider --tb=short
```

Expected on the current harness: all three parameters fail at `worker did not enter the selected slow stage`. Record the count and duration in the task report. Do not change a timeout.

- [ ] **Step 2: Add serialized parent fixtures.**

In the existing test, build the snapshot and base request in the parent process before `Popen`. The child owns the short-lived timing fields so process bootstrap cannot consume the production execution window:

```python
snapshot = _schema_v5_team_snapshot()
request = _request(snapshot)
snapshot_path = tmp_path / f"{slow_stage}.snapshot.json"
request_path = tmp_path / f"{slow_stage}.request.json"
snapshot_path.write_text(snapshot.model_dump_json(), encoding="utf-8")
request_path.write_text(request.model_dump_json(), encoding="utf-8")
```

Replace the `python -c <probe>` invocation with the helper path and arguments:

```python
probe_path = Path(__file__).with_name("worker_deadline_probe.py")
process = subprocess.Popen(
    [
        sys.executable,
        str(probe_path),
        slow_stage,
        str(ready_path),
        str(snapshot_path),
        str(request_path),
    ],
    cwd=Path(__file__).resolve().parents[2],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
)
```

Delete only the now-unused inline `probe` string and imports made unnecessary by that deletion. Keep the parent wait loop, exact deadlines, `finally` kill/wait and exit assertions unchanged.

- [ ] **Step 3: Implement the focused child entrypoint.**

Create `worker_deadline_probe.py` with these public/test boundaries:

```python
def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 4:
        raise SystemExit(
            "usage: worker_deadline_probe.py "
            "<stage> <ready-path> <snapshot-path> <request-path>"
        )
    stage, ready_name, snapshot_name, request_name = arguments
    if stage not in {"planning", "member", "synthesis"}:
        raise SystemExit(f"unsupported stage: {stage}")
    ready_path = Path(ready_name)
    snapshot = SnapshotResponse.model_validate_json(
        Path(snapshot_name).read_text(encoding="utf-8")
    )
    request = RunExecutionRequest.model_validate_json(
        Path(request_name).read_text(encoding="utf-8")
    )
    gateway = ProbeGateway(snapshot)
    # Wire the request, gateway and ProbeAgentFactory into real run_worker.
    return run_worker.main()
```

Define `ProbeGateway` with only the real runtime boundary used by this test:

```python
class ProbeGateway:
    def __init__(self, snapshot: SnapshotResponse):
        self.snapshot = snapshot

    def get_snapshot(self):
        return self.snapshot

    def get_latest_checkpoint(self):
        raise RunnerGatewayBusinessError("checkpoint_not_found")

    def save_checkpoint(self, checkpoint_key, state, idempotency_key):
        return {
            "checkpoint_key": checkpoint_key,
            "snapshot_digest": self.snapshot.digest,
            "state": state,
        }

    def append_event(self, **request):
        return request

    def complete(self, request, idempotency_key):
        return request

    def invoke_model(self, request, idempotency_key):
        raise AssertionError("probe graphs must not call the gateway model")

    def invoke_tool(self, **request):
        raise AssertionError("probe graphs must not invoke tools")

    def register_artifact_capability(self, **request):
        return f"capability:{request['invocation_id']}"

    def list_artifacts(self):
        return []
```

Define `ProbeTextGraph` and `ProbePlanGraph` to return the same completed state shapes as the current test doubles. Define `SlowGraph.invoke()` to write `stage` to `ready_path`, sleep 5 seconds, then delegate. Define `ProbeAgentFactory.build()` with the same stage selection rules now embedded in the test: first supervisor call is slow planning or a plan graph; member-1 is slow only for member; the second supervisor call is slow only for synthesis. Wire:

```python
class ClientFactory:
    @staticmethod
    def from_execution_request(_request):
        return gateway

run_worker.load_execution_request = lambda: request
run_worker.RunnerGatewayClient = ClientFactory
run_worker.SandboxRuntime = lambda current_gateway: SandboxRuntime(
    current_gateway,
    agent_factory=ProbeAgentFactory(stage, ready_path),
)
run_worker.sys.argv = ["run_worker"]
now = datetime.now(UTC)
request = request.model_copy(
    update={
        "deadline_at": now + timedelta(seconds=5),
        "execution_deadline_at": now + timedelta(seconds=0.75),
    }
)
```

Bind those timing fields only after imports, JSON validation, and test wiring are complete and immediately before the real `run_worker.main()` call. Do not replace the production runtime clock. The helper must end with `raise SystemExit(main())`. It must never import `backend/tests/runtime/test_sandbox_runtime.py` or use `runpy`.

- [ ] **Step 4: Run GREEN and mutation-oriented checks.**

Run the Step 1 command. Require `3 passed`, no skip/warning, and keep the original time assertions. Then run:

```powershell
python -m pytest backend/tests/runtime/test_sandbox_runtime.py -q -p no:cacheprovider --tb=short
python -m ruff check backend/tests/runtime/worker_deadline_probe.py
python -m ruff check --ignore I001 backend/tests/runtime/test_sandbox_runtime.py
python -m black --check backend/tests/runtime/worker_deadline_probe.py
git diff --exit-code -- backend/app/runtime
git diff --check
```

The parent test file already fails Black and Ruff `I001` at plan base; do not reformat its unrelated contents. Before declaring GREEN, temporarily make `SlowGraph.invoke()` omit the ready write and confirm the three-parameter test fails, then restore the helper and rerun `3 passed`. Do not commit the mutation.

- [ ] **Step 5: Fetch, merge, rerun and commit Task 1.**

Use the approved fetch override:

```powershell
git -c http.sslVerify=true -c http.sslBackend=openssl -c http.proxy=http://127.0.0.1:7897 -c https.proxy=http://127.0.0.1:7897 -c http.version=HTTP/1.1 -c http.sslVersion=tlsv1.2 fetch origin
git merge --no-edit origin/main
```

Rerun Step 4 after the merge. Stage exactly the two Task 1 files and commit:

```text
test: isolate worker deadline subprocess fixture
```

## Task 2: Project Skill Startup Formatting

**Files:**
- Modify: `backend/app/skills/project_startup.py`

**Interfaces:**
- Consumes and produces the existing `ProjectSkillStartupError`, `load_code_migration_heads()` and `validate_project_skills_startup()` behavior without semantic change.

- [ ] **Step 1: Record the formatting failure.**

Run:

```powershell
python -m black --check backend/app/skills/project_startup.py
```

Expected before formatting: exit `1`, with only the Alembic `frozenset(...)` expression reported for reformatting.

- [ ] **Step 2: Apply Black to only the target.**

Run:

```powershell
python -m black backend/app/skills/project_startup.py
```

Inspect the diff and require only mechanical line wrapping. No identifiers, strings, control flow or exception handling may change.

- [ ] **Step 3: Verify behavior and formatting.**

Run:

```powershell
python -m pytest backend/tests/skills/test_project_startup.py backend/tests/integration/test_postgres_migrations.py -q -p no:cacheprovider --tb=short
python -m ruff check backend/app/skills/project_startup.py
python -m black --check backend/app/skills/project_startup.py
git diff --check
```

Require all tests and checks to pass with clean output.

- [ ] **Step 4: Fetch, merge, rerun and commit Task 2.**

Run the same secure fetch and `git merge --no-edit origin/main` commands from Task 1. Rerun Step 3, stage only `backend/app/skills/project_startup.py`, and commit:

```text
style: format project skill startup validation
```

## Task 3: Final Regression and Durable Evidence

**Files:**
- Modify when the verified floating-point baseline failure is present: `backend/tests/runtime/test_run_lifecycle.py`
- Modify: `docs/superpowers/plans/2026-09-09-project-skill-production-activation-verification.md`

**Interfaces:**
- Consumes: Task 1 and Task 2 commits, existing credential-safe service helpers, dedicated PostgreSQL databases, UUID MinIO buckets and `intelligent-agent-platform-api:local`.
- Produces: a green or precisely blocked final gate record with commands, exit codes, counts and residual baseline debt.

- [ ] **Step 1: Run the runtime and complete backend gates sequentially.**

Clear all external `TEST_*`; set only `DATABASE_URL` to the approved dedicated Cookie test database. Run:

```powershell
python -m pytest backend/tests/runtime -q -rs -p no:cacheprovider --tb=short
python -m pytest backend/tests -q -rs -p no:cacheprovider --tb=short
```

Require the runtime suite and complete backend suite to have zero failures. Record passed/skipped/warning counts and durations without adding overlapping totals. If `test_team_watchdog_clamps_poll_sleep_to_remaining_deadline` alone fails because a real `time.monotonic()` subtraction yields a sleep such as `0.1500000000014552` against the exact `0.15` assertion, preserve `poll_interval=0.6` and `timeout_seconds=0.15` but replace that test's wall-clock coupling with a test-local controllable monotonic clock and a sleeper that records and advances the clock. Assert the exact deterministic sleep call and remove the redundant real elapsed-time assertion. Do not change `backend/app/runtime/run_lifecycle.py` or any timeout value. Verify RED before the test edit, GREEN after it, and temporarily mutate the production clamp expression to prove the deterministic test detects an unclamped poll sleep; restore production immediately and confirm its diff is clean.

- [ ] **Step 2: Run real PostgreSQL and MinIO selections with zero configuration skips.**

Run the existing credential-safe helpers exactly as documented in the durable verification report:

```powershell
python .superpowers/sdd/2026-09-09-project-skill-production-activation/task-6-service-mode.py

$env:TASK6_DATABASE_NAME = 'iap_skill_control_test_20260908_a'
python .superpowers/sdd/2026-09-09-project-skill-production-activation/task-6-service-mode.py python -m pytest backend/tests/integration/test_project_skill_storage_bootstrap_minio.py backend/tests/integration/test_skill_package_minio.py backend/tests/integration/test_skill_control_plane_minio.py backend/tests/integration/test_skill_control_plane_postgres.py backend/tests/integration/test_skill_versions_postgres.py -q -rs -p no:cacheprovider --tb=short
Remove-Item Env:TASK6_DATABASE_NAME

& .superpowers/sdd/2026-09-09-project-skill-production-activation/run_migration_test_mode.ps1 -PytestArguments @('backend/tests/integration/test_project_skill_startup_postgres.py', '-q', '-rs', '-p', 'no:cacheprovider', '--tb=short')
```

Require zero configuration skips. Helpers may read existing test-container credentials into process memory but must never print or persist them.

- [ ] **Step 3: Run baseline-aware static and release gates.**

Run:

```powershell
..\skill-version-foundation\.venv\Scripts\python.exe -m pip check
$changedPython = @(git diff --name-only 4930f67 HEAD -- '*.py')
python -m ruff check --ignore I001 @changedPython
$addedPython = @(git diff --diff-filter=A --name-only 4930f67 HEAD -- '*.py')
python -m black --check @addedPython
git diff --check 4930f67 HEAD
docker compose config --quiet
docker compose --profile operations config --quiet
docker compose build api
docker image inspect --format '{{json .Config.Cmd}}' intelligent-agent-platform-api:local
git diff --exit-code 4930f67 HEAD -- backend/app/skills/router.py backend/app/skills/service.py frontend backend/app/agents backend/app/collaboration backend/app/runtime
```

Require project `pip check`, baseline-aware Ruff, added-file Black, diff, Compose, image build and protected production paths to pass. Require image `Config.Cmd` exactly:

```json
["uvicorn","app.main:app","--host","0.0.0.0","--port","8000","--proxy-headers"]
```

Also rerun ordinary full-repository Ruff and branch-changed-file Black only to preserve their baseline status in the report; do not fix unrelated baseline files.

- [ ] **Step 4: Fetch, merge and rerun affected gates.**

Run the secure fetch and merge commands from Task 1. If `origin/main` changes source, rerun every affected gate from Steps 1-3. If it is already up to date, rerun at minimum the three-parameter deadline test, complete backend, added-file Black, branch Ruff, diff and protected-path checks.

- [ ] **Step 5: Update durable evidence and commit Task 3.**

Update the final-gates section with the new commands, exit codes, counts, durations, review result and exact residual baseline debt. Remove the obsolete statement that the new `project_startup.py` has a Black difference. Do not describe the runtime or backend suite as passing unless the fresh commands exited `0`.

If the deterministic watchdog correction was required, stage exactly that test file and the verification document together; otherwise stage only the verification document. Commit:

```text
test: stabilize watchdog deadline regression
```

## Final Review and Branch Finish

- [ ] Generate a whole-remediation review package from `258cae0` to Task 3 HEAD and run one independent final review for scope, timing semantics, cleanup, test quality and evidence accuracy.
- [ ] If the review reports findings, use one consolidated fix wave and one scoped re-review.
- [ ] Run the three-parameter deadline test, complete backend suite and branch-owned static gates once more after the final reviewed commit.
- [ ] Keep the previous Project Skill branch review conclusions and real-service evidence; do not erase historical failed runs.
- [ ] Remove only this remediation plan's ignored SDD workspace after all gates are green and evidence is committed. Preserve the parent activation plan workspace until branch integration.
- [ ] Invoke `superpowers:finishing-a-development-branch`. Present merge/push/keep choices only if the fresh complete backend suite is green.
