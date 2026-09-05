# Knowledge RAG And Citation Foundation Design

## 1. Goal

Build a project-scoped knowledge-base capability that ingests PDF, DOCX,
Markdown, and plain-text documents, indexes them in Milvus, exposes retrieval
only through the existing Tool Gateway, and preserves trustworthy citations in
Agent and published-Team conversations.

This phase also adds a dedicated Embedding-model configuration path to the
existing model-provider management system. Cloud providers, provider-specific
APIs, OpenAI-compatible endpoints, Ollama, vLLM, and other local deployments
share one typed adapter contract.

## 2. Scope

This phase includes:

- project-scoped knowledge-base CRUD, enablement, and authorization;
- PDF, DOCX, Markdown, and TXT upload, parsing, chunking, indexing, and versioning;
- PostgreSQL metadata, MinIO source objects, and Milvus Standalone vectors;
- durable PostgreSQL-backed ingestion jobs processed by a separate worker;
- dedicated Embedding-model capabilities, configuration, testing, and defaults;
- one platform-default Embedding model plus an optional per-knowledge-base override;
- immutable index versions bound to the effective Embedding configuration;
- Tool Gateway-mediated retrieval for both Agent and Team execution snapshots;
- server-issued citations with document-version and chunk provenance;
- a real knowledge-base directory, detail workspace, retrieval debugger, and
  citation preview UI;
- backend, frontend, migration, PostgreSQL, MinIO, Milvus, gateway, and browser
  acceptance coverage.

This phase does not include:

- OCR for scanned documents;
- Excel, PowerPoint, HTML crawling, Web connectors, or database connectors;
- user-authored parsing code;
- automatic model migration of an already published index;
- GIS rendering or GIS interaction;
- a general-purpose workflow designer;
- semantic reranking, knowledge graphs, or cross-encoder models;
- physical deletion of historical document or citation provenance;
- a new secret store or duplicate provider credentials.

## 3. Architecture Decision

Use PostgreSQL as the authoritative store for resource scope, documents,
versions, chunks, jobs, index versions, retrievals, and citations. Store source
documents and parsed text artifacts in MinIO. Store only rebuildable vectors
and filter fields in Milvus Standalone.

Every runtime retrieval passes through the Tool Gateway. Agents, Teams,
DeepAgents, LangGraph nodes, and the frontend do not connect directly to
Milvus. The gateway validates the intersection of caller, unit, project,
execution snapshot, Agent or Team member, knowledge-base state, and published
index version before invoking the knowledge adapter.

The knowledge module uses narrow ports for object storage, Embedding, and
vector storage. Production adapters use MinIO and Milvus; deterministic test
adapters use in-memory objects, fixed vectors, and exact filtering. PostgreSQL
remains authoritative in every environment.

Rejected alternatives:

1. PostgreSQL pgvector first would reduce initial deployment work but create a
   second migration when the approved Milvus production topology is enabled.
2. Supporting pgvector and Milvus simultaneously would double consistency,
   migration, and acceptance behavior without a current product requirement.
3. Letting the runtime call Milvus directly would bypass Tool Gateway
   authorization, audit, deadline, and immutable-snapshot enforcement.

## 4. Module Boundaries

### 4.1 Model providers and Embedding gateway

Extend the existing model-provider domain instead of creating a second provider
or credential store. Provider-level Base URL, API key, auth token, custom
headers, and enablement remain the only source of provider credentials.

Models receive stable machine-readable capabilities. Initial capabilities are
`chat` and `embedding`; localized display labels remain presentation data and
must not control runtime behavior. One model may expose both capabilities if a
provider supports them.

Embedding model configuration records:

- provider ID and model ID;
- adapter kind;
- endpoint-path or deployment-name override where the adapter requires it;
- vector dimension;
- maximum input tokens;
- batch size;
- normalization mode;
- request timeout;
- enabled state and last successful probe metadata.

Initial adapter kinds are:

