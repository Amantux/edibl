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
import time
from datetime import datetime, timezone
from functools import lru_cache

from ..logsafe import scrub

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

# foodcore provenance methods a bare float may carry. "estimated" is excluded:
# it means a convention stood in for a measurement, and a float cannot say so.
# "inferred" likewise — Edibl has no way to show either as anything but a fact.
_TRUSTED_METHODS = frozenset({"measured", "declared", "calculated"})

# A bound on the resolution cache. Food names come from user-supplied product
# text once there is a caller, so this must not be able to grow without limit;
# the dataset has ~7,800 foods, so the cap is generous and the clear is cheap.
_FOOD_CACHE_MAX = 2048

# How long a failed dataset open is remembered before it is retried. Long
# enough that a permanent failure costs one attempt a minute instead of one per
# call, short enough that a transient one heals without a restart.
_RETRY_AFTER_S = 60.0

_lock = threading.Lock()
_dataset_cache: dict[str, object] = {}
_dataset_failed_at: dict[str, float] = {}
_food_cache: dict[str, object] = {}


def reset_cache() -> None:
    """Drop the cached dataset handle, failures and resolutions (tests)."""
    with _lock:
        _dataset_cache.clear()
        _dataset_failed_at.clear()
        _food_cache.clear()
    _off_allergen_states.cache_clear()


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
        # scrub: the path is composed from $TMPDIR, which is operator-supplied
        # and must not be able to forge a log line.
        _LOGGER.warning("could not create the food reference cache directory "
                        "(%s); food-aware conversion is unavailable", scrub(exc))
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
    import zlib

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
    # A truncated or corrupt asset raises EOFError or zlib.error, NEITHER of
    # which is an OSError — only gzip.BadGzipFile is. Catching OSError alone
    # made the docstring's "never raises" false, and left the cleanup below
    # unreachable, so every boot leaked a ~10 MB partial into the temp dir.
    except (OSError, EOFError, zlib.error) as exc:
        # scrub: an OSError carries the path, which is composed from $TMPDIR —
        # operator-supplied, so it must not be able to forge a log line.
        _LOGGER.warning("could not expand the food reference dataset (%s: %s); "
                        "food-aware conversion is unavailable",
                        type(exc).__name__, scrub(exc))
        return ""
    finally:
        # In `finally`, not in the error branch: os.replace already consumed
        # `tmp` on success, and on failure it must go however we got here.
        try:
            os.unlink(tmp)
        except OSError:
            pass
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
        failed_at = _dataset_failed_at.get(key)
        if failed_at is not None and time.monotonic() - failed_at < _RETRY_AFTER_S:
            return None

    # Outside the lock: the expansion is ~10 MB of I/O and opening the dataset
    # touches the filesystem, and holding a module-global lock across that
    # blocks every other thread in the worker on the first request. Two threads
    # racing here cost one redundant expansion (os.replace makes it atomic) and
    # one redundant handle, which is strictly cheaper.
    ds = None
    try:
        import foodcore

        if operator_owned:
            # allow_minimal=False: an operator who named a file wants a loud
            # failure here, not foodcore's definitional-facts-only fallback
            # quietly standing in for it.
            ds = foodcore.load_dataset(allow_minimal=False)
        else:
            path = _shipped_dataset_path()
            ds = foodcore.load_dataset(path, allow_minimal=False) if path else None
    except Exception as exc:  # noqa: BLE001 - a bad dataset must not break the app
        # Named, not echoed: foodcore's messages are curated, but the path can
        # come from operator config, so only the exception class is logged.
        _LOGGER.warning("the food reference dataset could not be opened (%s); "
                        "food-aware conversion is unavailable",
                        type(exc).__name__)
    if ds is None:
        # A failure is remembered, but only for _RETRY_AFTER_S. Caching it
        # forever meant a full disk or a half-written file at boot disabled
        # food-aware conversion in that worker until someone restarted it;
        # caching it not at all meant a PERMANENT failure (a corrupt asset, a
        # hostile temp dir) re-ran the whole path — a ~10 MB write and a WARNING
        # — on every single call, which a per-item loop turns into a log flood.
        with _lock:
            _dataset_failed_at[key] = time.monotonic()
        return None
    with _lock:
        _dataset_failed_at.pop(key, None)
        return _dataset_cache.setdefault(key, ds)


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
        if len(_food_cache) >= _FOOD_CACHE_MAX:
            # Clear rather than evict one entry: this is a memo of a pure
            # lookup over an immutable dataset, so losing it costs a repeat
            # query and nothing else — an LRU's bookkeeping is not worth it.
            _food_cache.clear()
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

    The provenance foodcore returns is deliberately dropped here, so the one
    part of it a bare float cannot carry is turned into a REFUSAL instead:
    ``method="estimated"`` means foodcore applied a convention rather than a
    published measurement (a garlic clove at 0.55 confidence), and ADR-0002's
    whole premise is that Edibl distinguishes exact from estimated. Handing an
    estimate back as a float indistinguishable from `convert`'s exact
    arithmetic would erase that distinction, so this returns only answers
    foodcore measured, declared or calculated. The first caller that can
    actually represent an estimate should widen this seam to return the
    ``Outcome`` — not relax the check.
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
    method = getattr(outcome.provenance, "method", "")
    if method not in _TRUSTED_METHODS:
        _LOGGER.debug("food-aware conversion was %s, not a measurement; refused",
                      method)
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


