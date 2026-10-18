"""Allergen declarations read off a barcode (Open Food Facts → foodcore verdicts).

The whole point of this feature is the state it REFUSES to claim. OFF's allergen
tags are derived from community-entered ingredient text: they prove presence and
say nothing at all about absence. So a product with no declaration is `unknown`,
and `unknown` must be impossible to read as safe — these tests exist mostly to
keep that true.
"""
import pytest

from app.services.barcode import _allergens_from_off
from app.services.food_reference import allergens_from_declarations


def _states(block):
    return {v["allergen"]: v["state"] for v in block["verdicts"]}


def _verdict(block, allergen):
    return next(v for v in block["verdicts"] if v["allergen"] == allergen)


# ---- declared ("an ingredient IS this") ------------------------------------ #
def test_declared_allergens_tags_become_contains():
    block = _allergens_from_off({"allergens_tags": ["en:milk", "en:eggs"]}, "1")

    states = _states(block)
    assert states["milk"] == "contains"
    assert states["egg"] == "contains"
    assert _verdict(block, "milk")["evidence"][0]["ruleId"] == "off:allergens_tags"
    assert _verdict(block, "milk")["evidence"][0]["provenance"]["method"] == "declared"


def test_declared_wheat_also_declares_gluten_via_the_shared_ruleset():
    """foodcore's implication table, not a second mapping of our own."""
    block = _allergens_from_off({"allergens_tags": ["en:wheat"]}, "1")

    assert _states(block)["gluten"] == "contains"
    implied = _verdict(block, "gluten")["evidence"][0]
    assert implied["kind"] == "implied"
    assert implied["provenance"]["method"] == "inferred"


# ---- traces ("may contain") ------------------------------------------------- #
def test_traces_tags_alone_become_may_contain_not_contains_and_not_unknown():
    """The precautionary line is its own state. Reading only allergens_tags
    loses every shared-line warning, which for a severe allergy is the half
    that matters; collapsing it into `contains` would be a different lie."""
    block = _allergens_from_off({"traces_tags": ["en:nuts", "en:peanuts"]}, "1")

    states = _states(block)
    assert states["tree_nuts"] == "may_contain"
    assert states["peanuts"] == "may_contain"
    assert _verdict(block, "tree_nuts")["evidence"][0]["ruleId"] == "off:traces_tags"
    assert states["milk"] == "unknown"       # nothing said about milk


def test_declared_beats_a_duplicate_trace():
    block = _allergens_from_off({"allergens_tags": ["en:milk"],
                                 "traces_tags": ["en:milk"]}, "1")

    assert _states(block)["milk"] == "contains"


# ---- absence is unknown, never a clearance --------------------------------- #
def test_a_product_with_neither_field_is_unknown_throughout():
    block = _allergens_from_off({"product_name": "Mystery Snack"}, "1")

    assert set(_states(block).values()) == {"unknown"}
    assert len(block["verdicts"]) == 12          # every allergen, stated explicitly


def test_every_ruleset_allergen_gets_a_verdict_so_a_short_list_cannot_read_as_safe():
    """A block listing only what was declared invites "not in the list = fine".
    Emitting the unknowns is what makes the gap visible to a consumer."""
    block = _allergens_from_off({"allergens_tags": ["en:milk"]}, "1")

    states = _states(block)
    assert states["milk"] == "contains"
    assert states["peanuts"] == "unknown"
    assert "no_known_ingredient_identified" not in states.values()


def test_an_empty_tag_list_is_not_a_declaration_of_absence():
    """OFF writes [] for "nobody has entered ingredients", not "contains none"."""
    block = _allergens_from_off({"allergens_tags": [], "traces_tags": []}, "1")

    assert set(_states(block).values()) == {"unknown"}


def test_unknown_carries_a_zero_confidence_absence_note():
    block = _allergens_from_off({}, "1")
    evidence = _verdict(block, "sesame")["evidence"][0]

    assert evidence["kind"] == "absent"
    assert "not a clearance" in evidence["note"]
    assert evidence["provenance"]["confidence"] == 0.0


