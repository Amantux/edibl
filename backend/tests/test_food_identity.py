"""A food identity persisted on Product, and the deduction it unblocks.

`convert_for_food` existed with no production caller because of IDENTITY, not
arithmetic (ADR-0005): the only food name available in a consumption path is the
product's free text, and resolving it *at the moment of a deduction* is the
guess ADR-0003 reserves for the matching service. `Product.food_key` is the
prerequisite — an identity chosen once, deliberately, and looked up by exact key.

Two things these tests exist to hold down, because both are corrections in
someone's pantry if they slip:

1. **A low-confidence match persists NOTHING.** An identity stored from a guess
   is inherited silently by every later conversion, which is worse than no
   identity at all.
2. **The round-trip is gone.** ADR-0005 claimed foodcore's two directions
   "invert exactly". That is true of honey and false in general — see
   `test_foodcore_directions_do_not_always_invert`.
"""
import pytest

from app.extensions import db
from app.models import Product
from app.services import food_reference as fr
from app.services.inventory import selection
from app.services.quantity import convert, convert_for_food_key

# Two real foods from the shipped USDA snapshot, picked for opposite reasons.
HONEY = "usda:169640"      # one cup portion -> its directions DO invert
ALFALFA = "usda:168384"    # publishes 1 cup = 33 g AND 1 tbsp = 3 g, which
                           # disagree (16 tbsp/cup would be 48 g) -> they do NOT


@pytest.fixture(autouse=True)
def _fresh_dataset():
    fr.reset_cache()
    yield
    fr.reset_cache()


@pytest.fixture()
def version():
    """The dataset version the loaded snapshot actually declares."""
    return fr.dataset_version("usda")


class _Lot:
    """A stock lot, without the database. `plan_consumption` takes any object."""

    def __init__(self, quantity, unit, food_key="", dataset_version="",
                 created_at=0):
        self.quantity, self.unit = quantity, unit
        self.finished, self.package_state, self.expiry_date = False, "", None
        self.created_at = created_at
        self.product = type("P", (), {
            "food_key": food_key, "food_key_dataset_version": dataset_version})()


# --------------------------------------------------------------------------- #
# The evidence: foodcore's two directions do not always invert
# --------------------------------------------------------------------------- #
def test_foodcore_directions_do_not_always_invert():
    """The counter-example to ADR-0005's "both come off the same published
    portion, so they invert".

    They do for honey, which is the only food the original round-trip test
    checked. Alfalfa sprouts publish a cup portion AND a tbsp portion that
    disagree with each other, so foodcore answers tbsp->g from the tbsp row and
    g->tbsp from the cup-derived density. Round-tripping compounds the gap: a
    10 g lot reads as 4.85 tbsp, and converting THAT back asks for 14.5 g — 45%
    more than the lot holds.

    Pinned as a test so the "it inverts" claim cannot be re-generalised from
    honey. Across the whole shipped dataset 4.7% of mass<->volume round-trips
    fail this way, 0.5% by more than 5%, the worst (agave syrup, tsp) by 50%.
    """
    as_tbsp = convert_for_food_key(10.0, "g", "tbsp", ALFALFA)
    assert as_tbsp == pytest.approx(4.8484, abs=1e-3)

    back = convert_for_food_key(as_tbsp, "tbsp", "g", ALFALFA)
    assert back == pytest.approx(14.545, abs=1e-2)   # NOT 10.0


# --------------------------------------------------------------------------- #
# Resolution refuses rather than guessing
# --------------------------------------------------------------------------- #
def test_an_unambiguous_name_resolves_to_a_key_and_its_snapshot(version):
    identity = fr.resolve_identity("honey")

    assert identity["foodKey"] == HONEY
    assert identity["datasetVersion"] == version


def test_an_ambiguous_name_resolves_to_nothing_but_still_explains():
    """Raw rice is 185 g a cup and cooked is 158 g. Nothing may be persisted —
    but a read may aggregate the candidates and say why (ADR-0003)."""
    assert fr.resolve_identity("rice") is None

    names = [c["name"] for c in fr.candidates("rice")]
    assert len(names) >= 2
    assert any("cooked" in n.lower() for n in names)


