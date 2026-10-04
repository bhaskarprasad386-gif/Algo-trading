

def test_paper_create_order_flush_integrity_failure_rolls_back_and_normalizes(tmp_path, monkeypatch):
    from sqlalchemy.exc import IntegrityError
    from app.execution.paper_routes import _create_order

    engine = create_engine(f"sqlite:///{tmp_path / 'flush-integrity.db'}", connect_args={"check_same_thread": False, "timeout": 10})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        user = User(email="flush-integrity@example.com", hashed_password="", full_name="Flush Integrity", is_active=True)
        db.add(user)
        db.flush()
        db.add(TradingAccount(
            user_id=user.id, mode="PAPER", virtual_balance=1000.0,
            initial_virtual_balance=1000.0, initial_balance_source="BOOTSTRAP",
            realized_pnl=0.0, is_active=True,
        ))
        db.commit()

        original_flush = db.flush
        def fail_flush(*args, **kwargs):
            raise IntegrityError("forced", {}, Exception("forced"))
        monkeypatch.setattr(db, "flush", fail_flush)
        try:
            _create_order(db, user_id=user.id, symbol="FLUSH", side="BUY", price=10.0, quantity=1, fill_id="FLUSH-1")
            raise AssertionError("flush integrity failure must be normalized")
        except HTTPException as exc:
            assert exc.status_code == 409
            assert "conflicts with existing ledger state" in str(exc.detail)
    finally:
        db.close()
        engine.dispose()
