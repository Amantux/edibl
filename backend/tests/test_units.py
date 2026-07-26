"""Canonical unit normalization + dimensional awareness (myMeal-aligned)."""
import pytest

from app.services.units import canonical_unit, dimension


@pytest.mark.parametrize("raw,canon", [
    ("Units", "count"), ("pieces", "count"), ("Piece", "count"), ("pcs", "count"),
    ("each", "count"), ("", "count"), (None, "count"),
    ("Grams", "g"), ("kilogram", "kg"), ("Ounce", "oz"), ("lbs", "lb"),
    ("litre", "l"), ("milliliters", "ml"), ("packet", "pack"), ("Bottles", "bottle"),
    ("count", "count"), ("oz", "oz"),
    # measure vocabulary shared with myMeal
    ("cups", "cup"), ("Tablespoon", "tbsp"), ("tsp", "tsp"), ("fluid ounces", "fl oz"),
    ("gallons", "gallon"), ("qt", "quart"), ("jars", "jar"), ("boxes", "box"),
    ("sprig", "sprig"),  # unknown → passes through lowercased, never mis-mapped
])
def test_canonical_unit(raw, canon):
    assert canonical_unit(raw) == canon


@pytest.mark.parametrize("unit,dim", [
    ("count", "count"), ("pieces", "count"), ("pack", "count"), ("box", "count"),
    ("g", "weight"), ("kg", "weight"), ("Ounce", "weight"), ("lbs", "weight"),
    ("ml", "volume"), ("cups", "volume"), ("Tablespoon", "volume"), ("gallon", "volume"),
    ("sprig", None), ("", "count"),  # blank canonicalizes to count
])
def test_dimension(unit, dim):
    assert dimension(unit) == dim
