# Edibl — Home Assistant add-on

Your kitchen's real food inventory, inside Home Assistant: what you have, where
it is, how fresh it is, what you're about to waste, and a chat assistant for your
pantry. Opens in the sidebar via **Ingress** (no separate login).

## Install

1. **Settings → Add-ons → Add-on Store → ⋮ → Repositories**, add:
   `https://github.com/Amantux/edibl`
2. Install **Edibl**, then **Start**. Open it from the sidebar.

**Built for ARM.** The web UI is pre-built and shipped with the add-on, so there
is **no npm build on your device** — install only fetches the small Python
backend and installs wheels (no compiling). Works on **amd64** and **aarch64**.
On a Raspberry Pi, use the **64-bit** Home Assistant OS (aarch64); 32-bit (armv7)
isn't supported because its crypto libraries have no prebuilt wheels.

Data is stored in the add-on's persistent `/data` (survives updates).

## Configuration

| Option | Meaning |
|---|---|
| `disable_auth` | Keep **on** behind Ingress (HA authenticates you). |
| `llm_provider` | `ollama` / `openai` / `anthropic`, or blank for the built-in rules assistant. |
| `llm_base_url` | e.g. `http://homeassistant.local:11434` for the HA **Ollama** add-on. |
| `llm_api_key` | Only for `openai` / `anthropic`. |
| `llm_model` | `llama3.1`, `gpt-4o-mini`, `claude-opus-4-8`, … |
| `barcode_lookup` | Enrich unknown barcodes from Open Food Facts (online). |
| `mcp_enabled` | Run the MCP tool server (for HA's MCP Client / other agents). |
| `mcp_server_token` | Bearer token to protect the MCP endpoint if you map port 7767. |

## Using Ollama (recommended) or OpenAI

The chat box (bottom-right on every screen) works with **no config** via a
built-in rules assistant. For full natural-language chat:

**Ollama, entirely local** — install the community **Ollama** add-on (or point at
any Ollama host), pull a model (`ollama run llama3.1`), then set:

```
llm_provider: ollama
llm_base_url: http://<ollama-host>:11434   # e.g. http://homeassistant.local:11434
llm_model:    llama3.1
```

**OpenAI-compatible** — `llm_provider: openai`, set `llm_api_key`, `llm_model`
(`gpt-4o-mini`), and `llm_base_url` if you use a compatible gateway.

**Anthropic** — `llm_provider: anthropic`, set `llm_api_key`, `llm_model`
(`claude-opus-4-8`).

The assistant uses the same inventory tools as the MCP server, so it can look
things up **and** act (add stock, record what you ate/tossed, edit the shopping
list). An unreachable provider falls back to the rules assistant — the chat never
goes dark.

## Talking to your pantry through HA Assist (voice)

Edibl's MCP server exposes the same tools to Home Assistant's own assistant:

1. Set `mcp_enabled: true` and a `mcp_server_token`, and map port `7767`.
2. Add HA's **Model Context Protocol** (MCP Client) integration pointing at
   `http://<addon-host>:7767/sse` with the token.
3. Use **Ollama** or **OpenAI Conversation** as your Assist agent — now "what's
   expiring?" / "add milk to the shopping list" work by voice.

See the repo's `docs/home-assistant.md` for the full wiring, plus the optional
**HACS integration** that surfaces Edibl as sensors (items in stock, expiring,
expired) and an `edibl.add_to_shopping_list` service.
