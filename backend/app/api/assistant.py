"""Chat assistant endpoints — a conversational surface over the same inventory
actions the MCP server exposes, available from a widget on every screen."""
import ipaddress
import socket
from urllib.parse import urlparse

from flask import Blueprint, request, jsonify

from ..auth import login_required, owner_required, current_group
from ..extensions import limiter, db
from ..services import assistant

bp = Blueprint("assistant", __name__)


def _llm_url_ok(url):
    """Guard the user-supplied LLM base URL against SSRF to link-local services
    (notably the cloud metadata endpoint 169.254.169.254 / fe80::) while still
    allowing loopback and private LAN, where Ollama legitimately runs. Blank → ok
    (falls back to the configured default). Returns (ok, error). Note: this checks
    the resolved address now; it is not hardened against DNS-rebinding, which is an
    acceptable residual for a semi-trusted owner configuring their own endpoint."""
    if not url:
        return True, None
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False, "base URL must be http or https"
    host = parsed.hostname
    if not host:
        return False, "base URL has no host"
    try:
        infos = socket.getaddrinfo(host, None)
    except (OSError, UnicodeError):
        # Unresolvable / malformed host: let the real request fail rather than 500.
        return True, None
    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        # An IPv4-mapped IPv6 address (::ffff:169.254.169.254) connects to the real
        # IPv4 on a dual-stack host, so test the mapped v4, not the v6 wrapper.
        if addr.version == 6 and addr.ipv4_mapped is not None:
            addr = addr.ipv4_mapped
        if addr.is_link_local:
            return False, "base URL host is not allowed"
    return True, None


@bp.get("/assistant/config")
@login_required
def config():
    """What the chat widget needs: whether an LLM is wired up, and which."""
    return jsonify(assistant.config_public())


@bp.get("/assistant/settings")
@login_required
def get_settings():
    """Editable provider settings for the UI (never returns the API key)."""
    return jsonify(assistant.settings_public())


@bp.put("/assistant/settings")
@owner_required
def put_settings():
    """Persist the chat provider from the UI (overrides the add-on/env default).
    Body: { provider, baseUrl, model, apiKey? }. A blank/omitted apiKey is left
    unchanged; blank provider/baseUrl/model fall back to the add-on default."""
    data = request.get_json(force=True) or {}
    provider = data.get("provider")
    if provider is not None and str(provider) not in assistant.PROVIDER_CHOICES:
        return jsonify({"error": "unknown provider"}), 422
    kwargs = {}
    if "provider" in data:
        kwargs["provider"] = str(data["provider"] or "")
    if "baseUrl" in data:
        ok, err = _llm_url_ok(str(data["baseUrl"] or ""))
        if not ok:
            return jsonify({"error": err}), 422
        kwargs["base_url"] = str(data["baseUrl"] or "")
    if "model" in data:
        kwargs["model"] = str(data["model"] or "")
    if "agentId" in data:
        kwargs["agent_id"] = str(data["agentId"] or "")
    # Only update the key when a non-empty value is supplied (don't clobber it).
    if data.get("apiKey"):
        kwargs["api_key"] = str(data["apiKey"])
    return jsonify(assistant.save_settings(current_group().id, **kwargs))


@bp.delete("/assistant/settings")
@owner_required
def reset_settings():
    """Clear the UI overrides so the assistant falls back to the add-on/env
    default (the 'Reset to add-on default' action)."""
    return jsonify(assistant.reset_settings(current_group().id))


@bp.post("/assistant/models")
@owner_required
@limiter.limit("30/minute")
def list_models():
    """List models available on the (optionally overridden) provider — the UI polls
    this to populate the model picker. Body: { provider?, baseUrl?, apiKey? }."""
    data = request.get_json(silent=True) or {}
    provider = data.get("provider")
    if provider is not None and str(provider) not in assistant.PROVIDER_CHOICES:
        return jsonify({"error": "unknown provider"}), 422
    ok, err = _llm_url_ok(data.get("baseUrl") or "")
    if not ok:
        return jsonify({"error": err}), 422
    return jsonify(assistant.list_models(
        provider=provider, base_url=data.get("baseUrl"), api_key=data.get("apiKey")))


@bp.post("/assistant/chat")
@login_required
@limiter.limit("30/minute")
def chat():
    """Body: { messages: [{role, content}] } or { message: "..." }. Returns the
    assistant's reply plus the list of inventory actions it took."""
    data = request.get_json(force=True) or {}
    messages = data.get("messages")
    if not messages and data.get("message"):
        messages = [{"role": "user", "content": str(data["message"])}]
    if not messages:
        return jsonify({"error": "messages[] or message required"}), 422
    messages = [{"role": m.get("role", "user"), "content": str(m.get("content", ""))[:4000]}
                for m in messages if m.get("content")][-20:]
    result = assistant.run_chat(current_group().id, messages)
    return jsonify(result)


@bp.post("/assistant/undo")
@login_required
@limiter.limit("60/minute")
def undo():
    """Reverse a single action the chat took. Body: { undo: <descriptor> } from a
    chat response's actions[].undo. All ops are group-scoped."""
    data = request.get_json(force=True) or {}
    descriptor = data.get("undo")
    if not isinstance(descriptor, dict) or not descriptor.get("op"):
        return jsonify({"error": "undo descriptor required"}), 422
    try:
        message = assistant.apply_undo(current_group().id, descriptor)
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        return jsonify({"error": f"undo failed: {exc}"}), 400
    return jsonify({"ok": True, "message": message})
