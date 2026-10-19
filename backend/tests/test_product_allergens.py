"""Persisting a scanned label's allergen verdicts on a Product.

The defect these tests pin: `GET /products/barcode/<code>` returned the allergen
block for a code you did NOT own and nothing at all for one you did, so scanning
something already in your pantry told you LESS than scanning something new
(ADR-0005). The `found: true` branch now carries it.

Most of what follows is about the two states that look alike and are not:

  * **never screened** — no label has been read. `allergens` is None.
  * **screened, nothing found** — a label WAS read and declared nothing. A real
    block whose twelve verdicts are all `unknown`.

Neither is a clearance, and the second is not "safe". Every test that could pass
with the guard removed is marked and mutation-checked in
`test_*_mutation_check` below.
"""
import importlib.util
import json
import os

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.extensions import db
from app.models import Product
from app.schemas.serializers import product_out
from app.services.allergens import (SCREEN_ATTEMPTED, apply_to_product, block_out,
                                    compact, stored_allergens)
from app.services.food_reference import allergens_from_declarations

pytestmark = pytest.mark.usefixtures("app")


def _block(declared=(), traces=(), code="123"):
    return allergens_from_declarations(list(declared), list(traces), source_id=code)


def _states(out):
    return {v["allergen"]: v["state"] for v in out["verdicts"]}


class _Bare:
    """A Product-shaped object, so the service's rules can be tested without a DB."""
    id = "bare"
    label_allergens = None
    allergen_ruleset_version = ""
    allergen_source = ""


# ---- the defect: the found:true branch now carries allergens ---------------- #
def test_known_product_scan_carries_allergens(auth_client, app, gid):
    """The ADR-0005 defect, pinned. A barcode you already own must not come back
    with less than one you don't."""
    with app.app_context():
        p = Product(name="Milky Bar", group_id=gid, barcode="5000")
        assert apply_to_product(p, _block(["en:milk"], ["en:peanuts"], "5000"))
        db.session.add(p)
        db.session.commit()

    r = auth_client.get("/api/v1/products/barcode/5000").get_json()
    assert r["found"] is True
    block = r["product"]["allergens"]
    assert block is not None, "a known product must carry its label's allergens"
    states = _states(block)
    assert states["milk"] == "contains"
    assert states["peanuts"] == "may_contain"
    # Every ruleset key is present, so a short list of findings can never be read
    # as a complete picture.
    assert states["sesame"] == "unknown"
    assert len(block["verdicts"]) == 12


def test_product_out_omits_nothing_for_never_screened(auth_client, app, gid):
    with app.app_context():
        p = Product(name="Plain", group_id=gid, barcode="6000")
        db.session.add(p)
        db.session.commit()

    r = auth_client.get("/api/v1/products/barcode/6000").get_json()
    assert r["found"] is True
    # None, not {} and not an empty verdict list: we have not read a label.
    assert r["product"]["allergens"] is None


# ---- never screened is NOT "screened, nothing found" ----------------------- #
def test_never_screened_and_nothing_found_are_different_answers():
    never = _Bare()
    found_nothing = _Bare()
    assert apply_to_product(found_nothing, _block([], [], "7000"))

    # Storage: one is NULL, the other is a real record with a ruleset version.
    assert never.label_allergens is None
    assert never.allergen_ruleset_version == ""
    assert isinstance(found_nothing.label_allergens, dict)
    assert found_nothing.allergen_ruleset_version

    # API: None vs a block that says the label was read and declared nothing.
    assert block_out(never) is None
    out = block_out(found_nothing)
    assert out is not None
    assert out["declaresNothing"] is True
    assert len(out["verdicts"]) == 12
    assert {v["state"] for v in out["verdicts"]} == {"unknown"}


def test_declared_product_does_not_claim_to_declare_nothing():
    p = _Bare()
    apply_to_product(p, _block(["en:milk"], [], "7001"))
    assert block_out(p)["declaresNothing"] is False


# ---- an empty / absent record is NEVER "free from" ------------------------- #
# GUARD. Mutation-checked by test_absence_is_never_clearance_mutation_check.
@pytest.mark.parametrize("stored", [
    None,                                        # never screened
    {},                                          # empty dict
    {"verdicts": []},                            # a record with no verdicts
    {"verdicts": [], "rulesetVersion": "x/1"},   # ...even with a version
    {"rulesetVersion": "x/1"},                   # a version and nothing else
    [],                                          # wrong type entirely
    "free from everything",                      # garbage
])
def test_empty_or_absent_record_never_reads_as_free_from(stored):
    p = _Bare()
    p.label_allergens = stored
    p.allergen_ruleset_version = "x/1"
    # The ONLY no-data answer is None, which means "we have not read a label".
    assert stored_allergens(p) is None
    assert block_out(p) is None


