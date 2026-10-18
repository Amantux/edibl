"""The foodcore seam: the ONLY module in Edibl that imports foodcore.

Two questions Edibl could not answer, both of which need a *specific food* and
reference data rather than arithmetic:

* **"How much does a cup of this weigh?"** ``quantity.convert`` converts freely
  within a dimension and refuses across one, by design — it has no food, so it
  has no density, and inventing one is how 2 kg of flour silently becomes 2
  litres. With a food in hand, foodcore answers from USDA portion weights.
* **"What does this label declare about allergens?"** A barcode scan reads
  Open Food Facts, which publishes declared allergens and precautionary
  "may contain" traces. Edibl was throwing both away.

Everything else in Edibl keeps calling ``quantity.convert_for_food`` and
``barcode._from_off``. This module is the single place that knows foodcore
exists, so the library can be upgraded, swapped or removed without touching a
caller, and only foodcore's **documented public API** is used here
(``load_dataset``, ``Dataset.resolve_food``/``food_record``, ``convert``,
``allergens``' record types and ``allergen_ruleset``, ``rule_version``). Its
internals are explicitly not a contract.

Three rules this module exists to enforce
-----------------------------------------
1. **No foodcore, no change.** An absent library, a missing dataset asset, an
   unreadable temp dir: every failure degrades to exactly today's behaviour
   (``None`` / no allergen block) and none of them raises. Edibl worked without
   any of this yesterday.
2. **A refusal is not a zero.** foodcore returns ``Unresolved`` with a reason
   rather than a plausible number. Every refusal becomes ``None`` here — never
   0, never a fallback guess.
3. **foodcore's allergen verdicts are kept SEPARATE from Edibl's own
   ``FoodConcept.allergens``** — see ``allergens_from_declarations``.
"""
from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone

_LOGGER = logging.getLogger("edibl.food_reference")

# Where the dataset foodcore ships is expanded to.
#
# EPHEMERAL on purpose. The obvious home is DATA_DIR — the only durable path an
# add-on has — but Home Assistant backs that up wholesale, and this is ~10 MB
# regenerable from a 2.5 MB asset already inside the image. Paying the
# expansion once per container start is plainly better than adding 10 MB to
# every snapshot a user ever takes of data they already have.
_SHIPPED_NAME = "usda-sr-legacy-2018-04.sqlite"
# A private directory, per euid, inside the system temp dir. The path has to be
# PREDICTABLE so gunicorn's workers share one expansion instead of paying the
# 10 MB each — but a predictable name in a world-writable directory is also
# somewhere another local user can leave a file, or a symlink, for us to open as
# our reference data, which would quietly change every weight the app reports.
# In the add-on /tmp belongs to the container; a standalone install shares the
# host's. So: 0700, and used only while it is still a directory we own that
# nobody else can reach. The euid suffix keeps a root-created directory from
# locking out the unprivileged server (the container starts as root and drops).
_EXPANSION_DIR = "edibl-foodcore"

_lock = threading.Lock()
_dataset_cache: dict[str, object] = {}
_food_cache: dict[str, object] = {}


def reset_cache() -> None:
    """Drop the cached dataset handle and resolutions (tests)."""
    with _lock:
        _dataset_cache.clear()
        _food_cache.clear()


def _expansion_dir() -> str:
    """Our private 0700 expansion directory, or "" when it is not ours alone.

    Refuses rather than repairs: a directory at this path that we do not own, or
    that anyone else can write, is not a situation to silently take over. ""
    costs the conversion refinement, which is the behaviour that preceded it.
    """
    import stat
    import tempfile

    path = os.path.join(tempfile.gettempdir(), f"{_EXPANSION_DIR}-{os.geteuid()}")
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        pass
    except OSError as exc:
        _LOGGER.warning("could not create the food reference cache directory (%s); "
                        "food-aware conversion is unavailable", exc)
        return ""
    try:
        # lstat, not stat: a symlink planted here must be seen as a symlink
        # rather than followed to whatever it points at.
        st = os.lstat(path)
    except OSError:
        return ""
    if (not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid()
            or st.st_mode & (stat.S_IRWXG | stat.S_IRWXO)):
        _LOGGER.warning("the food reference cache directory is not private to this "
                        "user; food-aware conversion is unavailable")
        return ""
    return path


