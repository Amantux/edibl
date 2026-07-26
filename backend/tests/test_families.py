"""Heuristic generic-family (brand-strip) derivation."""
import pytest

from app.services.families import generic_family


@pytest.mark.parametrize("name,family", [
    ("Wegmans Teriyaki Marinade", "Teriyaki Marinade"),
    ("Kirkland Signature Olive Oil", "Olive Oil"),
    ("Great Value Whole Milk", "Whole Milk"),
    ("365 Organic Eggs", "Organic Eggs"),
    ("Teriyaki Marinade", ""),        # no known brand prefix → no strip
    ("Wegmans", ""),                  # brand alone → don't return the brand
    ("", ""),
])
def test_generic_family(name, family):
    assert generic_family(name) == family
