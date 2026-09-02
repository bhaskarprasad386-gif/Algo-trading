from app.execution import paper_routes


def test_paper_position_lifecycle():
    paper_routes._paper_position = None

    entry = paper_routes.paper_entry(
        paper_routes.PaperEntryRequest(price=100.0, quantity=2.0, stop_loss_pct=0.02, target_pct=0.04)
    )
    assert entry["status"] == "success"
    assert entry["position"]["entry_price"] == 100.0
    assert entry["position"]["stop_loss"] == 98.0
    assert entry["position"]["target"] == 104.0

    active = paper_routes.paper_position()
    assert active["status"] == "active"
    assert active["position"]["quantity"] == 2.0

    exit_result = paper_routes.paper_exit(paper_routes.PaperExitRequest(price=103.5))
    assert exit_result == {
        "status": "closed",
        "entry_price": 100.0,
        "exit_price": 103.5,
        "quantity": 2.0,
        "pnl": 7.0,
    }

    flat = paper_routes.paper_position()
    assert flat == {"status": "flat", "position": None}


def test_paper_exit_without_position_is_flat():
    paper_routes._paper_position = None
    result = paper_routes.paper_exit(paper_routes.PaperExitRequest(price=101.0))
    assert result == {"status": "flat", "position": None, "pnl": 0.0}