# Leading negations that turn a tag into a CLAIM of absence. foodcore's claim
# rule reads the suffix form ("gluten free") and nothing else, so these are
# rewritten into it rather than judged here: the spelling of an OFF tag is this
# adapter's problem, but what a claim is worth is the shared ruleset's, and it
# says a claim downgrades to may_contain because it has been read, not verified.
# Without this, "en:no-gluten" matched the bare phrase and read `contains` — the
# opposite answer, from community-editable data.
_TAG_NEGATIONS = ("no ", "non ", "not ", "without ", "free from ", "free of ",
                  "zero ", "sans ", "sin ", "senza ", "ohne ")

# A tag is a taxonomy term, not prose. Cap it before it is reflected into a
# response as an allergen label.
_TAG_MAX = 64


def _off_tag_term(tag: str) -> str:
    """An OFF allergen tag's bare term, or "" when there is nothing in it.

    Also the DEDUPE key: OFF tags arrive language-prefixed (``en:milk``,
    ``fr:lait``), sometimes repeated, and sometimes in a different case, and
    ``en:celery`` / ``EN:CELERY`` are one declaration, not three. "" means the
    tag carried no term at all (``""``, ``"en:"``) — community-entered noise,
    not a declaration, and it must not be reported as an unkeyed allergen.
    """
    term = (tag or "").strip().lower()[:_TAG_MAX]
    if len(term) >= 3 and term[2] == ":" and term[:2].isalpha():
        term = term[3:]
    return term.replace("-", " ").strip()