- OpenAI-compatible `/embeddings`;
- Azure OpenAI deployment endpoints;
- Aliyun DashScope native or compatible-mode endpoints;
- Ollama local embeddings;
- vLLM and other OpenAI-compatible local deployments.

The platform stores a separate `active_embedding_model` setting. A knowledge
base may select another enabled Embedding model. Resolution order is:

1. explicit knowledge-base Embedding selection;
2. platform-default Embedding selection;
3. structured `embedding_model_unavailable` failure.

Changing the platform default affects only new or explicitly rebuilt indexes.
It never changes the effective model of an existing published index.

### 4.2 Knowledge-base control plane

The `knowledge_bases` module owns lifecycle, project scope, document metadata,
index publication, retrieval authorization, citation resolution, and audit
recording. It exposes public management APIs and an internal retrieval service
used by the Tool Gateway.

Knowledge bases are mutable management resources, but every usable index is an
immutable version. The knowledge base points to one current published index
version. A new build cannot mutate or partially replace the published version.

### 4.3 Ingestion worker

A separate knowledge worker polls durable PostgreSQL jobs. Workers acquire jobs
with `FOR UPDATE SKIP LOCKED`, write a lease owner and expiry, heartbeat long
jobs, and make abandoned jobs reclaimable. The first release does not add
Celery or Redis.

Each job advances through validated stages:

`uploaded -> parsing -> chunking -> embedding -> vector_write -> publishing -> succeeded`

Stage output is idempotent by document-version checksum and index-build ID.
Automatic retry is limited to three attempts. User-triggered retry creates a
new attempt record and preserves previous diagnostic history.

### 4.4 Parser and chunker

Parsers are isolated by media type and return the same structured document
contract: ordered blocks with text, page number when available, section path,
and source offsets. DOCX ZIP structure and decompression limits are validated
before parsing. The actual content signature must match the accepted media
type; filename extensions alone are insufficient.

Default chunk size is approximately 800 model tokens with 120-token overlap.
Chunking prefers heading and paragraph boundaries, then falls back to bounded
token windows. A knowledge base may configure these values, but every index
version captures the exact effective settings.

### 4.5 Vector store

Milvus vectors carry only the filter and lookup fields needed for retrieval:

- unit ID;
- project ID;
- knowledge-base ID;
- index-version ID;
- document-version ID;
- chunk ID;
- active state;
- vector.

PostgreSQL holds authoritative chunk content and provenance. Search first
applies the full scope and index-version filters in Milvus, then hydrates only
authorized result IDs from PostgreSQL. Missing or mismatched metadata removes
the result instead of weakening the filter.

## 5. Data Model

### 5.1 `knowledge_bases`

Stores ID, unit ID, project ID, name, description, visibility scope, enabled
state, optional Embedding provider/model override, chunk size, overlap, current
published index-version ID, optimistic version, creator, and timestamps.

The initial visibility scope is `project`. The schema reserves `private`,
`unit`, and `public` values, but public APIs reject them until their resource
authorization semantics are delivered.

### 5.2 `knowledge_documents`

Stores the logical document, knowledge-base ID, display name, enabled state,
current version ID, creator, and timestamps. Disabling a document removes it
from future index builds but does not destroy historical versions.

### 5.3 `knowledge_document_versions`

Stores an immutable version number, source Artifact/object reference, filename,
content type, byte size, SHA-256 checksum, parser version, page or section
count, parsed-text Artifact/object reference, status, creator, and timestamps.

### 5.4 `knowledge_chunks`

Stores immutable chunk ID, document-version ID, ordinal, text, page number,
section path, source offsets, token count, and content hash. Chunk rows remain
available for historical citations even after the source document is disabled.

### 5.5 `knowledge_index_jobs`

Stores knowledge-base ID, target document versions, target index-build ID,
stage, status, attempts, lease owner, lease expiry, heartbeat, progress counts,
sanitized error code/detail, requester, and timestamps.

### 5.6 `knowledge_index_versions`

Stores immutable version number, Embedding provider/model, adapter kind, vector
dimension, normalized non-secret configuration digest, chunking configuration,
document-version manifest digest, Milvus collection/partition identifiers,
status, publisher, and timestamps.

