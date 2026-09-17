"""Durable request-idempotency primitive for paper execution."""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class IdempotencyResult:
    replay: bool
    response: dict | None = None


def claim(db: Session, *, user_id: int, scope: str, key: str) -> IdempotencyResult:
    """Atomically claim a paper request key, or replay its completed response."""
    normalized = key.strip()
    if not normalized or len(normalized) > 128:
        raise ValueError("idempotency key must contain 1-128 non-whitespace characters")

    try:
        with db.begin_nested():
            db.execute(
                text(
                    "INSERT INTO paper_idempotency "
                    "(user_id, scope, idempotency_key, response_json) "
                    "VALUES (:user_id, :scope, :key, NULL)"
                ),
                {"user_id": user_id, "scope": scope, "key": normalized},
            )
            db.flush()
            return IdempotencyResult(replay=False)
    except IntegrityError:
        row = db.execute(
            text(
                "SELECT response_json FROM paper_idempotency "
                "WHERE user_id = :user_id AND scope = :scope AND idempotency_key = :key"
            ),
            {"user_id": user_id, "scope": scope, "key": normalized},
        ).first()
        if row is None:
            raise RuntimeError("paper idempotency claim disappeared")
        if row.response_json is None:
            raise RuntimeError("paper request with this idempotency key is already in progress")
        return IdempotencyResult(replay=True, response=json.loads(row.response_json))


def complete(db: Session, *, user_id: int, scope: str, key: str, response: dict) -> None:
    """Persist the successful response in the same transaction as the execution."""
    result = db.execute(
        text(
            "UPDATE paper_idempotency SET response_json = :response "
            "WHERE user_id = :user_id AND scope = :scope AND idempotency_key = :key"
        ),
        {
            "response": json.dumps(response, separators=(",", ":"), sort_keys=True),
            "user_id": user_id,
            "scope": scope,
            "key": key.strip(),
        },
    )
    if result.rowcount != 1:
        raise RuntimeError("paper idempotency record was not claimed")
