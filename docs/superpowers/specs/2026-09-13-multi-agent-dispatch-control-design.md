# Multi-Agent Dispatch Control Design

## Goal

Extend the existing published Team runtime with a production-safe dispatch-control boundary. Teams remain generic: one immutable supervisor coordinates fixed, published member Agents through bounded DAG tasks, while control commands are represented and authorized by a dedicated platform gateway rather than executed directly by an LLM.

## Existing Foundation

The repository already provides project-scoped Team CRUD, immutable published Team and Agent snapshots, bounded serial/parallel member execution, failure strategies, approval interruption, cancellation, Artifact provenance, SSE events, and a Team management/conversation UI. This design preserves those contracts and adds only the missing control domain.

## Control Boundary

A dispatch-control Team may contain forecast, optimization, constraint-validation, command-proposal, and monitoring Agents. Agents can propose a command, but only the control gateway can admit it for execution.

The gateway requires:

- a project-scoped enabled control policy;
- JSON Schema validation of normalized parameters;
- a successful simulation Artifact attached to the same project and, when supplied, the same Run;
- a timezone-aware validity window bounded by policy;
- one or two distinct human approvals according to policy, never by the requester;
- an immutable parameter and simulation digest;
- a stable external idempotency key;
- an executor adapter registered for the target asset type;
- execution feedback and deviation evaluation before terminal success.

Database rollback is never treated as physical rollback. Failed or uncertain external execution enters an explicit `failed`, `unknown`, or `rollback_pending` state. A separately authorized rollback call uses the stored rollback plan and records its own execution attempt.

## Domain Model

`ControlPolicy` defines an asset/command pair, parameter JSON Schema, maximum command lifetime, approval mode (`single` or `dual`), deviation thresholds, enabled state, and audit ownership.

`ControlCommand` stores scope, optional Team Run/version, target, parameters and digest, simulation evidence, validity window, required approval count, rollback plan, expected/actual state, deviation result, external command reference, and status.

`ControlCommandApproval` stores one immutable decision per distinct approver. A rejection terminates admission; sufficient approvals move the command to `approved`.

`ControlExecutionAttempt` records execute or rollback attempts, request idempotency key, external idempotency key, outcome, safe result summary, and timestamps.

## API

- `GET/POST /api/control/policies`
- `GET/PATCH /api/control/policies/{policy_id}`
- `GET/POST /api/control/commands`
- `GET /api/control/commands/{command_id}`
- `POST /api/control/commands/{command_id}/approve`
- `POST /api/control/commands/{command_id}/reject`
- `POST /api/control/commands/{command_id}/execute`
- `POST /api/control/commands/{command_id}/feedback`
- `POST /api/control/commands/{command_id}/rollback`

The existing Team and Agent Run APIs remain unchanged.

## Authorization And Audit

Add `control.read`, `control.propose`, `control.approve`, `control.execute`, `control.feedback`, and `control.manage`. Unit administrators receive all permissions. Project administrators manage policies and execute; business operators propose and report feedback; auditors and viewers are read-only. Every mutation writes a sanitized, project-scoped audit event with `resource_type=control_command` or `control_policy`.

## Frontend

Add a project-scoped control-command center under Operations. It lists command state, target, validity window, simulation status, approval progress, execution result, deviation, and rollback status. Actions are permission-gated and use the dedicated APIs. Existing Team configuration remains the place to assemble supervisor and dispatch/control member Agents.

## Acceptance

Tests prove policy and project isolation, schema/range validation, simulation provenance, expiry, command and execution idempotency, distinct dual approval, executor fail-closed behavior, uncertain state handling, deviation detection, rollback recording, audit events, Team failure-strategy database constraints, and frontend permission/action flows.