### 5.7 `knowledge_retrievals` and `knowledge_citations`

Retrieval records append the Run, Tool invocation, caller scope, query hash,
knowledge-base ID, index-version ID, requested Top K, duration, result count,
and sanitized outcome. Raw user queries are not copied into general audit
metadata.

Each accepted result produces a server-issued citation ID bound to the Run,
Tool invocation, index version, document version, chunk, score, rank, page or
section, and preview policy. The assistant message records the citation IDs it
uses. Clients can display only citation IDs returned by the server.

## 6. Ingestion Flow

1. An authorized caller uploads one accepted file to a selected project-scoped
   knowledge base.
2. The API validates size, content signature, filename, knowledge-base state,
   and `knowledge.manage` permission before any durable publication.
3. The source object is written to MinIO and a document version plus ingestion
   job are committed in PostgreSQL. Compensating cleanup handles a failed
   database commit after object creation.
4. A worker claims the job, parses the source into structured blocks, stores a
   parsed-text artifact, and writes immutable chunks.
5. The worker resolves the knowledge-base override or platform-default
   Embedding model and captures its non-secret configuration.
6. Embeddings are generated in bounded batches. Every response must contain the
   expected number of finite numeric vectors with one configured dimension.
7. Vectors are written under a new build ID and verified by count and sample
   reads.
8. PostgreSQL atomically marks the index version published and updates the
   knowledge-base pointer. Only then may the new version serve searches.
9. The previous index remains queryable by historical Run snapshots until
   retention cleanup is separately authorized.

## 7. Retrieval And Citation Flow

1. Run acceptance captures the exact knowledge-base IDs and published index
   versions permitted for the Agent or Team member.
2. A model requests the registered `knowledge.search` capability with query,
   knowledge-base ID, and Top K.
3. Tool Gateway validates the Run token, deadline, caller context, snapshot,
   Agent or Team member capability intersection, knowledge-base enablement, and
   index-version match.
4. The knowledge service embeds the query with the same effective Embedding
   configuration captured by the index version.
5. Milvus search includes unit, project, knowledge-base, index-version, and
   active filters before vector ranking.
6. PostgreSQL revalidates and hydrates result chunks. Invalid, disabled, stale,
   or cross-scope rows are discarded.
7. The service stores retrieval and citation records, then returns bounded text
   snippets plus server-issued citation IDs to the runtime.
8. The final assistant message stores its citation IDs separately from rendered
   Markdown. Unknown or mismatched IDs are removed and recorded as a sanitized
   validation event.
9. The frontend resolves a citation only through the authorized citation API
   and obtains a short-lived preview URL when source preview is allowed.

## 8. Public API

Knowledge management endpoints:

- `GET /api/knowledge-bases`;
- `POST /api/knowledge-bases`;
- `GET /api/knowledge-bases/{knowledge_base_id}`;
- `PATCH /api/knowledge-bases/{knowledge_base_id}`;
- `POST /api/knowledge-bases/{knowledge_base_id}/enable`;
- `POST /api/knowledge-bases/{knowledge_base_id}/disable`;
- `GET /api/knowledge-bases/{knowledge_base_id}/documents`;
- `POST /api/knowledge-bases/{knowledge_base_id}/documents`;
- `POST /api/knowledge-documents/{document_id}/disable`;
- `POST /api/knowledge-documents/{document_id}/reindex`;
- `GET /api/knowledge-bases/{knowledge_base_id}/jobs`;
- `POST /api/knowledge-index-jobs/{job_id}/retry`;
- `POST /api/knowledge-bases/{knowledge_base_id}/search`;
- `GET /api/knowledge-citations/{citation_id}`;
- `POST /api/knowledge-citations/{citation_id}/preview`.

Embedding configuration endpoints:

- `GET /api/models/embedding/active`;
- `PUT /api/models/embedding/active`;
- `POST /api/models/{provider_id}/models/{model_id}/test-embedding`.

