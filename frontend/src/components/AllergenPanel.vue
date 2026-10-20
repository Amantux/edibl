<script setup>
/**
 * "What this contains" — what a package LABEL declares about allergens.
 *
 * Vocabulary is mirrored from myMeal's recipe safety card deliberately, down to
 * the words: the two apps are siblings and a household uses both, so a state
 * that reads one way here and another way there is worse than either wording
 * alone. The load-bearing choice is that `unknown` renders "Not screened" — a
 * claim about US, not about the food. "No milk found" is a claim we cannot make
 * from a community-entered ingredient list.
 *
 * The panel exists to separate two things that look identical and are not:
 *   - `block === null`  nothing has ever been screened for this product
 *   - `declaresNothing` a label WAS read and declared no allergens
 * Neither is "free from". The first says we did not look; the second says the
 * label did not say. They get different copy and different structure, which is
 * the entire point of the component.
 *
 * There is no boolean here and no `safe`/`free`/`ok` — not even in a helper
 * name. Whether a state is acceptable depends on a policy (a medical allergy
 * excludes `unknown`, a dislike does not) that this component does not hold.
 */

defineProps({
  // The `allergens` block from product_out, or null when never screened.
  // No product name prop: the sheet already titles itself with it, and a second
  // copy inside the panel is one more thing to keep in sync for no gain.
  block: { type: Object, default: null },
})

// Severity, highest first. `unknown` is NOT a quiet state: "we did not check"
// belongs where it will be read, so it sorts above anything calmer and is never
// collapsed into a footnote. An unrecognised state sorts FIRST (-1), same
// reasoning — something this view does not understand must not be hidden.
const ORDER = { contains: 0, may_contain: 1, unknown: 2 }

const WORD = {
  contains: 'Contains',
  may_contain: 'May contain',
  unknown: 'Not screened',
}

function tone(state) {
  if (state === 'contains') return 'bad'
  if (state === 'may_contain') return 'warn'
  if (state === 'unknown') return 'unscreened'
  return 'unscreened'
}

/**
 * The matched tag, shown only when it adds something the allergen name does not.
 * `en:milk` under a row already labelled "Milk" is noise — a machine string in
 * front of someone deciding whether to eat this — while `en:lactose` under
 * "Milk" or `en:spelt` under "Gluten" is exactly the explanation they need.
 */
function why(verdict) {
  const name = (verdict.label || '').toLowerCase()
  return (verdict.terms || [])
    .filter((t) => t.replace(/^[a-z]{2}:/i, '').replace(/-/g, ' ').toLowerCase() !== name)
    .join(', ')
}

/**
 * Verdicts grouped into display rows: rows that agree on state AND on the reason
 * a reader can actually see merge, with their allergen names joined. Without
 * this, a label declaring one allergen renders eleven identical "Not screened"
 * lines and the one finding is lost in them — and a traces line naming three
 * nuts renders as three near-identical "May contain" blocks. Keyed on the
 * VISIBLE reason: two rows a reader cannot tell apart must not be two rows.
 */
function rows(block) {
  const groups = new Map()
  for (const v of block?.verdicts || []) {
    const terms = why(v)
    const key = `${v.state}|${terms}|${v.note || ''}`
    if (!groups.has(key)) {
      groups.set(key, {
        key, state: v.state, why: terms, note: v.note || '', labels: [],
      })
    }
    groups.get(key).labels.push(v.label)
  }
  return [...groups.values()]
    .map((g) => ({ ...g, labels: g.labels.slice().sort((a, b) => a.localeCompare(b)) }))
    .sort((a, b) => (ORDER[a.state] ?? -1) - (ORDER[b.state] ?? -1)
      || a.labels[0].localeCompare(b.labels[0]))
}
</script>

