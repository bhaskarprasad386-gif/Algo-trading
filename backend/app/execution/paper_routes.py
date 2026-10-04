        db.rollback()
        raise HTTPException(status_code=409, detail="paper trading account is busy; retry") from exc
    return {"id": order.order_id, "symbol": order.symbol, "transaction_type": order.transaction_type, "price": price, "quantity": normalized_quantity, "status": order.status, "pnl": pnl, "fill_id": fill_id}



def _validate_paper_state(db: Session, user_id: int) -> None:
    """Fail closed before commit if a paper-account invariant is violated."""
    account = _account(db, user_id)
    # SessionLocal uses autoflush=False. Mutation callers can therefore have
    # newly-created/updated positions or orders that are not visible to the
    # invariant queries below unless we explicitly flush first. Include that
    # pending state in the same transaction before validating conservation.
    if db.new or db.dirty or db.deleted:
        db.flush()
    initial_virtual_balance = float(account.initial_virtual_balance)
    virtual_balance = float(account.virtual_balance)
    realized_pnl = float(account.realized_pnl or 0.0)
    if (
        not math.isfinite(initial_virtual_balance)
        or initial_virtual_balance <= 0
    ):
        raise RuntimeError("paper account initial_virtual_balance invariant violated")
    if not math.isfinite(virtual_balance) or virtual_balance < 0:
        raise RuntimeError("paper account virtual_balance invariant violated")
    if not math.isfinite(realized_pnl):
        raise RuntimeError("paper account realized_pnl invariant violated")

    paper_positions = (
        db.query(Position)
        .filter(Position.user_id == user_id, Position.is_paper.is_(True))
        .all()
    )
    open_position_cost = 0.0
    seen_symbols: set[str] = set()
    for position in paper_positions:
        symbol = str(position.symbol or "").strip().upper()