def _shipped_dataset_path() -> str:
    """The expanded dataset foodcore ships, expanding it on first use. "" if absent.

    foodcore ships the USDA SR Legacy snapshot gzipped (2.5 MB against ~10 MB),
    because a frozen CC0 release is better committed than downloaded during a
    build that would then depend on a third party being reachable. SQLite cannot
    read a gzip stream, so it is expanded once and opened thereafter.

    Every failure degrades to "" — no food-aware answers, i.e. today's
    behaviour — and never raises.
    """
    import gzip
    import shutil
    import stat

    try:
        import foodcore
    except Exception:  # noqa: BLE001 - the library being absent is a valid deployment
        return ""

    # Package DATA, not a sibling directory: foodcore is pip-installed, so the
    # asset travels inside the package. Deriving the path from a repo layout
    # works in a checkout and silently finds nothing once installed.
    source = os.path.join(os.path.dirname(os.path.abspath(foodcore.__file__)),
                          "data", "usda-sr-legacy-2018-04.sqlite.gz")
    if not os.path.isfile(source):
        return ""

    directory = _expansion_dir()
    if not directory:
        return ""

    target = os.path.join(directory, _SHIPPED_NAME)
    try:
        st = os.lstat(target)
        if stat.S_ISREG(st.st_mode) and st.st_size > 0:
            return target
        # Anything else at this path (a symlink, a directory, a truncated file)
        # is removed rather than opened — see the note on _EXPANSION_DIR.
        if stat.S_ISDIR(st.st_mode):
            return ""
        os.unlink(target)
    except FileNotFoundError:
        pass
    except OSError:
        return ""
    # Expand to a temporary name and rename into place, so a container killed
    # mid-write leaves no truncated database for the next boot to open.
    tmp = f"{target}.partial-{os.getpid()}"
    try:
        with gzip.open(source, "rb") as src, open(tmp, "wb") as dst:
            shutil.copyfileobj(src, dst, 1024 * 1024)
        os.replace(tmp, target)
    except OSError as exc:
        _LOGGER.warning("could not expand the food reference dataset (%s); "
                        "food-aware conversion is unavailable", exc)
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return ""
    return target


def _dataset():
    """The open reference dataset, or None when there is nothing usable.

    ``$FOODCORE_DATASET`` is foodcore's own documented way for a host to point
    at a dataset it built itself; when it is set we pass no path and let
    foodcore resolve it, so the operator's file wins over the shipped snapshot.
    This is deliberately NOT an ``EDIBL_*`` setting: it is the library's
    contract, not Edibl configuration, and there is nothing for an Edibl
    operator to choose while the shipped snapshot is the only dataset we build.
    """
    operator_owned = bool(os.environ.get("FOODCORE_DATASET", "").strip())
    key = "env" if operator_owned else "shipped"
    with _lock:
        if key in _dataset_cache:
            return _dataset_cache[key]
        ds = None
        try:
            import foodcore

            if operator_owned:
                # allow_minimal=False: an operator who named a file wants a
                # loud failure here, not foodcore's definitional-facts-only
                # fallback quietly standing in for it.
                ds = foodcore.load_dataset(allow_minimal=False)
            else:
                path = _shipped_dataset_path()
                ds = foodcore.load_dataset(path, allow_minimal=False) if path else None
        except Exception as exc:  # noqa: BLE001 - a bad dataset must not break the app
            # Named, not echoed: foodcore's messages are curated, but the path
            # can come from operator config, so only the exception class is
            # logged.
            _LOGGER.warning("the food reference dataset could not be opened (%s); "
                            "food-aware conversion is unavailable",
                            type(exc).__name__)
        _dataset_cache[key] = ds
        return ds


def _resolve_food(text: str):
    """A free-text food name as a foodcore ``FoodRecord``, or None.

    Resolution is foodcore's own confidence-tiered policy (``resolve_food``),
    which **refuses rather than guesses**: "rice" comes back unresolved with raw
    and cooked rice as candidates, because they differ by 17% per cup. We only
    use a confident match, so an ambiguous name yields no conversion rather
    than a conversion against the wrong food.

    This is a lookup in foodcore's dataset. It is NOT Edibl's name→product
    matching (``services.matching``) and must not be used as such.
    """
    name = (text or "").strip().lower()
    if not name:
        return None
    ds = _dataset()
    if ds is None:
        return None
    with _lock:
        if name in _food_cache:
            return _food_cache[name]
    record = None
    try:
        match = ds.resolve_food(name)
        if match.confident:
            record = match.food
    except Exception as exc:  # noqa: BLE001 - a lookup must never break a caller
        _LOGGER.debug("food resolution failed for %r (%s)", name, type(exc).__name__)
    with _lock:
        _food_cache[name] = record
    return record