def test_absence_is_never_clearance_mutation_check():
    """The guard above must FAIL when removed, or it is vacuous.

    `stored_allergens` returning None for an empty record is only meaningful if a
    record WITH verdicts returns something — otherwise a function that returned
    None unconditionally would pass every assertion above.
    """
    screened = _Bare()
    apply_to_product(screened, _block(["en:milk"], [], "7002"))
    assert stored_allergens(screened) is not None
    assert block_out(screened) is not None
    # And the empty case is genuinely discriminated, not swallowed by a blanket None.
    empty = _Bare()
    empty.label_allergens = {"verdicts": [], "rulesetVersion": "x/1"}
    assert stored_allergens(empty) is None


# ---- a verdict with no ruleset version is not a verdict -------------------- #
# GUARD. Mutation-checked below.
def test_unversioned_verdicts_are_refused_on_write():
    p = _Bare()
    block = _block(["en:milk"], [], "7003")
    block["rulesetVersion"] = ""
    assert apply_to_product(p, block) is False
    assert p.label_allergens is None          # not stored at all
    assert p.allergen_ruleset_version == ""


def test_unversioned_verdicts_read_as_never_screened():
    p = _Bare()
    p.label_allergens = {"verdicts": [{"allergen": "milk", "state": "contains"}]}
    p.allergen_ruleset_version = ""           # cannot be re-interpreted later
    assert stored_allergens(p) is None
    assert block_out(p) is None


def test_ruleset_version_guard_mutation_check():
    """Removing the version guard must break the two tests above: the same blob
    WITH a version is stored and read normally."""
    p = _Bare()
    block = _block(["en:milk"], [], "7004")
    assert block["rulesetVersion"]            # the real seam supplies one
    assert apply_to_product(p, block) is True
    assert p.label_allergens is not None
    assert stored_allergens(p) is not None


# ---- no boolean, no safe/free/ok ------------------------------------------- #
def test_no_safety_boolean_anywhere_in_the_payload():
    p = _Bare()
    apply_to_product(p, _block(["en:milk"], ["en:peanuts"], "7005"))
    out = block_out(p)
    blob = json.dumps(out).lower()
    for banned in ('"safe"', '"free"', '"ok"', '"isfree"', '"allergenfree"',
                   '"freefrom"', '"clear"'):
        assert banned not in blob, f"{banned} must never appear in an allergen payload"
    # The only bool is foodcore's own needsReview, which is True for the two states
    # that mean "a human has to look" and False for `contains` — it cannot be
    # mistaken for "safe".
    for v in out["verdicts"]:
        assert set(v) <= {"allergen", "label", "state", "rulesetKeyed",
                          "needsReview", "terms", "note"}
        assert isinstance(v["state"], str)
    assert _verdict_of(out, "milk")["needsReview"] is False
    assert _verdict_of(out, "sesame")["needsReview"] is True


def _verdict_of(out, allergen):
    return next(v for v in out["verdicts"] if v["allergen"] == allergen)


def test_foodcore_verdict_bool_still_raises():
    """foodcore's AllergenVerdict.__bool__ raises on purpose. Nothing in Edibl may
    route around it by reintroducing truthiness."""
    from foodcore.allergens import AllergenVerdict

    v = next(iter(_raw_verdicts()))
    assert isinstance(v, AllergenVerdict)
    with pytest.raises(TypeError):
        bool(v)


def _raw_verdicts():
    from foodcore import rule_version  # noqa: F401 - proves the library is present
    from foodcore.allergens import (AllergenState, AllergenVerdict, Evidence,
                                    allergen_ruleset)
    rs = allergen_ruleset()
    return [AllergenVerdict(allergen="milk", label="Milk",
                            state=AllergenState.UNKNOWN,
                            evidence=(Evidence(
                                ingredient="", matched_term="", rule_id="t",
                                rule_version=rs.version, match_kind="absent",
                                contributes=AllergenState.UNKNOWN, note=""),),
                            ruleset_version=rs.version)]


