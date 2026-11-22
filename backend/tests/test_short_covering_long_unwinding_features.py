from app.scanner.short_covering_long_unwinding.features import robust_z, normalized_momentum, directional_pressure

def test_robust_z_filters_bad_values_and_clips_extremes():
    assert robust_z("bad", [1,2,3,4,5,6,7,8]) is None
    assert robust_z(float("nan"), [1,2,3,4,5,6,7,8]) is None
    assert robust_z(10000, [1,2,3,4,5,6,7,8]) == 5.0
    assert robust_z(4, [4]*8) is None

def test_normalized_momentum_requires_positive_scale():
    assert normalized_momentum(12, 10, 2) == 1.0
    assert normalized_momentum(8, 10, 2) == -1.0
    assert normalized_momentum(10, 9, 0) is None
    assert normalized_momentum("x", 9, 1) is None

def test_directional_pressure_keeps_missing_independent():
    bull, bear = directional_pressure(positive=.7, negative=None)
    assert bull == .7 and bear is None
    assert directional_pressure(positive=-.4, negative=2) == (0,1)