def test_a_key_chosen_against_another_snapshot_is_not_current(version):
    assert fr.key_is_current(HONEY, version) is True
    assert fr.key_is_current(HONEY, "SR Legacy 1999-01") is False
    assert fr.key_is_current("", version) is False


def test_the_newest_snapshot_wins_when_a_provider_has_several(monkeypatch):
    """`sources` arrives ordered by dataset_version ASC, so taking the first
    match reported the OLDEST snapshot while foodcore converted against the
    newest (`Dataset._source_for` picks max by retrieved_at). `key_is_current`
    would then call a re-pointed key current — the staleness check failing
    OPEN, the one direction it must never fail."""
    old = type("S", (), {"provider": "usda", "dataset_version": "SR Legacy 2018-04",
                         "retrieved_at": "2018-04-01"})()
    new = type("S", (), {"provider": "usda", "dataset_version": "SR Legacy 2019-04",
                         "retrieved_at": "2019-04-01"})()
    monkeypatch.setattr(fr, "_dataset",
                        lambda: type("D", (), {"sources": (old, new)})())

    assert fr.dataset_version("usda") == "SR Legacy 2019-04"
    assert fr.key_is_current(HONEY, "SR Legacy 2018-04") is False


def test_an_unknown_key_resolves_to_nothing():
    assert fr.identity_for_key("usda:not-a-real-key") is None
    assert convert_for_food_key(1, "cup", "g", "usda:not-a-real-key") is None


def test_a_food_name_cannot_poison_the_key_lookup_cache():
    """Name resolutions and key lookups are different questions and must not
    share a cache namespace.

    Product names are user-supplied, so a name crafted to look like a cache
    entry for a key ("key:usda:169640") could otherwise either blank out a real
    identity or, worse, answer a key lookup with some other food."""
    assert fr.resolve_identity(f"key:{HONEY}") is None

    assert fr.identity_for_key(HONEY)["name"] == "Honey"
    assert convert_for_food_key(1, "cup", "g", HONEY) == pytest.approx(339.0)


def test_a_persisted_identity_does_not_loosen_the_estimated_guard():
    """A garlic clove is method="estimated" — a convention, not a measurement.
    Knowing exactly WHICH food it is changes nothing about what a bare float can
    say (ADR-0005), so the key path refuses identically to the name path."""
    garlic = fr.resolve_identity("garlic, raw")
    assert garlic is not None

    assert fr.convert_amount_for_key(2, "clove", "g", garlic["foodKey"]) is None
    assert convert_for_food_key(2, "clove", "g", garlic["foodKey"]) is None


# --------------------------------------------------------------------------- #
# plan_consumption: what actually gets deducted
# --------------------------------------------------------------------------- #
def test_without_an_identity_a_cross_dimension_lot_is_still_skipped():
    """Today's behaviour, unchanged and load-bearing: no identity means no
    density, so the lot cannot satisfy a volume demand and the shortfall is
    visible. Under-reporting what we have beats over-drawing it."""
    assert convert(1000, "g", "cup") is None

    picks, shortfall = selection.plan_consumption(
        [_Lot(1000, "g")], 2, demand_unit="cup")

    assert picks == []
    assert shortfall == 2


def test_with_an_identity_the_same_lot_is_drawn(version):
    """The behavioural change. A cup of honey is 339 g, so 2 cups is 678 g."""
    picks, shortfall = selection.plan_consumption(
        [_Lot(1000, "g", HONEY, version)], 2, demand_unit="cup")

    assert len(picks) == 1
    assert picks[0].take == pytest.approx(678.0)
    assert shortfall == 0


def test_a_fully_drawn_lot_takes_its_own_quantity_not_a_round_trip(version):
    """The over-deduction fix, on the food that exposes it.

    A 10 g lot of alfalfa against a 100 tbsp demand is a FULL draw. The take is
    the lot's own 10 g — a fact. Converting the availability figure back, as
    this module used to, asks for 14.5 g from a 10 g lot (see
    `test_foodcore_directions_do_not_always_invert`)."""
    picks, _ = selection.plan_consumption(
        [_Lot(10, "g", ALFALFA, version)], 100, demand_unit="tbsp")

    assert len(picks) == 1
    assert picks[0].take == pytest.approx(10.0)
    assert picks[0].take <= 10.0


