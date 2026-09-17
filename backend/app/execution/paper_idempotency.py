"""Durable request-idempotency primitive for paper execution."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Mapping

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class IdempotencyResult:
    replay: bool
    response: dict | None = None


def request_fingerprint(payload: Mapping[str, object]) -> str:
    """Return a stable digest for the semantic request payload."""
    canonical = json.dumps(payload, separators=(",", ":"), sort_keys=True, allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def claim(
    db: Session,
    *,
    user_id: int,
    scope: str,
    key: str,
    request_hash: str,
) -> IdempotencyResult:
    """Atomically claim a paper request key, or replay its completed response."""
    normalized = key.strip()
    if not normalized or len(normalized) > 128:
        raise ValueError("idempotency key must contain 1-128 non-whitespace characters")
    if len(request_hash) != 64 or any(ch not in "0123456789abcdef" for ch in request_hash):
        raise ValueError("request hash must be a lowercase SHA-256 hex digest")

    try:
        with db.begin_nested():
            db.execute(
                text(
                    "INSERT INTO paper_idempotency "
                    "(user_id, scope, idempotency_key, request_hash, response_json) "
                    "VALUES (:user_id, :scope, :key, :request_hash, NULL)"
                ),
                {
                    "user_id": user_id,
                    "scope": scope,
                    "key": normalized,
                    "request_hash": request_hash,
                },
            )
            db.flush()
            return IdempotencyResult(replay=False)
    except IntegrityError:
        row = db.execute(
            text(
                "SELECT request_hash, response_json FROM paper_idempotency "
                "WHERE user_id = :user_id AND scope = :scope AND idempotency_key = :key"
            ),
            {"user_id": user_id, "scope": scope, "key": normalized},
        ).first()
        if row is None:
            raise RuntimeError("paper idempotency claim disappeared")
        if row.request_hash != request_hash:
            raise ValueError("idempotency key was already used for a different request")
        if row.response_json is None:
            raise RuntimeError("paper request with this idempotency key is already in progress")
        return IdempotencyResult(replay=True, response=json.loads(row.response_json))


def complete(db: Session, *, user_id: int, scope: str, key: str, response: dict) -> None:
    """Persist the successful response in the same transaction as the execution."""
    result = db.execute(
        text(
            "UPDATE paper_idempotency SET response_json = :response "
            "WHERE user_id = :user_id AND scope = :scope AND idempotency_key = :key "
            "AND response_json IS NULL"
        ),
        {
            "response": json.dumps(response, separators=(",", ":"), sort_keys=True),
            "user_id": user_id,
            "scope": scope,
            "key": key.strip(),
        },
    )
    if result.rowcount != 1:
        raise RuntimeError("paper idempotency record was not claimed or already completed")
