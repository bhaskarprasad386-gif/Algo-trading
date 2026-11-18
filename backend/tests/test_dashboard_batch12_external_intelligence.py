from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAIN = ROOT / "backend" / "app" / "main.py"
ROUTE = ROOT / "backend" / "app" / "intelligence" / "routes.py"
WEB = ROOT / "web" / "dashboard" / "index.html"


def test_batch12_external_intelligence_route_is_registered_and_paper_safe():
    main = MAIN.read_text(encoding="utf-8")
    route = ROUTE.read_text(encoding="utf-8")
    assert 'from app.intelligence.routes import router as intelligence_router' in main
    assert 'app.include_router(intelligence_router)' in main
    assert 'prefix="/api/v1/intelligence"' in route
    assert 'https://m.rbi.org.in/home.aspx' in route
    assert 'https://dbie.rbihub.in/' in route
    assert 'live_orders' in route and '"OFF"' in route


def test_batch12_analyze_hub_consumes_external_macro_source_without_fabrication():
    ui = WEB.read_text(encoding="utf-8")
    for marker in (
        '/api/v1/intelligence/macro',
        'rbiCurrent',
        'rbiPrevious',
        'rbiChange',
        'rbiStatement',
        'analyzeRbiHealth',
        'SOURCE PENDING',
        'No policy text fabricated',
    ):
        assert marker in ui
