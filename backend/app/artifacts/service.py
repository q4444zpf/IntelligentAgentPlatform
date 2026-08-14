from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timezone
from pathlib import PurePosixPath
from urllib.parse import quote

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.conversations.models import AgentRun, Conversation
from app.core.request_context import RequestContext

from .models import ArtifactRecord
from .storage import S3ObjectStorage


class ArtifactNotFoundError(LookupError):
    pass


class ArtifactAlreadyExistsError(FileExistsError):
    pass


class ArtifactIntegrityError(ValueError):
    pass


class ArtifactSizeError(ValueError):
    pass


class ArtifactContentTypeError(ValueError):
    pass


_RUNNER_CONTENT_TYPES = frozenset(
    {
        "application/json",
        "application/octet-stream",
        "application/pdf",
        "image/jpeg",
        "image/png",
        "text/csv",
        "text/markdown",
        "text/plain",
    }
)


def _runner_artifact_max_bytes() -> int:
    raw = os.getenv("IAP_RUNNER_ARTIFACT_MAX_BYTES", "10485760")
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError("IAP_RUNNER_ARTIFACT_MAX_BYTES must be positive") from error
    if value <= 0:
        raise ValueError("IAP_RUNNER_ARTIFACT_MAX_BYTES must be positive")
    return value