def test_a_partial_draw_can_never_exceed_the_lot(version):
    """4 tbsp of alfalfa is 12 g by the measured tbsp portion, but the lot holds
    10 g. The availability check came from the opposite direction and the two
    disagree, so the take is clamped. Over-drawing is the one outcome this
    module must never produce."""
    lot = _Lot(10, "g", ALFALFA, version)
    assert convert_for_food_key(4, "tbsp", "g", ALFALFA) == pytest.approx(12.0)

    picks, _ = selection.plan_consumption([lot], 4, demand_unit="tbsp")

    assert picks[0].take == pytest.approx(10.0)


def test_a_clamped_draw_reports_the_shortfall_it_actually_left(version):
    """The other half of the clamp, and the one that was missing.

    Clamping the take without also reducing what it CREDITS drives `remaining`
    to zero: the planner then reports shortfall=0 and stops. This lot owes 12 g
    for 4 tbsp and holds 10, so 0.67 tbsp is genuinely short and must be said
    so. A silent under-draw is as wrong as an over-draw."""
    picks, shortfall = selection.plan_consumption(
        [_Lot(10, "g", ALFALFA, version)], 4, demand_unit="tbsp")

    assert picks[0].take == pytest.approx(10.0)
    assert shortfall == pytest.approx(0.667, abs=0.01)


def test_a_clamped_draw_still_spills_to_the_next_lot(version):
    """Same disagreement, but there IS more stock. The planner must carry the
    uncredited demand to the next lot instead of declaring itself finished —
    the bug was a 4 tbsp request taking 10 g and leaving a 1 kg lot untouched
    while reporting nothing short."""
    lots = [_Lot(10, "g", ALFALFA, version, created_at=1),
            _Lot(1000, "g", ALFALFA, version, created_at=2)]

    picks, shortfall = selection.plan_consumption(lots, 4, demand_unit="tbsp")

    assert len(picks) == 2
    assert shortfall == 0
    # 4 tbsp is 12 g, and that is what leaves the pantry across both lots.
    assert sum(p.take for p in picks) == pytest.approx(12.0, abs=0.01)


def test_a_stale_identity_falls_back_to_plain_conversion(version):
    """A key chosen against a different snapshot is not used. The fallback is
    `convert`, and therefore today's refusal — never a name match."""
    picks, shortfall = selection.plan_consumption(
        [_Lot(1000, "g", HONEY, "SR Legacy 1999-01")], 2, demand_unit="cup")

    assert picks == []
    assert shortfall == 2


def test_same_dimension_draws_are_untouched_by_an_identity(version):
    """g->kg is exact Decimal arithmetic and must not acquire a dependency on
    the reference dataset."""
    for key, ver in ((HONEY, version), ("", "")):
        picks, shortfall = selection.plan_consumption(
            [_Lot(2, "kg", key, ver)], 500, demand_unit="g")
        assert picks[0].take == pytest.approx(0.5)
        assert shortfall == 0


def test_spilling_across_lots_still_works_with_an_identity(version):
    """Two 200 g honey lots against a 1 cup (339 g) demand: the first is drawn
    whole, the second partially."""
    lots = [_Lot(200, "g", HONEY, version, created_at=1),
            _Lot(200, "g", HONEY, version, created_at=2)]

    picks, shortfall = selection.plan_consumption(lots, 1, demand_unit="cup")

    # 138.99, not 139.0: `remaining` is rounded to 4dp between lots, which is
    # pre-existing behaviour and costs a hundredth of a gram.
    assert [p.take for p in picks] == [pytest.approx(200.0),
                                       pytest.approx(139.0, abs=0.02)]
    assert shortfall == 0


# --------------------------------------------------------------------------- #
# The API: explicit, confirmable, clearable
# --------------------------------------------------------------------------- #
@pytest.fixture()
def product_id(auth_client, add_stock):
    return add_stock("Honey", 1000, unit="g")["product"]["id"]