# ---- no boolean, anywhere --------------------------------------------------- #
def test_the_payload_exposes_no_safety_boolean():
    """foodcore's AllergenVerdict raises on bool() and has no `.safe`, because
    whether a state is acceptable depends on a policy only the caller holds (a
    medical allergy excludes `unknown`; a dislike does not). A convenience
    boolean here would re-introduce exactly the unsafe reading it prevents."""
    block = _allergens_from_off({"allergens_tags": ["en:milk"]}, "1")

    banned = {"safe", "free", "freeFrom", "free_from", "contains", "allowed",
              "ok", "clear", "permitted"}
    for verdict in block["verdicts"]:
        assert banned.isdisjoint(verdict)
        for value in verdict.values():
            assert not isinstance(value, bool) or value is verdict["needsReview"]


def test_needs_review_is_true_for_both_uncertain_states_and_not_a_safety_flag():
    block = _allergens_from_off({"allergens_tags": ["en:milk"],
                                 "traces_tags": ["en:nuts"]}, "1")

    assert _verdict(block, "milk")["needsReview"] is False       # certain: contains
    assert _verdict(block, "tree_nuts")["needsReview"] is True    # may_contain
    assert _verdict(block, "fish")["needsReview"] is True         # unknown


# ---- tags foodcore has no rule for ----------------------------------------- #
def test_an_allergen_foodcore_cannot_key_is_reported_not_dropped():
    """Celery, mustard and lupin are declarable in the EU and foodcore's
    ruleset has no key for them. Dropping them would turn a declared allergen
    into silence."""
    block = _allergens_from_off(
        {"allergens_tags": ["en:celery", "en:mustard"],
         "traces_tags": ["en:lupin"]}, "1")

    assert block["unmappedTags"] == [
        {"tag": "en:celery", "declaredIn": "allergens_tags"},
        {"tag": "en:mustard", "declaredIn": "allergens_tags"},
        {"tag": "en:lupin", "declaredIn": "traces_tags"},
    ]


def test_a_non_english_tag_is_reported_unmapped_rather_than_guessed():
    block = _allergens_from_off({"allergens_tags": ["fr:lait"]}, "1")

    assert _states(block)["milk"] == "unknown"
    assert block["unmappedTags"] == [{"tag": "fr:lait", "declaredIn": "allergens_tags"}]


@pytest.mark.parametrize("tags", [None, "not-a-list", [None, 5], [""], ["en:"]])
def test_garbled_tag_payloads_do_not_break_the_scan(tags):
    block = _allergens_from_off({"allergens_tags": tags}, "1")

    assert block is not None
    assert set(_states(block).values()) == {"unknown"}
    assert block["unmappedTags"] == []


# ---- provenance + licence --------------------------------------------------- #
def test_the_block_carries_the_odbl_licence_and_the_ruleset_version():
    """OFF is ODbL (attribution and share-alike), not CC0 like USDA. A consumer
    cannot work its obligations out from the value, so the licence travels."""
    block = _allergens_from_off({"allergens_tags": ["en:milk"]}, "7613035833744")

    assert block["license"] == "ODbL-1.0"
    assert block["source"] == "openfoodfacts"
    assert block["rulesetVersion"].startswith("allergens/")
    provenance = _verdict(block, "milk")["evidence"][0]["provenance"]
    assert provenance["source"] == "off"
    assert provenance["sourceId"] == "7613035833744"
    assert provenance["license"] == "ODbL-1.0"


# ---- degradation ------------------------------------------------------------ #
def test_no_foodcore_means_no_allergen_block_and_an_unchanged_payload(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def refuse(name, *args, **kw):
        if name.startswith("foodcore"):
            raise ImportError("foodcore is not installed")
        return real_import(name, *args, **kw)

    monkeypatch.setattr(builtins, "__import__", refuse)

    assert allergens_from_declarations(["en:milk"], None, source_id="1") is None
