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
