import math

import pytest

from app.backtesting.provenance import canonical_provenance_json, provenance_hash


def test_mapping_order_does_not_change_provenance_hash() -> None:
    left = {"b": {"y": 2, "x": 1}, "a": [1, 2]}
    right = {"a": [1, 2], "b": {"x": 1, "y": 2}}
    assert provenance_hash(left) == provenance_hash(right)
    assert canonical_provenance_json(left) == canonical_provenance_json(right)


def test_type_identity_is_preserved() -> None:
    assert provenance_hash({"value": 1}) != provenance_hash({"value": 1.0})
    assert provenance_hash({"value": True}) != provenance_hash({"value": 1})
    assert provenance_hash({"value": [1, 2]}) != provenance_hash({"value": (1, 2)})


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_non_finite_float_is_rejected(value: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        canonical_provenance_json({"value": value})


@pytest.mark.parametrize("value", [b"bytes", {1, 2}, object()])
def test_unsupported_values_are_rejected(value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        canonical_provenance_json({"value": value})


def test_non_string_mapping_key_is_rejected() -> None:
    with pytest.raises(TypeError, match="keys must be strings"):
        canonical_provenance_json({1: "value"})