class ArtifactService:
    def __init__(
        self,
        session: Session,
        storage: S3ObjectStorage,
        *,
        runner_max_bytes: int | None = None,
        runner_content_types: frozenset[str] | None = None,
    ):
        self.session = session
        self.storage = storage
        self.runner_max_bytes = (
            _runner_artifact_max_bytes()
            if runner_max_bytes is None
            else runner_max_bytes
        )
        if self.runner_max_bytes <= 0:
            raise ValueError("runner artifact max bytes must be positive")
        self.runner_content_types = runner_content_types or _RUNNER_CONTENT_TYPES

    def _visible(self, context: RequestContext):
        return or_(
            (ArtifactRecord.scope == "public"),
            (ArtifactRecord.scope == "tenant") & (ArtifactRecord.unit_id == context.unit_id),
            (ArtifactRecord.scope == "project") & (ArtifactRecord.unit_id == context.unit_id) & (ArtifactRecord.project_id == context.project_id),
            (ArtifactRecord.scope == "private") & (ArtifactRecord.unit_id == context.unit_id) & (ArtifactRecord.owner_id == context.user_id),
        )

    def get(self, artifact_id: str, context: RequestContext) -> ArtifactRecord:
        row = self.session.scalar(select(ArtifactRecord).where(
            ArtifactRecord.id == artifact_id,
            ArtifactRecord.status == "active",
            self._visible(context),
        ))
        if row is None:
            raise ArtifactNotFoundError(artifact_id)
        return row

    def list(self, context: RequestContext) -> list[ArtifactRecord]:
        return list(self.session.scalars(select(ArtifactRecord).where(
            ArtifactRecord.status == "active", self._visible(context)
        ).order_by(ArtifactRecord.created_at.desc())))

    def create(self, *, context: RequestContext, filename: str, content_type: str, data: bytes, scope: str, run_id: str | None = None) -> ArtifactRecord:
        project_id = context.project_id if scope in {"project", "private"} else None
        if run_id:
            run = self.session.scalar(select(AgentRun).join(Conversation, Conversation.id == AgentRun.conversation_id).where(
                AgentRun.id == run_id,
                Conversation.unit_id == context.unit_id,
                Conversation.project_id == context.project_id,
            ))
            if run is None:
                raise ArtifactNotFoundError(run_id)
        artifact_id = str(uuid.uuid4())
        safe_name = filename.replace(" ", "_")
        object_key = f"units/{context.unit_id}/projects/{context.project_id}/{artifact_id}/{safe_name}"
        record = ArtifactRecord(
            id=artifact_id, unit_id=context.unit_id, project_id=project_id,
            owner_id=context.user_id, scope=scope, run_id=run_id,
            object_key=object_key, filename=filename, content_type=content_type,
            size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest(), status="active",
        )
        self.storage.put_bytes(object_key, data, content_type)
        self.session.add(record)
        self.session.commit()
        self.session.refresh(record)
        return record

    def create_for_run(
        self,
        *,
        run_id: str,
        path: str,
        content_type: str,
        data: bytes,
        sha256: str,
        commit: bool = True,
    ) -> ArtifactRecord:
        run_context = self._run_context(run_id)
        normalized_path = self._validate_runner_path(path)
        if len(data) > self.runner_max_bytes:
            raise ArtifactSizeError("artifact exceeds size limit")
        if content_type not in self.runner_content_types:
            raise ArtifactContentTypeError("artifact content type is not allowed")
        actual_sha256 = hashlib.sha256(data).hexdigest()
        if sha256 != actual_sha256:
            raise ArtifactIntegrityError("artifact digest does not match content")
        existing = self.session.scalar(
            select(ArtifactRecord).where(
                ArtifactRecord.run_id == run_id,
                ArtifactRecord.filename == normalized_path,
                ArtifactRecord.status == "active",
            )
        )
        if existing is not None:
            raise ArtifactAlreadyExistsError(normalized_path)

        artifact_id = str(uuid.uuid4())
        safe_name = quote(normalized_path, safe="._-")
        object_key = (
            f"units/{run_context['unit_id']}/projects/{run_context['project_id']}"
            f"/runs/{run_id}/{artifact_id}/{safe_name}"
        )
        record = ArtifactRecord(
            id=artifact_id,
            unit_id=str(run_context["unit_id"]),
            project_id=str(run_context["project_id"]),
            owner_id=str(run_context["user_id"]),
            scope="private",
            run_id=run_id,
            object_key=object_key,
            filename=normalized_path,
            content_type=content_type,
            size_bytes=len(data),
            sha256=actual_sha256,
            status="active",
        )
        uploaded = False
        try:
            self.storage.put_bytes(object_key, data, content_type)
            uploaded = True
            self.session.add(record)
            if commit:
                self.session.commit()
                self.session.refresh(record)
            else:
                self.session.flush()
        except Exception:
            self.session.rollback()
            if uploaded:
                self.storage.delete_object(object_key)
            raise
        return record

    def get_for_run(self, run_id: str, artifact_id: str) -> ArtifactRecord:
        row = self.session.scalar(
            select(ArtifactRecord).where(
                ArtifactRecord.id == artifact_id,
                ArtifactRecord.run_id == run_id,
                ArtifactRecord.status == "active",
            )
        )
        if row is None:
            raise ArtifactNotFoundError(artifact_id)
        return row

    def list_for_run(self, run_id: str) -> list[ArtifactRecord]:
        return list(
            self.session.scalars(
                select(ArtifactRecord)
                .where(
                    ArtifactRecord.run_id == run_id,
                    ArtifactRecord.status == "active",
                )
                .order_by(ArtifactRecord.created_at, ArtifactRecord.id)
            )
        )

    def _run_context(self, run_id: str) -> dict[str, str]:
        row = (
            self.session.execute(
                select(
                    Conversation.unit_id,
                    Conversation.project_id,
                    Conversation.owner_id.label("user_id"),
                )
                .join(AgentRun, AgentRun.conversation_id == Conversation.id)
                .where(AgentRun.id == run_id)
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise ArtifactNotFoundError(run_id)
        return dict(row)

    @staticmethod
    def _validate_runner_path(path: str) -> str:
        if (
            not path
            or "\x00" in path
            or "\\" in path
            or path.startswith("/")
            or len(path) > 255
        ):
            raise ArtifactIntegrityError("artifact path is invalid")
        parts = PurePosixPath(path).parts
        if not parts or any(part in {"", ".", ".."} for part in parts):
            raise ArtifactIntegrityError("artifact path is invalid")
        return "/".join(parts)

    def delete(self, artifact_id: str, context: RequestContext) -> ArtifactRecord:
        row = self.get(artifact_id, context)
        self.storage.delete_object(row.object_key)
        row.status = "deleted"
        row.deleted_at = datetime.now(timezone.utc)
        self.session.commit()
        return row

    def attach_to_run(self, artifact_id: str, run_id: str, context: RequestContext) -> ArtifactRecord:
        artifact = self.get(artifact_id, context)
        run = self.session.scalar(select(AgentRun).join(Conversation, Conversation.id == AgentRun.conversation_id).where(
            AgentRun.id == run_id,
            Conversation.unit_id == context.unit_id,
            Conversation.project_id == context.project_id,
        ))
        if run is None:
            raise ArtifactNotFoundError(run_id)
        artifact.run_id = run_id
        self.session.commit()
        self.session.refresh(artifact)
        return artifact
