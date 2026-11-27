import ast
from pathlib import Path


def test_dashboard_websocket_client_registry_is_initialized_at_module_scope():
    main_path = Path(__file__).resolve().parents[1] / "app" / "main.py"
    tree = ast.parse(main_path.read_text(encoding="utf-8"))

    initialized = False
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == "_dashboard_clients":
                value = node.value
                initialized = (
                    isinstance(value, ast.Call)
                    and isinstance(value.func, ast.Name)
                    and value.func.id == "set"
                )
                break

    assert initialized, (
        "_dashboard_clients must be initialized at module scope before the "
        "publisher task or /ws/dashboard handler can access it"
    )


def test_dashboard_snapshot_is_serialized_once_before_client_fanout():
    main_path = Path(__file__).resolve().parents[1] / "app" / "main.py"
    tree = ast.parse(main_path.read_text(encoding="utf-8"))
    functions = {
        node.name: node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    frame = functions["_build_dashboard_snapshot_frame"]
    frame_calls = [
        node for node in ast.walk(frame)
        if isinstance(node, ast.Call)
    ]
    assert any(
        isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "json"
        and node.func.attr == "dumps"
        for node in frame_calls
    )

    sender = functions["_send_dashboard_snapshot"]
    sender_calls = [
        node for node in ast.walk(sender)
        if isinstance(node, ast.Call)
    ]
    assert any(
        isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "client"
        and node.func.attr == "send_text"
        for node in sender_calls
    )
    assert not any(
        isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "client"
        and node.func.attr == "send_json"
        for node in sender_calls
    )


def test_dashboard_dispatch_wait_formula_uses_elapsed_residual_and_clamps_to_zero():
    main_path = Path(__file__).resolve().parents[1] / "app" / "main.py"
    tree = ast.parse(main_path.read_text(encoding="utf-8"))
    publisher = next(
        node for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "_dashboard_snapshot_publisher_loop"
    )
    measurement = next(
        node for node in ast.walk(publisher)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name)
            and target.id == "build_dispatch_wait_seconds"
            for target in node.targets
        )
        and node.value is not None
        and any(
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and child.func.attr == "monotonic"
            for child in ast.walk(node.value)
        )
    )

    expression = compile(
        ast.Expression(measurement.value),
        filename=str(main_path),
        mode="eval",
    )

    class Clock:
        def __init__(self, now):
            self.now = now

        def monotonic(self):
            return self.now

    measured = eval(
        expression,
        {"time_module": Clock(10.46)},
        {
            "dispatch_started": 10.0,
            "build_seconds": 0.25,
            "serialization_seconds": 0.03,
        },
    )
    assert abs(measured - 0.18) < 1e-9

    clamped = eval(
        expression,
        {"time_module": Clock(10.10)},
        {
            "dispatch_started": 10.0,
            "build_seconds": 0.25,
            "serialization_seconds": 0.03,
        },
    )
    assert clamped == 0.0


def test_dashboard_publisher_records_the_measured_dispatch_wait():
    main_path = Path(__file__).resolve().parents[1] / "app" / "main.py"
    tree = ast.parse(main_path.read_text(encoding="utf-8"))
    publisher = next(
        node for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "_dashboard_snapshot_publisher_loop"
    )

    record_calls = [
        node for node in ast.walk(publisher)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "record_publish"
    ]
    assert any(
        keyword.arg == "build_dispatch_wait_seconds"
        and isinstance(keyword.value, ast.Name)
        and keyword.value.id == "build_dispatch_wait_seconds"
        for call in record_calls
        for keyword in call.keywords
    )


def test_dashboard_payload_contains_only_box_spread_read_model():
    main_path = Path(__file__).resolve().parents[1] / "app" / "main.py"
    tree = ast.parse(main_path.read_text(encoding="utf-8"))
    payload = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_build_dashboard_snapshot_payload"
    )
    source = ast.unparse(payload)
    assert '"box_spread"' in source
    assert "live_cash_future_scanner" not in source
    assert "live_calendar_spread_scanner" not in source
    assert "live_synthetic_latest_results" not in source
    assert '"cash_future"' not in source
    assert '"calendar_spread"' not in source
    assert '"synthetic_arbitrage"' not in source


def test_retired_cash_future_page_push_provider_is_absent():
    main_path = Path(__file__).resolve().parents[1] / "app" / "main.py"
    source = main_path.read_text(encoding="utf-8")
    assert "def _cash_future_page_push_snapshot" not in source
    assert 'publish_page_update("cash-future"' not in source
    assert "async def _live_cash_future_loop" not in source
    assert "async def _cash_future_history_loop" not in source
