# Edibl frontend — design system & UX patterns

Vue 3 (`<script setup>`) · vue-router (hash) · one global stylesheet (`src/style.css`)
· thin fetch wrapper (`src/api.js`). No component library — shared UI is expressed
through global CSS classes + a few primitives. Keep it that way: reach for a shared
pattern before inventing a one-off.

## Design tokens (`style.css :root`)
Colours: `--accent` (+ `--accent-hover`, `--accent-soft`), `--bg`, `--surface`,
`--surface-2`, `--border`, `--text`, `--muted`, status `--danger`/`--warning`/`--success`.
Also `--primary`/`--surface-raised` **aliases** (some scoped styles reference them —
don't reintroduce hardcoded hex). Radius `--radius`/`--radius-sm`; `--shadow`,
`--popover-shadow`. Light + dark via `prefers-color-scheme`. **Use tokens, not hex.**

## Reusable classes
- Layout: `.app-shell`, `.sidebar` + `.nav-link`, `.page-head`, `.toolbar`, `.content`, `.row`/`.wrap`, `.grow`, `.divider`.
- Surfaces: `.card`, `.card-grid`, `.stat`/`.stat-grid`.
- Buttons: base `button` (accent) + `.secondary`, `.ghost`, `.danger`, `.sm`, `.linkbtn`. **One primary per view.**
- Forms: `input/select/textarea`, `label.field > span` (label above — never placeholder-as-label).
- Data: `table`, `.chip` (accent pill; `.low` = warning), `.badge` (+ `.fresh`/`.expiring`/`.expired`/`.unknown`).
- Task-first: `.omnibox`, `.smart`/`.smart-grid` (tap-through cards), `.seg` (segmented control), `.sheet` (action sheet), `.drawer`.
- States: `.empty` (+ `.ico`), `.sr-only`, `.skip-link`.

## Primitives (don't reinvent)
- **Toasts** — `import { ui } from '../ui'`. `ui.success/info/error(msg, { action:{label,run} })`.
  Rendered by `<Toaster>` (mounted once in `App.vue`) with `aria-live`. Errors persist;
  others auto-dismiss; Undo is a toast action. **Never use `alert()`.**
- **Modals** — reuse the `.modal-backdrop > .card.modal` markup AND add
  `v-trap="closeFn"` to the inner element. The directive gives role=dialog, focus-in,
  Tab-trap, Esc-to-close, focus return, and scroll-lock. Backdrop closes via `@click.self`.
- **Chat bus** — `import { askEdibl } from '../chat'` opens the assistant prefilled.

## Conventions
- **Feedback**: inline near-the-control messages for form context (e.g. Settings'
  myMeal card); global toasts for completed background actions; `confirm()` only for
  destructive/irreversible actions; Undo via toast where the backend supports reversal.
- **Async**: wrap mount loads in try/catch → a clear message (+ retry) and a `loading`
  state; parallelize independent fetches with `Promise.all`. Every list has loading /
  empty / error / success states.
- **Accessibility**: global `:focus-visible` ring; a skip link; semantic `<nav>`/`<main>`;
  `aria-hidden` on decorative emoji; accessible names on icon-only buttons; real
  `<form>` elements; `prefers-reduced-motion` honored on every animation.
- **Deep links**: `/stock?add=1` (open add), `?focus=expiring|open|low`, `?reconcile=<loc>`.

## Known follow-ups
- No frontend test runner yet — recommend Playwright e2e in CI for the core journeys
  (add stock, consume + undo, reconcile, mark-low, connect myMeal).
- Tables are small (household scale); add sorting/pagination only if a list grows large.
- Consider extracting `Modal.vue`/`Field.vue` components if the inline patterns drift.
