from app.main import app


def test_auth_routes_are_not_registered():
    auth_paths = {
        route.path
        for route in app.routes
        if getattr(route, "path", "").startswith("/api/v1/auth")
    }
    assert auth_paths == set()


def test_google_login_configuration_is_removed():
    assert not hasattr(__import__("app.core.config", fromlist=["settings"]).settings, "GOOGLE_WEB_CLIENT_ID")