def convert_amount(value: float, from_unit: str, to_unit: str,
                   food: str) -> float | None:
    """``value`` in ``from_unit`` as ``to_unit`` for one specific ``food``, or None.

    None on **any** foodcore refusal — an unresolvable name, a food that
    publishes no weight for the measure, a measure whose published variants
    disagree materially (``qualifier_required``: a cup of chopped onion is
    160 g, sliced is 115 g, and foodcore refuses rather than pick one). The
    caller cannot tell those apart on purpose: every one of them means "we do
    not know", and the only safe thing to do with "we do not know" is what
    Edibl did before this module existed.

    The provenance foodcore returns is deliberately dropped here. This value is
    an arithmetic intermediate in a planning calculation and is not stored, so
    there is nothing for a licence or a source id to travel with. The first
    caller that persists or displays a converted weight should widen this seam
    to return the ``Outcome`` rather than re-derive the provenance elsewhere.
    """
    record = _resolve_food(food)
    if record is None:
        return None
    ds = _dataset()
    try:
        from foodcore.convert import convert as fc_convert

        outcome = fc_convert(float(value), from_unit, to_unit,
                             food=record, dataset=ds)
    except Exception as exc:  # noqa: BLE001 - never break a caller over a conversion
        _LOGGER.debug("food-aware conversion failed (%s)", type(exc).__name__)
        return None
    if not outcome.resolved:
        _LOGGER.debug("food-aware conversion refused: %s", outcome.reason)
        return None
    return float(outcome.value)


# --------------------------------------------------------------------------- #
# Allergens declared on a package label (Open Food Facts)
# --------------------------------------------------------------------------- #

# Our two OFF declaration kinds -> the foodcore state they contribute, the
# provenance method, and how much we trust them. OFF's allergen tags are
# derived from community-entered ingredient text, so "declared" is the honest
# method but not a high confidence.
_DECLARED, _TRACES = "allergens_tags", "traces_tags"


def _off_allergen_key(tag: str) -> tuple[str, str]:
    """An OFF allergen tag as (foodcore allergen key, cleaned term).

    Returns ("", term) when foodcore's ruleset has no key for it. OFF tags are
    language-prefixed (``en:milk``, ``fr:lait``); only the prefix is stripped,
    and a non-English term then simply fails to resolve and is reported as
    unmapped rather than guessed at.
    """
    from foodcore.allergens import resolve_allergen_key

    term = (tag or "").strip().lower()
    if len(term) >= 3 and term[2] == ":" and term[:2].isalpha():
        term = term[3:]
    if not term:
        return "", ""
    try:
        return resolve_allergen_key(term), term
    except ValueError:
        # Not a guess-and-move-on: celery, mustard and lupin are declarable in
        # the EU and foodcore's ruleset has no key for them. Reported verbatim
        # by the caller so a declared allergen is never silently dropped.
        return "", term


