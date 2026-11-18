from pathlib import Path


DEPRECATED_CALL = "datetime.utcnow"


def test_model_package_has_no_deprecated_utcnow_calls() -> None:
    models_dir = Path(__file__).parents[1] / "app" / "models"
    offenders: list[str] = []

    for path in sorted(models_dir.glob("*.py")):
        if DEPRECATED_CALL in path.read_text(encoding="utf-8"):
            offenders.append(str(path.relative_to(models_dir.parent.parent)))

    assert offenders == [], f"Deprecated datetime.utcnow remains in model files: {offenders}"
