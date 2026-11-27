from app import main


def test_box_spread_index_selection_api_is_not_registered():
    paths = {
        (getattr(route, "path", None), frozenset(getattr(route, "methods", set()) or set()))
        for route in main.app.routes
    }
    assert not any(path == "/api/v1/scanner/box-spread/index-selection" for path, _ in paths)