Existing provider and model mutation endpoints carry the additional typed model
capabilities and Embedding configuration. Provider secrets remain write-only
and masked on all responses.

The default upload limit is 50 MiB per file. An environment setting may lower
or raise it, but the hard server maximum is 200 MiB. Retrieval `top_k` is
restricted to 1 through 20.

## 9. Tool And Snapshot Contract

Every enabled, published knowledge base appears in the Tool Registry as a
`source=knowledge` capability with a stable resource binding. Its schema accepts
`query`, `knowledge_base_id`, and `top_k`; the server ignores or rejects scope,
index-version, provider, or collection values supplied by the model.

Agent and Team publication validates knowledge-base availability and project
scope. Execution snapshots embed the knowledge-base ID, published
index-version ID, Embedding configuration digest, and the minimum retrieval
schema needed by the Runner. A later knowledge-base rebuild does not alter an
accepted Run.

Team authorization remains the intersection of Team, Agent, member, caller,
and project capability sets. The knowledge adapter cannot widen this boundary.

## 10. Frontend Experience

### 10.1 Model provider settings

The model-provider page adds a compact segmented control for `Chat models` and
`Embedding models`. The Embedding view filters by stable capability, shows the
platform default, and exposes model-specific dimension, input limit, batch
size, normalization, timeout, and test status. It supports cloud providers,
provider-specific APIs, and local Ollama/vLLM endpoints with the same status
semantics.

`Test Embedding` uses a fixed non-sensitive sample and reports response
validity, vector dimension, batch support, and latency. It never renders raw
vectors or secrets.

### 10.2 Knowledge directory

`/knowledge` becomes a real project-scoped operational directory. A compact
toolbar contains project context, search, lifecycle/health filters, and a
`New knowledge base` command. A full-width summary strip reports knowledge-base
count, document count, active chunk count, and failed jobs.

The primary table shows name, scope, document count, effective Embedding model,
published index version, health, update time, and row actions. A right-side
creation drawer captures name, description, scope, Embedding inheritance or
override, and chunking settings.

### 10.3 Knowledge detail

`/knowledge/{id}` is a full page with tabs:

- Overview: health, effective model, current index, capacity, and recent jobs;
- Documents: upload, immutable versions, size, pages, chunks, index state,
  reindex, and disable actions;
- Ingestion jobs: stage progress, attempt history, sanitized failure, and retry;
- Retrieval debugger: query, Top K, approved filters, ranked chunks, score,
  document, page/section, version, and highlighted text;
- Settings: scope, Embedding inheritance or override, chunk policy, enablement,
  and explicit rebuild action.

The layout remains dense and operational. It does not nest cards or use a
marketing-style hero. Desktop uses tables and right-side detail surfaces;
mobile wraps the toolbar and renders rows as stable vertical items without
overlapping progress or actions.

### 10.4 Conversation citations

Assistant answers render server-provided references as numbered citation links.
Selecting one opens a right-side drawer with knowledge base, document, version,
page or section, matched excerpt, index version, and authorized preview action.
The conversation remains mounted while the drawer opens.

Forbidden, disabled, expired, or stale citations show a precise local state and
never reveal prior content or object URLs.

## 11. Error Handling And Recovery

Stable public error codes include:

- `knowledge_base_not_found`;
- `knowledge_base_unavailable`;
- `knowledge_document_unsupported`;
- `knowledge_document_too_large`;
- `knowledge_document_invalid`;
- `knowledge_ingestion_failed`;
- `embedding_model_unavailable`;
- `embedding_response_invalid`;
- `embedding_dimension_mismatch`;
- `knowledge_index_unavailable`;
- `knowledge_index_stale`;
- `knowledge_scope_forbidden`;
- `knowledge_citation_not_found`;
- `knowledge_citation_forbidden`.

Public errors are sanitized and include the existing trace/request identifier.
Provider bodies, object keys, credentials, raw vectors, and sensitive source
text are excluded from general logs and error responses.

