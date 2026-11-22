from importlib import import_module
from pathlib import Path


def test_short_covering_engine_and_submodules_resolve_as_package():
    package = import_module("app.scanner.short_covering_long_unwinding")
    features = import_module("app.scanner.short_covering_long_unwinding.features")
    liquidity = import_module("app.scanner.short_covering_long_unwinding.liquidity")

    assert Path(package.__file__).name == "__init__.py"
    assert callable(package.evaluate_signal)
    assert package.robust_z is features.robust_z
    assert callable(liquidity.detect_sweep)
