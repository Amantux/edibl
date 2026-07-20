"""Chat assistant endpoints — a conversational surface over the same inventory
actions the MCP server exposes, available from a widget on every screen."""
from flask import Blueprint, request, jsonify

from ..auth import login_required, current_group
from ..extensions import limiter
from ..services import assistant

bp = Blueprint("assistant", __name__)


@bp.get("/assistant/config")
@login_required
def config():
    """What the chat widget needs: whether an LLM is wired up, and which."""
    return jsonify(assistant.config_public())


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
