from datetime import datetime, timedelta, timezone

def test_paper_position_metadata_corruption_fails_closed_and_reconciles(tmp_path):
    from app.execution.paper_routes import PaperOrderRequest, paper_order, _reconcile_paper_ledger, _validate_paper_state
    engine = create_engine(f"sqlite:///{tmp_path / 'position-metadata.db'}", connect_args={"check_same_thread": False, "timeout": 10})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        user = User(email="position-meta@example.com", hashed_password="", full_name="Position Meta", is_active=True)
        db.add(user)
        db.flush()
        db.add(TradingAccount(user_id=user.id, mode="PAPER", virtual_balance=1000.0,
                               initial_virtual_balance=1000.0, initial_balance_source="BOOTSTRAP",
                               realized_pnl=0.0, is_active=True))
        db.commit()
        paper_order(PaperOrderRequest(symbol="META", transaction_type="BUY", price=10.0, quantity=1, fill_id="META-1"),
                    user_id=user.id, db=db)
        position = db.query(Position).filter(Position.user_id == user.id, Position.symbol == "META").one()
        position.product_type = "MIS"
        position.stop_loss = -1.0
        position.updated_at = position.created_at - timedelta(seconds=1)
        db.commit()
        try:
            _validate_paper_state(db, user.id)
            raise AssertionError("corrupt position metadata must fail closed")
        except RuntimeError as exc:
            assert "paper position invariant" in str(exc)
        result = _reconcile_paper_ledger(db, user.id)
        assert result["status"] == "MISMATCH"
        assert any(item.startswith("invalid_position:") for item in result["mismatches"])
        assert result["repairability"] == "BLOCKED"
        assert result["repair_plan"]["apply"] is False
    finally:
        db.close()
        engine.dispose()