Milvus, MinIO, or Embedding outages fail closed. Retrieval never falls back to
an unfiltered backend or an older index not captured by the Run. If reliable
citations cannot be created, the knowledge Tool returns a structured failure
and the final answer must state that knowledge retrieval was unavailable.

A failed new index build leaves the current published index untouched. Partial
Milvus build data is marked by build ID and may be removed only by bounded,
idempotent cleanup after PostgreSQL confirms it is not published or referenced.

## 12. Security And Audit

Management APIs require the existing `knowledge.read`, `knowledge.manage`, or
`knowledge.retrieve` permission at the current-project target as appropriate.
All repository queries include unit and project scope; route-level checks are
not the only isolation mechanism.

Uploads use generated object keys and sanitized display filenames. Accepted
content types are PDF, DOCX, Markdown, and plain text. DOCX archive entry count,
expanded bytes, and compression ratio are bounded. Parsers do not execute
macros, scripts, external references, or embedded binaries.

The phase provides a malware-scanner integration port. When deployment policy
requires scanning, documents cannot advance to parsing until the configured
scanner returns clean. Environments without a scanner expose the status as
`not_configured`; they do not claim that scanning succeeded.

Audit records cover knowledge-base lifecycle, upload, disable, retry, index
publication, Embedding default changes, model probes, retrieval outcomes, and
citation access. Audit metadata uses IDs, counts, hashes, and sanitized status,
not raw document passages or queries.

## 13. Testing And Acceptance

Backend unit tests cover:

- PDF, DOCX, Markdown, and TXT parsing contracts;
- content signature, size, archive, filename, and chunk-boundary validation;
- deterministic chunk IDs and immutable version behavior;
- Embedding adapter request/response mapping for every supported adapter kind;
- vector count, finite-number, dimension, batch, timeout, and error validation;
- default-versus-override Embedding resolution;
- job lease, heartbeat, retry, reclaim, idempotency, and atomic publication;
- citation issuance, validation, message linkage, and redaction.

Database and integration tests cover:

- Alembic upgrade/rollback and constraints on SQLite and PostgreSQL;
- PostgreSQL project isolation, optimistic concurrency, and `SKIP LOCKED` job claims;
- real MinIO upload, parsed artifact, preview authorization, and compensation;
- real Milvus write, filtered retrieval, version isolation, rebuild, and cleanup;
- Tool Gateway caller/Agent/Team/member/project permission intersection;
- immutable Agent and Team snapshot behavior across later index rebuilds;
- outage and dimension-mismatch failures without fallback or partial publication.

Frontend tests cover:

- Embedding model filtering, default selection, override, and probe states;
- knowledge directory loading, filters, empty/error states, and permissions;
- document upload, progress, version history, retry, disable, and rebuild;
- retrieval debugger result provenance and inaccessible-result handling;
- conversation citation rendering and authorized/forbidden/stale drawers;
- desktop and mobile layouts without overlap or truncated commands.

Browser acceptance creates a project-scoped knowledge base, uploads one
supported document, observes every ingestion stage, retrieves a known passage,
binds the published knowledge base to an Agent and a Team, receives a cited
answer, opens the citation preview, rebuilds with an explicit Embedding model,
and proves that another project cannot list, retrieve, invoke, or preview the
resource.

Before integration, the existing full backend suite, PostgreSQL suite, frontend
suite, type check, production build, and targeted real-service acceptance must
all pass. A skipped MinIO or Milvus test is reported as unverified rather than
treated as passing production acceptance.

## 14. Delivery Sequence

1. Extend provider/model contracts with typed Embedding capabilities and probes.
2. Add knowledge metadata, migration, repository, and project-scoped service.
3. Add safe parsers, chunking, durable jobs, and worker lifecycle.
4. Add Embedding gateway adapters and immutable effective configuration.
5. Add Milvus adapter and atomic index publication.
6. Add Tool Gateway retrieval, snapshot binding, and citations.
7. Replace the model-provider and knowledge frontend placeholders.
8. Integrate conversation citations and right-side preview.
9. Run PostgreSQL, MinIO, Milvus, browser, security, and full regression acceptance.
