import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.execution.paper_idempotency import claim, complete, request_fingerprint


def _db():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE paper_idempotency (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                scope VARCHAR(64) NOT NULL,
                idempotency_key VARCHAR(128) NOT NULL,
                request_hash VARCHAR(64) NOT NULL,
                response_json TEXT,
                created_at DATETIME
            )
        """))
        connection.execute(text("CREATE UNIQUE INDEX uq_paper_idempotency_identity ON paper_idempotency (user_id, scope, idempotency_key)"))
    return engine


def _hash(payload):
    return request_fingerprint(payload)


def test_claim_and_complete_replays_exact_response():
    engine = _db()
    with Session(engine) as db:
        request = {"symbol": "NIFTY", "price": 100.0, "quantity": 1}
        digest = _hash(request)
        first = claim(db, user_id=7, scope="paper/order", key="request-1", request_hash=digest)
        assert first.replay is False
        response = {"status": "success", "order": {"id": "PAPER-7-abc"}, "virtual_balance": 999.0}
        complete(db, user_id=7, scope="paper/order", key="request-1", response=response)
        db.commit()

        replay = claim(db, user_id=7, scope="paper/order", key="request-1", request_hash=digest)
        assert replay.replay is True
        assert replay.response == response


def test_same_key_is_scoped_by_user_and_operation():
    engine = _db()
    with Session(engine) as db:
        digest = _hash({"symbol": "NIFTY", "price": 100.0, "quantity": 1})
        assert claim(db, user_id=1, scope="paper/order", key="same", request_hash=digest).replay is False
        complete(db, user_id=1, scope="paper/order", key="same", response={"user": 1})
        db.commit()

        assert claim(db, user_id=2, scope="paper/order", key="same", request_hash=digest).replay is False
        complete(db, user_id=2, scope="paper/order", key="same", response={"user": 2})
        db.commit()

        assert claim(db, user_id=1, scope="paper/exit", key="same", request_hash=digest).replay is False


def test_in_progress_request_is_not_reexecuted():
    engine = _db()
    with Session(engine) as db:
        digest = _hash({"symbol": "NIFTY", "price": 100.0, "quantity": 1})
        assert claim(db, user_id=1, scope="paper/order", key="busy", request_hash=digest).replay is False
        db.commit()

        with pytest.raises(RuntimeError, match="already in progress"):
            claim(db, user_id=1, scope="paper/order", key="busy", request_hash=digest)


def test_same_key_with_different_request_is_rejected():
    engine = _db()
    with Session(engine) as db:
        first = _hash({"symbol": "NIFTY", "price": 100.0, "quantity": 1})
        second = _hash({"symbol": "NIFTY", "price": 101.0, "quantity": 1})
        assert claim(db, user_id=1, scope="paper/order", key="reuse", request_hash=first).replay is False
        complete(db, user_id=1, scope="paper/order", key="reuse", response={"status": "success"})
        db.commit()

        with pytest.raises(ValueError, match="different request"):
            claim(db, user_id=1, scope="paper/order", key="reuse", request_hash=second)
