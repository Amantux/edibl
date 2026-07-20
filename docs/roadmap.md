# Roadmap

## Planned (interfaces to add)
- **Computer vision intake** — photograph a fridge/haul; detect items and
  quantities to auto-create stock lots. *Interface:* a `VisionEstimator` service
  (image → [{name, category, quantity, confidence}]) behind a feature flag; the
  add-stock flow already accepts the resulting structured items. Not yet wired —
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
- Barcode → product metadata lookup (UPC database) for one-scan intake.

Every future item ships as an **interface + tests + docs** first; nothing that
changes security/privacy guarantees is enabled silently.