<template>
  <!-- Never screened. Said out loud and given the whole panel, because an empty
       panel here is indistinguishable from a clean one — which is the failure
       this feature exists to end. No verdict rows at all: there is nothing to
       report, and twelve "Not screened" rows would imply we had looked. -->
  <div v-if="!block" class="al">
    <h3 class="al-h">What this contains</h3>
    <div class="al-notice">
      <strong>This product has not been screened.</strong>
      No label has been read for it, so nothing is known about its allergens —
      which is not the same as it being free from them. Scanning its barcode will
      read the label if Open Food Facts has it.
    </div>
  </div>

  <div v-else class="al">
    <h3 class="al-h">What this contains
      <span class="muted al-sub">— allergens declared on the label</span>
    </h3>

    <!-- The dangerous case, and it leads with the caveat rather than trailing
         it: a label that declares nothing must not read, at a glance, as a
         free-from list. The reassuring half never stands alone. -->
    <p v-if="block.declaresNothing" class="al-lead">
      <strong>The label declares no allergens — which is not "free from".</strong>
      Open Food Facts lists allergens from community-entered ingredient text, so
      nothing below rules an allergen out. Check the packaging yourself.
    </p>

    <dl class="al-rows">
      <template v-for="row in rows(block)" :key="row.key">
        <dt :class="['al-dt', 'al-tone-' + tone(row.state), 'al-text-' + tone(row.state)]">
          {{ WORD[row.state] || row.state }}
        </dt>
        <dd :class="['al-dd', 'al-tone-' + tone(row.state)]">
          <span class="al-labels">{{ row.labels.join(' · ') }}</span>
          <span v-if="row.why" class="muted al-why">{{ row.why }}</span>
          <!-- The ruleset's own note, shown only on a hedged state: without it
               "May contain Gluten — en:oats" hides that the oat declaration was
               certain and the gluten LINK is the precautionary part. -->
          <span v-if="row.note && row.state !== 'contains'" class="muted al-why al-note">
            {{ row.note }}
          </span>
          <span v-else-if="row.state === 'unknown'" class="muted al-why">
            nothing was declared about {{ row.labels.length === 1 ? 'it' : 'these' }}
          </span>
        </dd>
      </template>
    </dl>

    <!-- A declared allergen outside the ruleset (celery, mustard, lupin are
         declarable in the EU) is already a row above; this names the raw tag so
         the reader can see what we could not key. -->
    <p v-if="block.unmappedCount" class="muted al-prov">
      {{ block.unmappedCount }} declared
      {{ block.unmappedCount === 1 ? 'allergen' : 'allergens' }}
      {{ block.unmappedCount === 1 ? 'is' : 'are' }} outside the ruleset, so no
      rule was applied to
      {{ block.unmappedCount === 1 ? 'it' : 'them' }}:
      {{ (block.unmappedTags || []).map((t) => t.tag).join(', ') }}
    </p>

    <p class="muted al-prov">
      Read from {{ block.source === 'openfoodfacts' ? 'Open Food Facts' : block.source }}
      <span v-if="block.screenedAt">on {{ block.screenedAt }}</span>
      against {{ block.rulesetVersion }}.
      This reports what one package's label says — it is not a safety check.
      Always read the packaging.
    </p>
  </div>
</template>

<style scoped>
/* A work surface: tokens only, no new colour, and the accent is deliberately
   absent — this panel has no primary action, so spending the accent here would
   compete with the sheet's real ones.

   The whole visual system is a 3px left rail, because the rail is the only
   decoration that encodes something true: how much attention the row wants.
     solid  --danger    contains
     solid  --warning   may contain
     dashed --warning   NOT SCREENED
   Dashed is doing real work. It keeps `unknown` in the attention family, so it
   cannot be mistaken for "fine", while reading as structurally incomplete
   rather than as a positive finding — and it is a second, non-colour signal, so
   the distinction survives for a colour-blind reader and in a screenshot. The
   state word is always spelled out too; colour is never the only signal. */
.al { margin: 10px 0 4px; }
.al-h {
  margin: 0 0 8px; font-size: 0.78rem; color: var(--muted);
  text-transform: uppercase; letter-spacing: 0.03em; font-weight: 600;
}
.al-sub { font-weight: 400; text-transform: none; letter-spacing: 0; }

/* STATE | names + reason. State on the left so severity is the axis the eye
   scans; 118px fits "May contain" on one line at this size. `stretch`, not
   `baseline`: a 3px dashed border across one line of text renders about two
   dashes and reads as solid, so the term is stretched to the row height to give
   the dash pattern room to actually look like a pattern. */
