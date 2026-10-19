/**
 * The panel's job is to keep two things apart that look alike:
 * "we never screened this" and "a label was read and declared nothing".
 * Neither is "free from", and these tests exist mostly to keep that true.
 */
import { describe, expect, it } from 'vitest'
import { createSSRApp } from 'vue'
import { renderToString } from 'vue/server-renderer'
import AllergenPanel from '../AllergenPanel.vue'

const KEYS = ['milk', 'egg', 'fish', 'crustacean_shellfish', 'tree_nuts', 'peanuts',
  'wheat', 'soybeans', 'sesame', 'gluten', 'sulfites', 'molluscs']

function label(k) {
  return k.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())
}

/** A block shaped exactly like the backend's, with `states` overriding keys. */
function block(states = {}, extra = {}) {
  return {
    source: 'openfoodfacts',
    license: 'ODbL-1.0',
    rulesetVersion: 'allergens/2026.10.1',
    screenedAt: '2026-10-19',
    verdicts: KEYS.map((k) => ({
      allergen: k,
      label: label(k),
      state: states[k]?.state || 'unknown',
      rulesetKeyed: true,
      needsReview: (states[k]?.state || 'unknown') !== 'contains',
      ...(states[k]?.terms ? { terms: states[k].terms } : {}),
      ...(states[k]?.note ? { note: states[k].note } : {}),
    })),
    declaresNothing: Object.keys(states).length === 0,
    unmappedTags: [],
    unmappedCount: 0,
    ...extra,
  }
}

/**
 * Rendered markup with HTML comments stripped. The component is heavily
 * commented and Vue's SSR emits those comments verbatim, so an un-stripped
 * string matches prose ABOUT the vocabulary as well as the vocabulary itself —
 * which silently broke the ordering and row-merging assertions below.
 */
async function render(props) {
  const html = await renderToString(createSSRApp(AllergenPanel, props))
  return html.replace(/<!--[\s\S]*?-->/g, '')
}