def allergens_from_declarations(declared, traces, *, source_id: str) -> dict | None:
    """What a package label declares about allergens, as foodcore verdicts.

    ``declared`` is OFF's ``allergens_tags`` (an ingredient IS this allergen),
    ``traces`` its ``traces_tags`` (precautionary "may contain"). Returns None
    only when foodcore is unavailable — never because a list was empty.

    **Absence is never a clearance.** A verdict is emitted for every allergen
    in foodcore's ruleset, and an allergen with no declaration gets
    ``unknown``, not "free from": OFF's tags prove presence and say nothing
    about absence, since they are derived from ingredient text that is often
    incomplete and sometimes missing altogether. Emitting the unknowns
    explicitly is what stops a consumer reading a short list as a safe one.

    For the same reason we never emit ``no_known_ingredient_identified``, which
    is foodcore's strongest statement and means *every ingredient was
    recognised and none matched a rule*. We did not screen ingredients; we read
    a declaration. Claiming the stronger state would be a lie about what we
    checked.

    There is deliberately **no boolean anywhere in the output**, mirroring
    foodcore's own ``AllergenVerdict``, which raises ``TypeError`` on
    ``bool()`` and has no ``.safe``: whether a state is acceptable depends on a
    policy (a medical allergy excludes ``unknown``, a dislike does not), and
    only the caller holds the policy.

    These verdicts describe ONE packaged product's label and are stored and read
    separately from ``FoodConcept.allergens``, which is Edibl's own
    classification of a material boundary used to stop matching crossing it
    (ADR-0003). The two answer different questions and reconciling them would
    produce one list that is wrong for one consumer: coconut is botanically a
    drupe and carries no allergen in a material lexicon, while FDA labelling —
    and therefore foodcore's ruleset and every package label — counts it as a
    tree nut. Keep them apart.
    """
    try:
        from foodcore import Provenance, rule_version
        from foodcore.allergens import (AllergenState, AllergenVerdict, Evidence,
                                        allergen_ruleset)
    except Exception:  # noqa: BLE001 - library absent is a valid deployment
        return None

    try:
        ruleset = allergen_ruleset()
        version = rule_version("allergens")
        retrieved = datetime.now(timezone.utc).date().isoformat()

        def provenance(method: str, confidence: float):
            return Provenance(source="off", source_id=str(source_id or ""),
                              retrieved=retrieved, method=method, version=version,
                              confidence=confidence,
                              # ODbL, not CC0: Open Food Facts data carries
                              # attribution and share-alike duties, and a host
                              # app cannot work those out from the value alone.
                              license="ODbL-1.0")

        evidence: dict[str, list] = {}
        unmapped: list[dict] = []

        def declare(tags, field: str, state, method: str, confidence: float) -> None:
            # A non-list is community-entered garbage, not one tag: iterating a
            # string would report each character as an unmapped allergen.
            for tag in tags if isinstance(tags, (list, tuple)) else ():
                if not isinstance(tag, str):
                    continue
                key, term = _off_allergen_key(tag)
                if not key:
                    if term:
                        unmapped.append({"tag": tag, "declaredIn": field})
                    continue
                evidence.setdefault(key, []).append(Evidence(
                    ingredient="", matched_term=tag, rule_id=f"off:{field}",
                    rule_version=ruleset.version, match_kind="declared_tag",
                    contributes=state, note=f"declared in Open Food Facts {field}",
                    provenance=provenance(method, confidence)))
                # foodcore's own implication rules, not ours: wheat implies
                # gluten. Applying them here keeps the one ruleset authoritative
                # instead of hand-rolling a second, divergent mapping.
                for implied in ruleset.implies.get(key, ()):
                    evidence.setdefault(implied, []).append(Evidence(
                        ingredient="", matched_term=tag, rule_id=f"off:{field}+implies",
                        rule_version=ruleset.version, match_kind="implied",
                        contributes=state,
                        note=f"implied by declared {key}",
                        provenance=provenance("inferred", confidence * 0.9)))

        # CONTAINS before MAY_CONTAIN: an allergen in both lists is present, and
        # the precautionary note adds nothing to that.
        declare(declared, _DECLARED, AllergenState.CONTAINS, "declared", 0.8)
        declare(traces, _TRACES, AllergenState.MAY_CONTAIN, "declared", 0.8)

        verdicts = []
        for key in sorted(ruleset.keys):
            found = evidence.get(key)
            if found:
                states = {e.contributes for e in found}
                state = (AllergenState.CONTAINS if AllergenState.CONTAINS in states
                         else AllergenState.MAY_CONTAIN)
            else:
                state = AllergenState.UNKNOWN
                found = [Evidence(
                    ingredient="", matched_term="", rule_id="off:no_declaration",
                    rule_version=ruleset.version, match_kind="absent",
                    contributes=AllergenState.UNKNOWN,
                    note=("Open Food Facts declares nothing about this allergen for "
                          "this product; absence from allergens_tags is not a "
                          "clearance"),
                    # "inferred" at confidence 0.0, not "declared": nothing was
                    # declared. The only thing inferred is that we do not know,
                    # and the zero says how much weight that carries.
                    provenance=provenance("inferred", 0.0))]
            verdicts.append(AllergenVerdict(
                allergen=key, label=ruleset.label(key), state=state,
                evidence=tuple(found), ruleset_version=ruleset.version))
    except Exception as exc:  # noqa: BLE001 - a label read must not fail a scan
        _LOGGER.info("allergen declarations could not be read (%s)",
                     type(exc).__name__)
        return None

    return {"source": "openfoodfacts", "license": "ODbL-1.0",
            "rulesetVersion": ruleset.version,
            "verdicts": [_verdict_out(v) for v in verdicts],
            "unmappedTags": unmapped}


def _verdict_out(verdict) -> dict:
    """One ``AllergenVerdict`` as JSON. Four states, no boolean.

    ``needsReview`` is foodcore's own property and is NOT a safety boolean: it
    is true for ``may_contain`` and ``unknown`` — the two states that mean
    "a human has to look" — and false for both ``contains`` and a negative
    finding, which are opposite answers. It cannot be mistaken for "safe".
    """
    return {
        "allergen": verdict.allergen,
        "label": verdict.label,
        "state": verdict.state.value,
        "needsReview": verdict.needs_review,
        "evidence": [{"matchedTerm": e.matched_term, "ruleId": e.rule_id,
                      "kind": e.match_kind, "contributes": e.contributes.value,
                      "note": e.note,
                      "provenance": e.provenance.to_dict() if e.provenance else None}
                     for e in verdict.evidence],
    }