# ---- compaction keeps every verdict --------------------------------------- #
def test_compaction_keeps_all_verdicts_and_shrinks_the_blob():
    raw = _block(["en:milk", "en:wheat"], ["en:peanuts"], "7006")
    small = compact(raw)
    assert len(small["verdicts"]) == len(raw["verdicts"]) == 12
    # The states survive compaction unchanged — this is the part that must not drift.
    assert ({v["allergen"]: v["state"] for v in small["verdicts"]}
            == {v["allergen"]: v["state"] for v in raw["verdicts"]})
    assert len(json.dumps(small)) < len(json.dumps(raw)) / 2
    assert small["rulesetVersion"] == raw["rulesetVersion"]
    # The matched tag survives for the findings that have one; `unknown` carries no
    # boilerplate evidence.
    milk = next(v for v in small["verdicts"] if v["allergen"] == "milk")
    assert milk["terms"] == ["en:milk"]
    sesame = next(v for v in small["verdicts"] if v["allergen"] == "sesame")
    assert "terms" not in sesame


def test_compact_refuses_a_screening_with_no_verdicts():
    # Storing this would create a record that renders as "screened, nothing found".
    assert compact({"verdicts": [], "rulesetVersion": "x/1"}) is None
    assert compact(None) is None
    assert compact({"source": "openfoodfacts"}) is None


def test_unmapped_declarations_survive_storage():
    """Celery is declarable in the EU and outside foodcore's ruleset. A declaration
    must never be reachable only by reading a second array."""
    p = _Bare()
    apply_to_product(p, _block(["en:celery"], [], "7007"))
    out = block_out(p)
    celery = next(v for v in out["verdicts"] if v["allergen"] == "off:celery")
    assert celery["state"] == "contains"
    assert celery["rulesetKeyed"] is False
    assert out["unmappedCount"] == 1


# ---- display order --------------------------------------------------------- #
def test_verdicts_sort_by_severity_with_unknown_above_the_quiet_states():
    p = _Bare()
    apply_to_product(p, _block(["en:milk"], ["en:peanuts"], "7008"))
    states = [v["state"] for v in block_out(p)["verdicts"]]
    assert states[0] == "contains"
    assert states[1] == "may_contain"
    assert set(states[2:]) == {"unknown"}
    # `unknown` is never collapsed away or sorted below anything.
    assert states.count("unknown") == 10


# ---- write policy ---------------------------------------------------------- #
def test_rescan_does_not_silently_replace_an_existing_screening():
    p = _Bare()
    apply_to_product(p, _block(["en:milk"], [], "7009"))
    first = json.dumps(p.label_allergens)
    assert apply_to_product(p, _block(["en:peanuts"], [], "7009")) is False
    assert json.dumps(p.label_allergens) == first
    # ...unless asked explicitly.
    assert apply_to_product(p, _block(["en:peanuts"], [], "7009"), overwrite=True)
    assert _states(block_out(p))["peanuts"] == "contains"


def test_unavailable_foodcore_stores_nothing():
    p = _Bare()
    assert apply_to_product(p, None) is False
    assert p.label_allergens is None
    assert block_out(p) is None


# ---- populate: scan creation + backfill ------------------------------------ #
def test_scan_creation_persists_allergens(auth_client, app, gid, add_location):
    add_location("Pantry")
    block = _block(["en:milk"], [], "8100")
    r = auth_client.post("/api/v1/stock", json={
        "productName": "Scanned Milk", "barcode": "8100", "location": "Pantry",
        "quantity": 1, "unit": "count", "allergens": block})
    assert r.status_code in (200, 201)
    with app.app_context():
        p = db.session.query(Product).filter_by(group_id=gid, barcode="8100").first()
        assert _states(block_out(p))["milk"] == "contains"
        assert p.allergen_source == "openfoodfacts"


def test_backfill_screens_an_existing_product(app, gid, monkeypatch):
    from app.services import jobs
    with app.app_context():
        p = Product(name="Old Jar", group_id=gid, barcode="8200")
        db.session.add(p)
        db.session.commit()
        pid = p.id
        assert block_out(db.session.get(Product, pid)) is None   # never screened
        app.config["BARCODE_LOOKUP"] = True
        monkeypatch.setattr(
            "app.services.barcode.lookup_barcode",
            lambda code: {"name": "Old Jar",
                          "allergens": _block(["en:sesame"], [], code)})
        filled, screened = jobs._backfill_from_barcode(gid)
        assert screened == 1
        refreshed = db.session.get(Product, pid)
        assert _states(block_out(refreshed))["sesame"] == "contains"


