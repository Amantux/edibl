"""Food-aware conversion: the foodcore seam and `quantity.convert_for_food`.

`convert` converts within a dimension and refuses across one because it has no
food. `convert_for_food` is allowed to cross when a specific food is named, and
must refuse in exactly the same way whenever the reference data cannot justify
an answer. These tests pin both halves — the answers it now gives AND the ones
it still declines to give.
"""
import os

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


def test_volume_to_mass_resolves_for_a_small_measure_too():
    assert convert_for_food(1, "tsp", "g", "garlic") == pytest.approx(2.8)


# ---- the conversions it still refuses -------------------------------------- #
def test_ambiguous_food_name_refuses_rather_than_picking_one():
    """Raw rice is 185 g a cup, cooked is 158 g. foodcore refuses; so do we."""
    assert convert_for_food(1, "cup", "g", "rice") is None


@pytest.mark.parametrize("food", ["", "   ", "zzzz not a food zzzz"])
def test_no_usable_food_means_no_cross_dimension_answer(food):
    assert convert_for_food(1, "cup", "g", food) is None


def test_an_estimated_conversion_is_refused_rather_than_passed_off_as_a_weight():
    """A garlic clove comes back method="estimated", confidence 0.55 — foodcore
    applied a convention. Edibl distinguishes exact from estimated (ADR-0002)
    and a bare float cannot carry that, so the answer is refused rather than
    returned as if it were the published 339 g of a cup of honey."""
    assert food_reference.convert_amount(2, "clove", "g", "garlic") is None
    assert convert_for_food(2, "clove", "g", "garlic") is None


@pytest.mark.parametrize("unit", [
    # Edibl's non-measurement dimensions.
    "can", "pack", "bottle", "jar", "serving", "half", "low", "full", "presence",
    # Units Edibl does NOT register, which foodcore WOULD reinterpret: "c" as
    # cup (as likely to be a can here), "t" as tsp, "#" as lb, "gr" as gram.
    # A block-list of dimensions let every one of these through.
    "c", "t", "#", "gr", "container", "pouch", "envelope", "gizmo",
])
def test_a_unit_edibl_does_not_measure_in_never_reaches_the_reference_data(
        unit, monkeypatch):
    """The guard, not foodcore, is what refuses these.

    Asserting the seam is never consulted is what makes the test fail if the
    guard is weakened; asserting None would pass with no guard at all — and for
    "c", "t" and "#" it would have passed while foodcore quietly answered in a
    unit the user did not mean."""
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


def test_the_expansion_directory_is_private_to_this_user(monkeypatch, tmp_path):
    """The path is predictable so gunicorn's workers share one expansion. That
    makes it somewhere another local user could plant a file for us to open as
    reference data, so it must be ours and 0700."""
    import stat

    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))

    directory = food_reference._expansion_dir()

    assert directory.startswith(str(tmp_path))
    mode = os.lstat(directory).st_mode
    assert stat.S_ISDIR(mode)
    assert not mode & (stat.S_IRWXG | stat.S_IRWXO)


def test_a_world_writable_expansion_directory_is_refused(monkeypatch, tmp_path):
    """Refuses rather than takes over: a directory anyone can write is not a
    situation to silently adopt, and "" is just today's behaviour."""
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    hostile = tmp_path / f"edibl-foodcore-{os.geteuid()}"
    hostile.mkdir(mode=0o777)
    os.chmod(hostile, 0o777)

    assert food_reference._expansion_dir() == ""
    assert food_reference._shipped_dataset_path() == ""
    assert convert_for_food(1, "cup", "g", "honey") is None


def test_a_symlink_where_the_dataset_belongs_is_not_opened(monkeypatch, tmp_path):
    """lstat, not stat: a planted symlink is replaced, never followed to
    whatever it points at — otherwise every weight the app reports could be
    swapped out from under it."""
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    directory = tmp_path / f"edibl-foodcore-{os.geteuid()}"
    directory.mkdir(mode=0o700)
    planted = tmp_path / "attacker-owned.sqlite"
    planted.write_text("not the reference dataset")
    link = directory / "usda-sr-legacy-2018-04.sqlite"
    link.symlink_to(planted)

    path = food_reference._shipped_dataset_path()

    assert path and not os.path.islink(path)
    assert planted.read_text() == "not the reference dataset"   # untouched
    food_reference.reset_cache()
    assert convert_for_food(1, "cup", "g", "honey") == pytest.approx(339.0)


def test_a_corrupt_shipped_asset_does_not_raise_and_leaves_no_partial(
        monkeypatch, tmp_path):
    """A truncated gzip raises EOFError or zlib.error, NEITHER of which is an
    OSError — so catching OSError alone both broke "never raises" and skipped
    the cleanup, leaking a near-10 MB partial on every worker boot."""
    import gzip

    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    fake_package = tmp_path / "pkg"
    (fake_package / "data").mkdir(parents=True)
    asset = fake_package / "data" / "usda-sr-legacy-2018-04.sqlite.gz"
    asset.write_bytes(gzip.compress(b"x" * 4096)[:20])        # truncated mid-stream
    monkeypatch.setattr("foodcore.__file__", str(fake_package / "__init__.py"))
    food_reference.reset_cache()

    assert food_reference._shipped_dataset_path() == ""
    leftovers = os.listdir(tmp_path / f"edibl-foodcore-{os.geteuid()}")
    assert leftovers == []
    assert convert_for_food(1, "cup", "g", "honey") is None


def test_a_transient_failure_is_not_cached_forever(monkeypatch):
    """A boot-time ENOSPC used to disable food-aware conversion in that worker
    until someone restarted it — the None was cached with no retry."""
    monkeypatch.setattr(food_reference, "_shipped_dataset_path", lambda: "")
    monkeypatch.delenv("FOODCORE_DATASET", raising=False)
    food_reference.reset_cache()
    assert food_reference._dataset() is None

    monkeypatch.undo()
    monkeypatch.delenv("FOODCORE_DATASET", raising=False)

    assert food_reference._dataset() is not None       # recovers without a reset


def test_the_resolution_cache_is_bounded(monkeypatch):
    """Food names come from user-supplied product text, so this cannot grow
    without limit once there is a caller."""
    monkeypatch.setattr(food_reference, "_FOOD_CACHE_MAX", 8)
    food_reference.reset_cache()

    for i in range(40):
        food_reference._resolve_food(f"not a food {i}")

    assert len(food_reference._food_cache) <= 8


def test_an_unopenable_dataset_is_reported_not_raised(monkeypatch, tmp_path):
    bad = tmp_path / "not-a-database.sqlite"
    bad.write_text("this is not sqlite")
    monkeypatch.setenv("FOODCORE_DATASET", str(bad))
    food_reference.reset_cache()

    assert food_reference._dataset() is None
    assert convert_for_food(1, "cup", "g", "honey") is None