describe('AllergenPanel', () => {
  it('renders a declared allergen as "Contains" with a solid danger rail', async () => {
    const html = await render({
      block: block({ milk: { state: 'contains', terms: ['en:milk'] } }),
    })
    expect(html).toContain('Contains')
    expect(html).toContain('Milk')
    expect(html).toContain('al-tone-bad al-text-bad')
    // The dashed rail belongs to `unknown` alone — a finding must never render
    // as structurally incomplete.
    expect(html).not.toContain('al-tone-bad al-text-bad dashed')
  })

  it('renders traces as "May contain"', async () => {
    const html = await render({
      block: block({ peanuts: { state: 'may_contain', terms: ['en:peanuts'] } }),
    })
    expect(html).toContain('May contain')
    expect(html).toContain('Peanuts')
    expect(html).toContain('al-tone-warn')
  })

  // ---- the distinction the whole feature exists for ----------------------- //
  it('says a never-screened product was not screened, with no verdict rows', async () => {
    const html = await render({ block: null })
    expect(html).toContain('This product has not been screened')
    expect(html).toContain('not the same as it being free from them')
    // No verdict rows at all: twelve "Not screened" lines would imply we looked.
    expect(html).not.toContain('al-rows')
  })

  it('says a screened-but-empty label declares nothing, and is NOT free from', async () => {
    const html = await render({ block: block() })
    expect(html).toContain('The label declares no allergens')
    // SSR escapes the quotes in the copy.
    expect(html).toContain('not &quot;free from&quot;')
    // This one DOES render rows — a label was read.
    expect(html).toContain('al-rows')
    expect(html).toContain('Not screened')
    expect(html).toContain('allergens/2026.10.1')
  })

  it('renders never-screened and declares-nothing as visibly different panels', async () => {
    const never = await render({ block: null })
    const nothing = await render({ block: block() })
    expect(never).not.toEqual(nothing)
    // The never-screened panel has the notice; the declares-nothing one has rows.
    expect(never).toContain('al-notice')
    expect(never).not.toContain('al-rows')
    expect(nothing).not.toContain('al-notice')
    expect(nothing).toContain('al-rows')
  })

  // ---- vocabulary --------------------------------------------------------- //
  it('renders `unknown` as "Not screened", never as a claim about the food', async () => {
    const html = await render({ block: block({ milk: { state: 'contains' } }) })
    expect(html).toContain('Not screened')
    for (const banned of ['No milk', 'free from', 'Free from', 'Safe', 'safe to eat',
      'allergen-free', 'None found']) {
      expect(html).not.toContain(banned)
    }
  })

  it('never emits the words safe / free / ok as state vocabulary', async () => {
    const html = (await render({ block: block({ milk: { state: 'contains' } }) })).toLowerCase()
    expect(html).not.toMatch(/\bsafe\b/)
    expect(html).not.toMatch(/\bok\b/)
    // "free" appears only inside the caveat "free from them", never as a state.
    expect(html).not.toMatch(/>\s*free\s*</)
  })

  it('gives unknown a dashed rail and may_contain a solid one', async () => {
    const html = await render({
      block: block({ peanuts: { state: 'may_contain', terms: ['en:peanuts'] } }),
    })
    // Both are --warning; the dash is the only difference, so it must be present
    // and must apply to the unscreened tone only.
    expect(html).toContain('al-tone-unscreened')
    expect(html).toContain('al-tone-warn')
  })

  // ---- ordering ----------------------------------------------------------- //
  it('sorts findings above Not screened, and never collapses unknown away', async () => {
    const html = await render({
      block: block({
        milk: { state: 'contains', terms: ['en:milk'] },
        peanuts: { state: 'may_contain', terms: ['en:peanuts'] },
      }),
    })
    const iContains = html.indexOf('Contains')
    const iMay = html.indexOf('May contain')
    const iUnknown = html.indexOf('Not screened')
    expect(iContains).toBeGreaterThan(-1)
    expect(iContains).toBeLessThan(iMay)
    expect(iMay).toBeLessThan(iUnknown)
    // The ten remaining allergens are still named, not hidden in a footnote.
    expect(html).toContain('Sesame')
    expect(html).toContain('Molluscs')
  })

  it('merges rows that agree on state and reason so one finding is not lost', async () => {
    const html = await render({ block: block({ milk: { state: 'contains', terms: ['en:milk'] } }) })
    // Eleven unknowns collapse to ONE row with eleven names, not eleven rows.
    expect(html.match(/Not screened/g)).toHaveLength(1)
    expect(html.match(/Contains/g)).toHaveLength(1)
  })

  // ---- provenance --------------------------------------------------------- //
  it('states the source, date and ruleset, and refuses to call itself a safety check', async () => {
    const html = await render({ block: block({ milk: { state: 'contains' } }) })
    expect(html).toContain('Open Food Facts')
    expect(html).toContain('2026-10-19')
    expect(html).toContain('it is not a safety check')
  })

  it('surfaces a declared allergen the ruleset could not key', async () => {
    const b = block({ milk: { state: 'contains' } }, {
      unmappedCount: 1, unmappedTags: [{ tag: 'en:celery', declaredIn: 'allergens_tags' }],
    })
    const html = await render({ block: b })
    expect(html).toContain('en:celery')
    expect(html).toContain('outside the ruleset')
  })
})

describe('AllergenPanel — reason display', () => {
  it('merges traces that differ only by their own name into one row', async () => {
    // Two nuts off the same traces line used to render as two near-identical
    // "May contain" blocks whose only difference was a machine tag.
    const html = await render({
      block: block({
        peanuts: { state: 'may_contain', terms: ['en:peanuts'] },
        tree_nuts: { state: 'may_contain', terms: ['en:tree-nuts'] },
      }),
    })
    expect(html.match(/May contain/g)).toHaveLength(1)
    expect(html).toContain('Peanuts · Tree nuts')
  })

  it('hides a tag that only repeats the allergen name', async () => {
    const html = await render({ block: block({ milk: { state: 'contains', terms: ['en:milk'] } }) })
    expect(html).not.toContain('en:milk')
    expect(html).toContain('Milk')
  })

  it('keeps a tag that explains a non-obvious match', async () => {
    const html = await render({ block: block({ milk: { state: 'contains', terms: ['en:lactose'] } }) })
    expect(html).toContain('en:lactose')
  })
})
