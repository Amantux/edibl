"""Food-aware conversion: the foodcore seam and `quantity.convert_for_food`.

`convert` converts within a dimension and refuses across one because it has no
food. `convert_for_food` is allowed to cross when a specific food is named, and
must refuse in exactly the same way whenever the reference data cannot justify
an answer. These tests pin both halves — the answers it now gives AND the ones
it still declines to give.
"""
import pytest

from app.services import food_reference
from app.services.quantity import convert, convert_for_food


@pytest.fixture(autouse=True)
def _fresh_dataset():
    food_reference.reset_cache()
    yield
    food_reference.reset_cache()


class _Spy:
    """Stands in for the foodcore seam and records that it was consulted."""

    def __init__(self):
        self.calls = []

    def __call__(self, value, from_unit, to_unit, food):
        self.calls.append((value, from_unit, to_unit, food))
        return 1.0


# ---- the conversions it can now answer ------------------------------------- #
def test_mass_to_volume_resolves_for_a_named_food():
    """A cup of honey weighs 339 g — USDA publishes it, so this is a fact.

    `convert` cannot answer it (no food, no density) and must not start to."""
    assert convert(1, "cup", "g") is None

    assert convert_for_food(1, "cup", "g", "honey") == pytest.approx(339.0)


def test_cross_dimension_round_trip_returns_the_original_amount():
    """Both directions come off the same published portion, so they invert.

    An asymmetric conversion here would silently over- or under-draw stock:
    a caller that converts a lot into a demand unit and the take back again
    would credit itself with an amount it never deducted."""
    as_cups = convert_for_food(1000, "g", "cup", "honey")
    assert as_cups is not None

    assert convert_for_food(as_cups, "cup", "g", "honey") == pytest.approx(1000.0)


def test_food_specific_count_unit_resolves():
    """A clove is a garlic measure, not a unit — only a food can price it."""
    assert convert(2, "clove", "g") is None

    assert convert_for_food(2, "clove", "g", "garlic") == pytest.approx(6.0)


# ---- the conversions it still refuses -------------------------------------- #
def test_ambiguous_food_name_refuses_rather_than_picking_one():
    """Raw rice is 185 g a cup, cooked is 158 g. foodcore refuses; so do we."""
    assert convert_for_food(1, "cup", "g", "rice") is None


@pytest.mark.parametrize("food", ["", "   ", "zzzz not a food zzzz"])
def test_no_usable_food_means_no_cross_dimension_answer(food):
    assert convert_for_food(1, "cup", "g", food) is None


def test_unknown_unit_pair_refuses_even_with_a_good_food():
    assert convert_for_food(1, "gizmo", "widget", "honey") is None


@pytest.mark.parametrize("unit", ["can", "pack", "bottle", "serving",
                                  "half", "low", "full", "presence"])
def test_package_and_qualitative_units_never_reach_the_reference_data(unit, monkeypatch):
    """The guard, not foodcore, is what refuses these.

    foodcore declines them today too, but as *unit lookups* — the day it learns
    a unit spelled "half" this guard is the only thing stopping "half a jar"
    being read as half a cup. Asserting the seam is never consulted is what
    makes the test fail if the guard is deleted; asserting None would pass
    with no guard at all."""
    spy = _Spy()
    monkeypatch.setattr(food_reference, "convert_amount", spy)

    assert convert_for_food(1, unit, "g", "honey") is None
    assert convert_for_food(1, "g", unit, "honey") is None
    assert spy.calls == []


# ---- within a dimension nothing changed ------------------------------------ #
@pytest.mark.parametrize("value,fu,tu,expected", [
    (1500, "g", "kg", 1.5), (2, "kg", "g", 2000), (1, "cup", "ml", 236.5882365),
    (3, "count", "count", 3), (1, "dozen", "count", 12),
])
def test_same_dimension_matches_plain_convert_exactly(value, fu, tu, expected,
                                                      monkeypatch):
    """g→kg is exact Decimal arithmetic and must not acquire a dependency on
    whether a reference dataset happens to recognise the food."""
    spy = _Spy()
    monkeypatch.setattr(food_reference, "convert_amount", spy)

    assert convert_for_food(value, fu, tu, "honey") == pytest.approx(expected)
    assert convert_for_food(value, fu, tu, "") == convert(value, fu, tu)
    assert spy.calls == []


def test_plain_convert_contract_is_unchanged():
    """The signature and the across-dimension refusal other callers rely on."""
    assert convert(1, "kg", "g") == 1000
    assert convert(1, "g", "ml") is None        # no food in scope → no density
    assert convert(1, "can", "can") == 1        # unit-for-unit only
    assert convert(1, "can", "g") is None
    assert convert(None, "g", "kg") is None


# ---- degradation ----------------------------------------------------------- #
def test_no_reference_dataset_degrades_to_the_plain_conversion(monkeypatch):
    """An absent library or a dropped data asset costs the refinement, not the
    app: every answer becomes what `convert` already returned."""
    monkeypatch.setattr(food_reference, "_shipped_dataset_path", lambda: "")
    monkeypatch.delenv("FOODCORE_DATASET", raising=False)
    food_reference.reset_cache()

    assert food_reference._dataset() is None
    assert convert_for_food(1, "cup", "g", "honey") is None
    assert convert_for_food(1500, "g", "kg", "honey") == 1.5


def test_an_unopenable_dataset_is_reported_not_raised(monkeypatch, tmp_path):
    bad = tmp_path / "not-a-database.sqlite"
    bad.write_text("this is not sqlite")
    monkeypatch.setenv("FOODCORE_DATASET", str(bad))
    food_reference.reset_cache()

    assert food_reference._dataset() is None
    assert convert_for_food(1, "cup", "g", "honey") is None