def test_backfill_marks_an_unscreenable_product_without_inventing_a_verdict(
        app, gid, monkeypatch):
    """A barcode OFF has no record for must stop being re-fetched — but the marker
    must NOT be an empty allergen record, which would render as a clean screening."""
    from app.services import jobs
    with app.app_context():
        p = Product(name="Ghost", group_id=gid, barcode="8300")
        db.session.add(p)
        db.session.commit()
        pid = p.id
        app.config["BARCODE_LOOKUP"] = True
        monkeypatch.setattr("app.services.barcode.lookup_barcode",
                            lambda code: {"name": "Ghost"})      # hit, no allergens
        _, screened = jobs._backfill_from_barcode(gid)
        assert screened == 0
        refreshed = db.session.get(Product, pid)
        assert refreshed.allergen_source == SCREEN_ATTEMPTED
        assert refreshed.label_allergens is None     # still never screened
        assert product_out(refreshed)["allergens"] is None
        # ...and it is no longer a backfill candidate.
        assert jobs._backfill_from_barcode(gid)[1] == 0


# ---- Edibl's own lexicon stays separate ------------------------------------ #
def test_label_allergens_never_touch_the_food_concept_lexicon(app, gid):
    """ADR-0005: foodcore's regulatory verdicts and FoodConcept.allergens (Edibl's
    material boundary) answer different questions and must not be merged."""
    from app.models import FoodConcept
    with app.app_context():
        c = FoodConcept(canonical_name="Coconut milk", group_id=gid,
                        allergens=["coconut"])
        db.session.add(c)
        db.session.flush()
        p = Product(name="Coconut Milk", group_id=gid, barcode="8400",
                    concept_id=c.id)
        apply_to_product(p, _block(["en:milk"], [], "8400"))
        db.session.add(p)
        db.session.commit()

        out = product_out(p)
        # The label block carries foodcore keys only; the concept keeps its own list.
        assert "coconut" not in {v["allergen"] for v in out["allergens"]["verdicts"]}
        assert db.session.get(FoodConcept, c.id).allergens == ["coconut"]


# ---- migration 0013 -------------------------------------------------------- #
def _load_mig(app):
    path = os.path.join(os.path.dirname(app.root_path),
                        "migrations", "versions", "0013_product_allergens.py")
    spec = importlib.util.spec_from_file_location("mig0013", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migration_0013_takes_its_assigned_slot(app):
    with app.app_context():
        mod = _load_mig(app)
        assert mod.revision == "0013_product_allergens"
        assert mod.down_revision == "0012_api_token_access"


def test_migration_0013_upgrade_is_idempotent(app):
    # The columns already exist (metadata create_all), so upgrade() must detect them
    # and no-op rather than raise. Downgrade is exercised out-of-process against a
    # seeded database instead — dropping the columns here would break the session.
    with app.app_context():
        mod = _load_mig(app)
        ctx = MigrationContext.configure(db.session.connection())
        with Operations.context(ctx):
            mod.upgrade()
        cols = {c["name"] for c in
                db.inspect(db.session.connection()).get_columns("products")}
        assert {"label_allergens", "allergen_ruleset_version",
                "allergen_source"} <= cols


# ---- notes only when they add something the state does not ----------------- #
def test_a_plain_declaration_stores_no_restating_note():
    """"declared in Open Food Facts traces_tags" only restates "May contain" in
    the source's field names. Repeated per allergen it renders as a stutter of
    identical rows and puts a database column name in front of someone deciding
    whether to eat something."""
    p = _Bare()
    apply_to_product(p, _block([], ["en:peanuts"], "7010"))
    peanuts = next(v for v in block_out(p)["verdicts"] if v["allergen"] == "peanuts")
    assert peanuts["state"] == "may_contain"
    assert "note" not in peanuts


def test_a_rule_softened_verdict_keeps_its_explanation():
    """Oats are a certain declaration whose gluten link is precautionary. That
    note is the only thing saying why a declared tag reads `may_contain`, so it
    must survive the filter above — mutation-check for it."""
    p = _Bare()
    apply_to_product(p, _block(["en:oats"], [], "7011"))
    gluten = next(v for v in block_out(p)["verdicts"] if v["allergen"] == "gluten")
    assert gluten["state"] == "may_contain"
    assert gluten.get("note"), "a rule-inferred verdict must explain itself"
