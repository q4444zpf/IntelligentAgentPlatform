from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, hmac
from pydantic import BaseModel, ConfigDict
from sqlalchemy import DateTime, Integer, String, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.core.config import RunnerTokenSettings
from app.db.base import Base

if TYPE_CHECKING:
    from .execution_snapshot import StoredExecutionSnapshot


RunnerAction = Literal[
    "snapshot.read",
    "model.invoke",
    "tool.invoke",
    "checkpoint.read",
    "checkpoint.write",
    "event.append",
    "artifact.create",
    "result.complete",
]
_ALLOWED_ACTIONS = {
    "snapshot.read",
    "model.invoke",
    "tool.invoke",
    "checkpoint.read",
    "checkpoint.write",
    "event.append",
    "artifact.create",
    "result.complete",
}


class RunTokenInvalid(ValueError):
    pass


class RunTokenForbidden(PermissionError):
    pass


class RunTokenNotFound(LookupError):
    pass


class RunTokenClaims(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    iss: str
    aud: str
    jti: str
    run_id: str
    unit_id: str
    project_id: str
    snapshot_id: str
    snapshot_digest: str
    actions: tuple[str, ...]
    iat: float
    nbf: float
    exp: float


@dataclass(frozen=True)
class IssuedRunToken:
    value: str
    claims: RunTokenClaims


class RuntimeRunTokenRevocation(Base):
    __tablename__ = "runtime_run_token_revocations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    jti: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    revoked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str] = mapped_column(String(120), nullable=False)


def _encode_segment(value: dict[str, object]) -> str:
    data = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _decode_segment(value: str) -> dict[str, object]:
    padding = "=" * (-len(value) % 4)
    decoded = base64.b64decode(
        value + padding, altchars=b"-_", validate=True
    )
    parsed = json.loads(decoded.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("token segment must be an object")
    return parsed


class RunTokenService:
    def __init__(
        self,
        session: Session,
        *,
        signing_key: bytes,
        issuer: str = "iap-api",
        audience: str = "iap-runner-gateway",
        grace_seconds: int = 30,
        clock=None,
    ) -> None:
        if len(signing_key) < 32:
            raise ValueError("Runner token signing key must be at least 32 bytes")
        self.session = session
        self.signing_key = signing_key
        self.issuer = issuer
        self.audience = audience
        self.grace_seconds = grace_seconds
        self.clock = clock or (lambda: datetime.now(UTC))

    @classmethod
    def from_settings(
        cls, session: Session, settings: RunnerTokenSettings
    ) -> "RunTokenService":
        return cls(
            session,
            signing_key=settings.signing_key,
            issuer=settings.issuer,
            audience=settings.audience,
            grace_seconds=settings.grace_seconds,
        )

    def issue(
        self,
        snapshot: StoredExecutionSnapshot,
        actions: set[str],
        deadline_at: datetime,
    ) -> IssuedRunToken:
        now = self.clock().astimezone(UTC)
        if deadline_at.tzinfo is None or deadline_at.utcoffset() is None:
            raise ValueError("deadline_at must include timezone information")
        deadline = deadline_at.astimezone(UTC)
        expires_at = deadline + timedelta(seconds=self.grace_seconds)
        if snapshot.expires_at is not None:
            expires_at = min(expires_at, snapshot.expires_at.astimezone(UTC))
        if expires_at <= now:
            raise ValueError("Runner token expiration must be in the future")
        normalized_actions = tuple(sorted(actions))
        if not normalized_actions or not set(normalized_actions) <= _ALLOWED_ACTIONS:
            raise ValueError("Runner token actions are invalid")
        claims = RunTokenClaims(
            iss=self.issuer,
            aud=self.audience,
            jti=str(uuid4()),
            run_id=snapshot.run_id,
            unit_id=snapshot.payload.unit_id,
            project_id=snapshot.payload.project_id,
            snapshot_id=snapshot.snapshot_id,
            snapshot_digest=snapshot.digest,
            actions=normalized_actions,
            iat=now.timestamp(),
            nbf=now.timestamp(),
            exp=expires_at.timestamp(),
        )
        header = _encode_segment({"alg": "HS256", "typ": "JWT"})
        payload = _encode_segment(claims.model_dump())
        signing_input = f"{header}.{payload}".encode("ascii")
        signer = hmac.HMAC(self.signing_key, hashes.SHA256())
        signer.update(signing_input)
        signature = base64.urlsafe_b64encode(signer.finalize()).rstrip(b"=")
        return IssuedRunToken(
            f"{header}.{payload}.{signature.decode('ascii')}", claims
        )

    def verify(
        self, token: str, run_id: str, required_action: str
    ) -> RunTokenClaims:
        try:
            header_segment, payload_segment, signature_segment = token.split(".")
            header = _decode_segment(header_segment)
            if header != {"alg": "HS256", "typ": "JWT"}:
                raise ValueError("unsupported token header")
            padding = "=" * (-len(signature_segment) % 4)
            signature = base64.b64decode(
                signature_segment + padding, altchars=b"-_", validate=True
            )
            verifier = hmac.HMAC(self.signing_key, hashes.SHA256())
            verifier.update(f"{header_segment}.{payload_segment}".encode("ascii"))
            verifier.verify(signature)
            claims = RunTokenClaims.model_validate(_decode_segment(payload_segment))
        except (InvalidSignature, ValueError, TypeError, json.JSONDecodeError) as error:
            raise RunTokenInvalid("Runner token is invalid") from error

        now = self.clock().astimezone(UTC).timestamp()
        if claims.iss != self.issuer or claims.aud != self.audience:
            raise RunTokenInvalid("Runner token issuer or audience is invalid")
        if now < claims.nbf:
            raise RunTokenInvalid("Runner token is not active")
        if now >= claims.exp:
            raise RunTokenInvalid("Runner token is expired")
        if claims.run_id != run_id:
            raise RunTokenNotFound(run_id)
        if required_action not in claims.actions:
            raise RunTokenForbidden("Runner token action is forbidden")
        revocations = self.session.scalars(
            select(RuntimeRunTokenRevocation).where(
                or_(
                    RuntimeRunTokenRevocation.jti == claims.jti,
                    RuntimeRunTokenRevocation.run_id == claims.run_id,
                )
            )
        ).all()
        for revocation in revocations:
            revoked_at_value = revocation.revoked_at
            if revoked_at_value.tzinfo is None:
                revoked_at_value = revoked_at_value.replace(tzinfo=UTC)
            revoked_at = revoked_at_value.astimezone(UTC).timestamp()
            if revocation.jti == claims.jti or claims.iat <= revoked_at:
                raise RunTokenInvalid("Runner token is revoked")
        return claims

    def revoke(self, run_id: str, reason: str) -> None:
        existing = self.session.scalar(
            select(RuntimeRunTokenRevocation).where(
                RuntimeRunTokenRevocation.run_id == run_id
            ).with_for_update()
        )
        revoked_at = self.clock().astimezone(UTC)
        if existing is not None:
            current = existing.revoked_at
            if current.tzinfo is None:
                current = current.replace(tzinfo=UTC)
            existing.revoked_at = max(current.astimezone(UTC), revoked_at)
            existing.reason = reason[:120]
        else:
            self.session.add(
                RuntimeRunTokenRevocation(
                    jti=f"run:{run_id}",
                    run_id=run_id,
                    revoked_at=revoked_at,
                    reason=reason[:120],
                )
            )
        try:
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            existing = self.session.scalar(
                select(RuntimeRunTokenRevocation)
                .where(RuntimeRunTokenRevocation.run_id == run_id)
                .with_for_update()
            )
            if existing is None:
                raise
            current = existing.revoked_at
            if current.tzinfo is None:
                current = current.replace(tzinfo=UTC)
            existing.revoked_at = max(current.astimezone(UTC), revoked_at)
            existing.reason = reason[:120]
            self.session.commit()


__all__ = [
    "IssuedRunToken",
    "RunTokenClaims",
    "RunTokenForbidden",
    "RunTokenInvalid",
    "RunTokenNotFound",
    "RunTokenService",
    "RunnerTokenSettings",
    "RuntimeRunTokenRevocation",
]