.al-rows {
  display: grid; grid-template-columns: 118px 1fr;
  gap: 6px 12px; margin: 0; align-items: stretch;
}
.al-dt {
  margin: 0; padding: 2px 0 2px 10px; font-weight: 600; font-size: 0.86rem;
  border-left: 3px solid var(--border);
}
.al-dd {
  margin: 0; padding: 2px 0; font-size: 0.86rem;
  display: flex; flex-wrap: wrap; align-content: flex-start; gap: 2px 8px;
  /* Width 0, not `border-left: none`, and never re-declared as a shorthand in
     the media query — the tone classes set only colour and style, so a later
     shorthand would silently reset the colour back to --border. Only the WIDTH
     changes at the breakpoint. */
  border-left: 0 solid var(--border);
}
.al-labels { font-weight: 600; }
/* Severity has to stay the visual axis. "Not screened" routinely carries eleven
   allergen names against a single declared one, so at the same weight the amber
   mass out-shouts the red finding and the ORDER above is undone by sheer area.
   The STATE WORD keeps its weight and colour — only the name list steps back. */
.al-dd.al-tone-unscreened .al-labels { font-weight: 400; }
.al-why { font-size: 0.8rem; }
/* The ruleset's note is a sentence, not a term list, so it gets its own line. */
.al-note { flex-basis: 100%; }

.al-tone-bad { border-left-color: var(--danger); }
.al-tone-warn { border-left-color: var(--warning); }
/* The one that matters. See the note above. */
.al-tone-unscreened { border-left-style: dashed; border-left-color: var(--warning); }
/* The -text variants, not the raw status tokens: a state word is the finding
   itself, and the raw tokens fail AA as text in one theme each. A 3px rule only
   needs 3:1, so the rails keep them. */
.al-text-bad { color: var(--danger-text); }
.al-text-warn { color: var(--warning-text); }
.al-text-unscreened { color: var(--warning-text); }

/* The never-screened notice. The rail is a ::before, not a border-left: a
   left-only dashed border on a rounded box bends its end dashes around the
   corner radius into little hooks, which makes the signature device look like a
   rendering bug. */
.al-notice {
  position: relative;
  margin: 0; padding: 10px 12px 10px 15px; font-size: 0.84rem;
  background: var(--surface-2); border-radius: var(--radius-sm);
}
.al-notice::before {
  content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 3px;
  border-radius: var(--radius-sm) 0 0 var(--radius-sm);
  background: repeating-linear-gradient(
    to bottom, var(--warning) 0 5px, transparent 5px 10px);
}
/* Neither shrunk NOR muted relative to the rows it qualifies: this is the clause
   that stops "declares no allergens" being read as "free from", and it was
   rendering in --muted — lower contrast than the allergen names beneath it,
   making the most load-bearing sentence on the panel the least legible thing in
   it. Full --text; the size stays small so it still reads as a lead-in. */
.al-lead { margin: 0 0 10px; font-size: 0.84rem; color: var(--text); }
.al-prov { margin: 10px 0 0; font-size: 0.78rem; }

@media (max-width: 560px) {
  /* One column: the reason text is what a phone needs the width for, and a
     118px state column would leave it two words wide.

     Stacked, the dd sits directly below the dt, so the rail CAN continue through
     it — and must. Left transparent it leaves a ~20px stub next to the state
     word, which is two dashes and not a pattern anyone reads as dashed. Running
     the tone border down the names too makes the rail 4-6 dashes tall AND
     brackets state->names as one unit, which is what the two-column alignment
     was doing before it collapsed. */
  .al-rows { grid-template-columns: 1fr; gap: 0; }
  .al-dt { padding-bottom: 0; }
  /* One separation value, on the 4/8/12/16 scale — it was 10px + 2px between
     some groups and 10px between others, so the rhythm read as uneven. */
  .al-dd { padding: 0 0 12px 10px; margin-bottom: 0; border-left-width: 3px; }
}
</style>
