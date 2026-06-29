# Repository Guidelines

## Project Scope

This repository is the workspace for a professional water-conservancy intelligent agent platform. The platform should cover water model management, model upload and registration, control models, drag-and-drop orchestration, agent management, skill management, multi-agent collaboration, multi-user access, dedicated conversation management, LLM settings, AI chatbox integration, Web system integration, and desktop/client integration.

## Project Structure & Module Organization

Keep the top-level layout predictable as modules are added:

- `frontend/` for the Web console, workflow designer, embedded AI chatbox, and administration UI.
- `backend/` for APIs, authentication, user permissions, model registry, and audit logs.
- `ai-services/` for LLM routing, RAG, prompt management, skill execution, and multi-agent coordination.
- `model-services/` for hydrology, hydrodynamic, water resource, flood forecasting, dispatch optimization, control, GIS/remote-sensing, and custom model services.
- `desktop-client/` for desktop-specific agent interaction, local file context, notifications, and client SDK use.
- `docs/` for architecture, API contracts, data models, deployment, and integration guides.
- `tests/` for unit, integration, workflow, and end-to-end tests.
- `assets/` for diagrams, UI images, and static resources.

## Build, Test, and Development Commands

No build system is committed yet. When adding one, document commands in module `README.md` files and expose common root commands:

- `npm run dev`: start the Web application.
- `npm test`: run unit tests.
- `npm run lint`: run formatting and static checks.
- `docker compose up`: start local infrastructure such as databases, object storage, queues, and vector stores.

Avoid committing generated build output unless required.

## Coding Style & Naming Conventions

Use clear, domain-oriented English identifiers such as `ModelRegistryService`, `ControlModelDefinition`, `SkillExecutionRecord`, and `ReservoirDispatchAgent`. Use Chinese only for user-facing labels, prompts, and documentation. Use 2-space indentation for JavaScript/TypeScript/JSON/YAML and 4-space indentation for Python. Keep API paths lowercase and resource-oriented, for example `/api/models/{id}/versions`.

## Domain & Architecture Guidelines

Model management must support uploaded files, Docker images, scripts, external APIs, versioning, parameter schemas, runtime configuration, permissions, and execution logs. Include control models as first-class model types for reservoirs, gates, pump stations, river networks, irrigation areas, and urban drainage.

Agent management must distinguish `web`, `desktop`, and `common` runtime forms because interaction context and prompts differ. Web agents should focus on page context, business objects, and embedded chat. Desktop agents should support local files, client state, notifications, and human confirmation for control commands.

## Testing Guidelines

Add tests with every behavior change. Cover model registration, control model validation, skill invocation, Web/Desktop prompt selection, permission checks, workflow execution, conversation persistence, and third-party integration APIs. Name tests after expected behavior, for example `registersUploadedHydrologyModel` or `usesDesktopPromptWhenClientTypeIsDesktop`.

## Commit & Pull Request Guidelines

This workspace does not currently expose Git history, so use concise conventional commits going forward, for example `feat: add model registry API` or `fix: validate skill parameters`. Pull requests should include a summary, test evidence, linked issue or requirement, screenshots for UI changes, and notes for database or configuration changes.

## Security & Configuration Tips

Never commit API keys, model credentials, database passwords, LLM secrets, or customer water-project data. Store secrets in environment variables or a secret manager. Keep sample configuration files safe with placeholders such as `LLM_API_KEY=change-me`.
