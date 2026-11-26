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