def _stored(app, product_id):
    with app.app_context():
        p = db.session.get(Product, product_id)
        return p.food_key, p.food_key_dataset_version


def test_a_product_starts_with_no_identity(auth_client, product_id):
    body = auth_client.get(f"/api/v1/products/{product_id}/food-identity").get_json()

    assert body["status"] == "none"
    assert body["food"] is None


def test_creating_a_product_never_resolves_an_identity_by_itself(
        auth_client, add_stock, app):
    """No automatic resolution: a match nobody looked at, stored on every
    product at once, is the silent-wrong-identity failure mode."""
    pid = add_stock("Honey", 500, unit="g")["product"]["id"]

    assert _stored(app, pid) == ("", "")


def test_an_ambiguous_name_is_refused_and_persists_nothing(
        auth_client, add_stock, app):
    """THE guard. 409 with candidates, and the product is untouched."""
    pid = add_stock("Rice", 1000, unit="g")["product"]["id"]

    res = auth_client.put(f"/api/v1/products/{pid}/food-identity", json={})

    assert res.status_code == 409
    assert res.get_json()["confirmWith"] == "foodKey"
    assert len(res.get_json()["candidates"]) >= 2
    assert _stored(app, pid) == ("", "")


def test_an_explicit_key_is_accepted_and_persisted(
        auth_client, product_id, app, version):
    res = auth_client.put(f"/api/v1/products/{product_id}/food-identity",
                          json={"foodKey": HONEY})

    assert res.status_code == 200
    assert res.get_json()["food"]["name"] == "Honey"
    assert _stored(app, product_id) == (HONEY, version)


def test_a_key_the_dataset_does_not_have_is_rejected_and_persists_nothing(
        auth_client, product_id, app):
    res = auth_client.put(f"/api/v1/products/{product_id}/food-identity",
                          json={"foodKey": "usda:not-a-real-key"})

    assert res.status_code == 400
    assert _stored(app, product_id) == ("", "")


def test_an_identity_can_be_cleared(auth_client, product_id, app):
    auth_client.put(f"/api/v1/products/{product_id}/food-identity",
                    json={"foodKey": HONEY})

    res = auth_client.delete(f"/api/v1/products/{product_id}/food-identity")

    assert res.status_code == 200
    assert res.get_json()["status"] == "none"
    assert _stored(app, product_id) == ("", "")


def test_an_identity_from_another_snapshot_reports_itself_stale(
        auth_client, product_id, app):
    """The user-visible half of the dataset-version column. `stale` is reported,
    not smoothed into `none`, because an identity that quietly stopped meaning
    what it said is exactly what has to be visible."""
    auth_client.put(f"/api/v1/products/{product_id}/food-identity",
                    json={"foodKey": HONEY})
    with app.app_context():
        db.session.get(Product, product_id).food_key_dataset_version = "SR Legacy 1999-01"
        db.session.commit()

    body = auth_client.get(
        f"/api/v1/products/{product_id}/food-identity").get_json()

    assert body["status"] == "stale"


def test_the_identity_is_visible_on_the_product_itself(auth_client, product_id,
                                                       version):
    auth_client.put(f"/api/v1/products/{product_id}/food-identity",
                    json={"foodKey": HONEY})

    listing = auth_client.get("/api/v1/products").get_json()
    row = next(r for r in listing if r["id"] == product_id)

    assert row["foodIdentity"] == {"foodKey": HONEY, "datasetVersion": version}


def test_a_product_without_an_identity_serialises_null(auth_client, product_id):
    listing = auth_client.get("/api/v1/products").get_json()
    row = next(r for r in listing if r["id"] == product_id)

    assert row["foodIdentity"] is None


def test_another_household_cannot_read_or_set_an_identity(second_client,
                                                          product_id):
    assert second_client.get(
        f"/api/v1/products/{product_id}/food-identity").status_code == 404
    assert second_client.put(f"/api/v1/products/{product_id}/food-identity",
                             json={"foodKey": HONEY}).status_code == 404