@lru_cache(maxsize=256)
def _off_allergen_states(term: str) -> tuple[tuple[str, object], ...]:
    """An OFF allergen term as ((foodcore key, state), …). Empty = not keyable.

    Resolved by **screening the tag's term through foodcore's own phrase rules**
    rather than ``resolve_allergen_key``. That distinction is load-bearing:
    ``resolve_allergen_key`` is an alias table for what a *person* types into a
    profile ("nuts", "dairy"), and it has no spelling for most of what OFF
    actually publishes. Screening keys the whole taxonomy and every sub-tag
    through one ruleset:

    * ``en:sulphur-dioxide-and-sulphites`` → ``sulfites``, where the alias table
      had nothing — so a wine that explicitly declares sulphites used to come
      back "unknown". A *declaration* downgraded to unknown is worse than the
      absence case this feature exists to be honest about, because `unknown` is
      permitted under a PREFERENCE policy.
    * ``en:almonds``, ``en:hazelnuts``, ``en:cashew-nuts`` → ``tree_nuts``;
      ``en:barley``, ``en:rye``, ``en:spelt`` → ``gluten``; ``en:lactose`` and
      ``en:milk-proteins`` → ``milk``. The alias table keyed none of them.
    * One tag can map to several allergens, and the implication rules come with
      it (``en:wheat`` → wheat AND gluten), so there is no second table of ours
      to drift.
    * ``en:oats`` → wheat/gluten ``may_contain``, not ``contains``, because
      foodcore's rule says the link is precautionary even though the
      declaration is certain.

    OFF tags are language-prefixed (``en:milk``, ``fr:lait``); the prefix is
    stripped and a non-English term then screens to nothing and is reported as
    unmapped rather than guessed at. Celery, mustard and lupin are declarable
    in the EU and genuinely outside foodcore's ruleset — they screen to nothing
    too, and the caller surfaces them.

    One known over-caution, left as it is on purpose: a *claim* of absence that
    somehow lands in an allergen field (``en:gluten-free``, ``en:nut-free``)
    screens to ``may_contain``, because foodcore downgrades an unverifiable
    claim rather than trusting it. Those belong in OFF's ``labels_tags``, not
    here, and the error points the safe way — over-cautious, not falsely clear.
    Overriding foodcore's claim policy from this side would be a second opinion
    on exactly the judgement the shared ruleset exists to hold.
    """
    from foodcore.allergens import (AllergenState, SensitivityProfile,
                                    allergen_ruleset, screen_text)

    if not term:
        return ()
    for negation in _TAG_NEGATIONS:
        if term.startswith(negation):
            term = f"{term[len(negation):].strip()} free"
            break
    # Every ruleset key, not the US-major default: screening against the
    # default profile silently cannot see sulfites, gluten or molluscs.
    ruleset = allergen_ruleset()
    profile = SensitivityProfile(allergens=tuple(ruleset.keys))
    screening = screen_text(term, profile)
    return tuple((v.allergen, v.state) for v in screening.verdicts
                 if v.state in (AllergenState.CONTAINS, AllergenState.MAY_CONTAIN))


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
        # Keyed on the NORMALISED term, so `en:celery` listed in both OFF fields
        # — which it routinely is — is one declaration rather than two rows for
        # the same allergen. Two rows is not cosmetic: anything that keys on
        # `allergen` (a dict, a template loop, this module's own test helper)
        # keeps the LAST one, which is the weaker `may_contain`, silently losing
        # a certain declaration. Insertion order gives the precedence for free:
        # the declared pass runs before the traces pass, so the first writer is
        # always the stronger state.
        unmapped: dict[str, dict] = {}

        def declare(tags, field: str, state, method: str, confidence: float) -> None:
            # A non-list is community-entered garbage, not one tag: iterating a
            # string would report each character as an unmapped allergen.
            for tag in tags if isinstance(tags, (list, tuple)) else ():
                if not isinstance(tag, str):
                    continue
                term = _off_tag_term(tag)
                if not term:
                    continue
                keyed = _off_allergen_states(term)
                if not keyed:
                    unmapped.setdefault(term, {"tag": tag[:_TAG_MAX],
                                               "declaredIn": field})
                    continue
                for key, rule_state in keyed:
                    # The declaration and the rule each carry their own
                    # certainty, and the weaker one wins: `en:oats` in
                    # allergens_tags is a certain declaration of oats, but
                    # foodcore's rule says the gluten link is precautionary, so
                    # the answer is may_contain. Only a certain declaration of a
                    # certain rule is `contains`.
                    #
                    # Note what this does NOT do: it never weakens a tag's own
                    # named allergen, only an implied one (oats → oats stays
                    # out of it; oats → gluten softens). The EU treats oats as a
                    # gluten cereal, so a coeliac reading this under a
                    # PREFERENCE policy would not be blocked by `may_contain` —
                    # STRICT does block it, and STRICT is foodcore's default for
                    # a profile that exists to avoid an allergen.
                    combined = (state if state is rule_state
                                else AllergenState.MAY_CONTAIN)
                    certain = combined is rule_state is state
                    evidence.setdefault(key, []).append(Evidence(
                        ingredient="", matched_term=tag,
                        rule_id=f"off:{field}", rule_version=ruleset.version,
                        match_kind="declared_tag" if certain else "declared_tag_rule",
                        contributes=combined,
                        note=f"declared in Open Food Facts {field}",
                        provenance=provenance(
                            method if certain else "inferred",
                            confidence if certain else confidence * 0.9)))

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

        # A declared allergen foodcore cannot key goes in `verdicts` too, not
        # only in `unmappedTags`. The twelve ruleset rows render as a complete
        # allergen picture, so a consumer looping them — the obvious shape —
        # would drop a declared celery or lupin silently. The `off:` prefix and
        # `rulesetKeyed: false` say these came from the label rather than the
        # ruleset; they are still a declaration, and a declaration must never be
        # reachable only by reading a second array.
        for term, entry in unmapped.items():
            field = entry["declaredIn"]
            state = (AllergenState.CONTAINS if field == _DECLARED
                     else AllergenState.MAY_CONTAIN)
            verdicts.append(AllergenVerdict(
                # The normalised term, not the raw tag: the key has to be one
                # value per declaration, and the raw spelling is already in the
                # evidence and in `unmappedTags`.
                allergen=f"off:{term}",
                label=term.capitalize(),
                state=state,
                evidence=(Evidence(
                    ingredient="", matched_term=entry["tag"],
                    rule_id=f"off:{field}", rule_version=ruleset.version,
                    match_kind="declared_tag_unkeyed", contributes=state,
                    note=("declared in Open Food Facts but outside foodcore's "
                          "ruleset, so no rule was applied to it"),
                    provenance=provenance("declared", 0.8)),),
                ruleset_version=ruleset.version))
    except Exception as exc:  # noqa: BLE001 - a label read must not fail a scan
        _LOGGER.info("allergen declarations could not be read (%s)",
                     type(exc).__name__)
        return None

    return {"source": "openfoodfacts", "license": "ODbL-1.0",
            "rulesetVersion": ruleset.version,
            "verdicts": [_verdict_out(v) for v in verdicts],
            "unmappedTags": list(unmapped.values()),
            "unmappedCount": len(unmapped)}


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
        "rulesetKeyed": not verdict.allergen.startswith("off:"),
        "evidence": [{"matchedTerm": e.matched_term, "ruleId": e.rule_id,
                      "kind": e.match_kind, "contributes": e.contributes.value,
                      "note": e.note,
                      "provenance": e.provenance.to_dict() if e.provenance else None}
                     for e in verdict.evidence],
    }
