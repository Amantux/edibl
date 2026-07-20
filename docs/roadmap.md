# Roadmap

## Delivered
- **Chat assistant, everywhere** — a floating assistant on every screen, backed
  by a provider-neutral LLM layer (`EDIBL_LLM_PROVIDER` = ollama | openai |
  anthropic — a provider is required). It uses the same
  inventory tools the MCP server exposes. Built for Home Assistant: point it at a
  local Ollama or any OpenAI-compatible / Anthropic endpoint.
- **Flexible bulk add** — one action to log many items (a grocery haul, a farm
  box, a butchered animal into the freezer) with shared defaults + per-row
  overrides (`POST /stock/bulk`). Butchering is now just a preset over this path.
- **Barcode intake** — scan (native `BarcodeDetector`) or type a barcode; known
  products resolve locally, unknown ones optionally enrich from Open Food Facts
  (`EDIBL_BARCODE_LOOKUP=1`) to pre-fill name/brand/category.
- **Perishable lifecycle learning** — mark how food left inventory (eaten /
  spoiled / expired / discarded) and its ripeness; Edibl learns each item's
  *real* shelf life from your losses and personalizes future expiry estimates and
  suggestions ("your bananas usually last ~5 days — buy fewer"). Waste feed on the
  dashboard; per-product insights via `GET /products/<id>/insights`.

## Planned (interfaces to add)
- **Computer vision intake** — photograph a fridge/haul; detect items and
  quantities to auto-create stock lots. *Interface:* a `VisionEstimator` service
  (image → [{name, category, quantity, confidence}]) behind a feature flag whose
  output feeds straight into the existing `POST /stock/bulk` path (same contract
  as barcode/manual intake). `services/barcode.py` is the reference shape a
  recognizer plugs into (`lookup_* → {name, brand, category}`). Not yet wired —
  needs a model/service decision (on-device vs API) and cost/privacy review.
- **Weight estimation** — scale integration or vision-based weight for meat cuts
  and bulk goods, to track quantity by mass and refine runout forecasts.
- **Receipt / purchase ingestion** — parse a grocery receipt (or a delivery
  order confirmation) into stock + purchase history, improving "how long will
  this last" predictions.

## Near-term hardening (port from HomeHoard)
- Backup + executed restore-verification scripts + DR docs.
- Security-scan workflow (CodeQL, pip-audit, Trivy, gitleaks, SBOM).
- Optional Home Assistant add-on packaging (Ingress + discovery).

## Integration deepening
- Two-way sync with myMeal meal plans (schedules → demand windows via neededBy).
- HomeHoard cross-query (e.g. "where's the vacuum sealer?").
- Voice: expose the chat assistant through Home Assistant Assist (the MCP server
  already covers the tool surface; native HA voice → assistant is the next step).

Every future item ships as an **interface + tests + docs** first; nothing that
changes security/privacy guarantees is enabled silently.
