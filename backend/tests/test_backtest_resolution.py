from backend.app.backtesting.backtest_resolution import (
    ResolutionCoverage,
    choose_finest_genuine_resolution,
)


def test_prefers_milliseconds_when_complete():
    result = choose_finest_genuine_resolution(
        [
            ResolutionCoverage("h", True, 0, 100, "hourly"),
            ResolutionCoverage("s", True, 0, 100, "seconds"),
            ResolutionCoverage("ms", True, 0, 100, "milliseconds"),
        ]
    )
    assert result.resolution == "ms"


def test_falls_back_to_seconds_without_fabricating_ms():
    result = choose_finest_genuine_resolution(
        [
            ResolutionCoverage("ms", False, 0, 100, "partial-ms"),
            ResolutionCoverage("s", True, 0, 100, "seconds"),
            ResolutionCoverage("m", True, 0, 100, "minutes"),
        ]
    )
    assert result.resolution == "s"


def test_falls_back_to_minutes_then_hours():
    result = choose_finest_genuine_resolution(
        [
            ResolutionCoverage("ms", False, 0, 100, "none"),
            ResolutionCoverage("s", False, 0, 100, "partial"),
            ResolutionCoverage("m", True, 0, 100, "minutes"),
            ResolutionCoverage("h", True, 0, 100, "hours"),
        ]
    )
    assert result.resolution == "m"


def test_requires_complete_genuine_coverage():
    try:
        choose_finest_genuine_resolution(
            [ResolutionCoverage("ms", False, 0, 100, "partial")]
        )
    except ValueError as exc:
        assert "complete genuine resolution" in str(exc)
    else:
        raise AssertionError("expected incomplete coverage to reject the run")
