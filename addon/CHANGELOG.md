# Changelog

All notable changes to the Edibl add-on.

## 1.4.1

- Add-on **icon** and **logo**, an "Add to my Home Assistant" repository button,
  a web favicon, and this changelog.

## 1.4.0

- **Shopping list as a Home Assistant To-do List** entity (HACS integration) —
  add/complete/delete, two-way synced.
- **Receipt photo → items**: snap a photo of a receipt in Bulk add and a
  vision-capable model (gpt-4o / Claude / `llava`) extracts the items.
- **Data export/import** — full JSON snapshot + stock CSV, additive import (a new
  **Data** page).
- Advisory **security-scan CI** (pip-audit, npm audit, gitleaks, Trivy).

## 1.3.0

- **Paste a receipt / order → auto-extract** items with the LLM, review, then add.
- New **`homeassistant`** LLM provider — reuse Home Assistant's own conversation
  agent (completion-only; powers extraction and a relay chat).

## 1.2.0

- The chat assistant now **requires an LLM provider** (ollama / openai /
  anthropic); the limited built-in fallback was removed. Without a provider the
  chat shows setup guidance.

## 1.1.0

- **User-driven** categories, units, and freshness (free-form + autocomplete).
- **Product grouping** — distinct products (organic vs filtered milk) roll up
  under one group while keeping separate shelf-lives; grouped stock view.
- **Full agent CRUD** on stock via the chat assistant and MCP tools.
- Renamed ripeness → **freshness**; added an internal **source** field.

## 1.0.0

- Initial Home Assistant add-on: Ingress-served Edibl, prebuilt multi-arch images
  (aarch64 / amd64) via GitHub Actions, the MCP tool server, and a
  provider-neutral chat assistant (Ollama / OpenAI / Anthropic).
- Lifecycle learning (personalized shelf life from your losses), flexible bulk
  add, barcode intake, wine/freezer views, and the myMeal reconciliation bridge.
