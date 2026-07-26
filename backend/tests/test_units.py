"""Canonical unit normalization."""
import pytest

from app.services.units import canonical_unit


@pytest.mark.parametrize("raw,canon", [
    ("Units", "count"), ("pieces", "count"), ("Piece", "count"), ("pcs", "count"),
    ("each", "count"), ("", "count"), (None, "count"),
    ("Grams", "g"), ("kilogram", "kg"), ("Ounce", "oz"), ("lbs", "lb"),
    ("litre", "l"), ("milliliters", "ml"), ("packet", "pack"), ("Bottles", "bottle"),
    ("count", "count"), ("oz", "oz"),
    ("jar", "jar"),  # unknown → passes through lowercased, never mis-mapped
])
def test_canonical_unit(raw, canon):
    assert canonical_unit(raw) == canon
