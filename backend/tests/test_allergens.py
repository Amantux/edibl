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
    assert _states(block)["wheat"] == "contains"


# Open Food Facts' own allergen taxonomy: the EU 14 as OFF spells them, plus the
# sub-tags real products carry. Every one of these is a DECLARATION, and a
# declaration reported as `unknown` is worse than the absence case this feature
# exists to be honest about — `unknown` is permitted under a PREFERENCE policy,
# so a consumer reads it as "not containing". Testing only the three tags
# foodcore genuinely cannot key (celery/mustard/lupin) hid exactly that bug:
# `en:sulphur-dioxide-and-sulphites` and `en:almonds` both came back unknown.
OFF_TAXONOMY = [
    ("en:gluten", "gluten"), ("en:milk", "milk"), ("en:eggs", "egg"),
    ("en:nuts", "tree_nuts"), ("en:peanuts", "peanuts"),
    ("en:sesame-seeds", "sesame"), ("en:soybeans", "soybeans"),
    ("en:fish", "fish"), ("en:crustaceans", "crustacean_shellfish"),
    ("en:molluscs", "molluscs"),
    ("en:sulphur-dioxide-and-sulphites", "sulfites"),
    ("en:tree-nuts", "tree_nuts"), ("en:almonds", "tree_nuts"),
    ("en:hazelnuts", "tree_nuts"), ("en:walnuts", "tree_nuts"),
    ("en:cashew-nuts", "tree_nuts"), ("en:pistachio", "tree_nuts"),
    ("en:lactose", "milk"), ("en:milk-proteins", "milk"),
    ("en:soya", "soybeans"), ("en:wheat", "wheat"),
    ("en:barley", "gluten"), ("en:rye", "gluten"), ("en:spelt", "gluten"),
    ("en:shellfish", "crustacean_shellfish"), ("en:sulphites", "sulfites"),
]


@pytest.mark.parametrize("tag,allergen", OFF_TAXONOMY)
def test_every_tag_open_food_facts_publishes_is_keyed_not_dropped(tag, allergen):
    block = _allergens_from_off({"allergens_tags": [tag]}, "1")

    assert _states(block)[allergen] == "contains", f"{tag} was not keyed"
    assert block["unmappedCount"] == 0


def test_a_rule_that_is_only_precautionary_downgrades_a_certain_declaration():
    """`en:oats` is a certain declaration of oats; foodcore's rule says the
    gluten link is precautionary. The weaker certainty wins — reporting
    `contains` would be a different lie from reporting `unknown`."""
    block = _allergens_from_off({"allergens_tags": ["en:oats"]}, "1")

    assert _states(block)["gluten"] == "may_contain"
    assert _verdict(block, "gluten")["evidence"][0]["provenance"]["method"] == "inferred"


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
    # The only two booleans, and neither can stand in for safety: `needsReview`
    # is true for `may_contain` AND `unknown` (and false for `contains`), and
    # `rulesetKeyed` is false for a declared celery, i.e. the opposite of safe.
    allowed_bools = {"needsReview", "rulesetKeyed"}
    for verdict in block["verdicts"]:
        assert banned.isdisjoint(verdict)
        for key, value in verdict.items():
            assert not isinstance(value, bool) or key in allowed_bools


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
    assert block["unmappedCount"] == 3


def test_an_unkeyable_declaration_also_appears_among_the_verdicts():
    """The twelve ruleset rows look like a complete allergen picture, so a
    consumer looping `verdicts` — the obvious shape — must not be able to miss
    a declared celery by not knowing to read a second array."""
    block = _allergens_from_off({"allergens_tags": ["en:celery"],
                                 "traces_tags": ["en:lupin"]}, "1")

    states = _states(block)
    assert states["off:en:celery"] == "contains"
    assert states["off:en:lupin"] == "may_contain"
    assert _verdict(block, "off:en:celery")["rulesetKeyed"] is False
    assert _verdict(block, "milk")["rulesetKeyed"] is True
    assert _verdict(block, "off:en:celery")["label"] == "Celery"


@pytest.mark.parametrize("tag,allergen", [("en:gluten-free", "gluten"),
                                           ("en:nut-free", "tree_nuts"),
                                           ("en:dairy-free", "milk")])
def test_a_free_from_claim_in_an_allergen_field_errs_toward_may_contain(tag, allergen):
    """Pinning a known over-caution as deliberate. foodcore downgrades an
    unverifiable claim rather than trusting it, so a claim that lands in an
    allergen field (they belong in OFF's labels_tags) reads `may_contain`. The
    error points the safe way; what must never happen is it reading as a
    clearance, or flipping to `contains`."""
    block = _allergens_from_off({"allergens_tags": [tag]}, "1")

    assert _states(block)[allergen] == "may_contain"


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
