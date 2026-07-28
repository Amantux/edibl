"""Streaming chat (/assistant/chat/stream): NDJSON deltas + terminal done, and the
household streaming-default setting. Edibl chat is stateless, so the endpoint just
frames run_chat_stream's events."""
import json

from app.services import assistant


def _fake_stream(gid, messages):
    yield {"type": "delta", "text": "Hello "}
    yield {"type": "delta", "text": "there."}
    yield {"type": "done", "reply": "Hello there.", "actions": [],
           "provider": "ollama", "model": "llama3.1", "enabled": True}


def _lines(resp):
    return [json.loads(ln) for ln in resp.get_data(as_text=True).splitlines() if ln.strip()]


def test_chat_stream_emits_deltas_then_done(auth_client, monkeypatch):
    monkeypatch.setattr(assistant, "run_chat_stream", _fake_stream)
    r = auth_client.post("/api/v1/assistant/chat/stream", json={"message": "hi"})
    assert r.status_code == 200
    events = _lines(r)
    assert [e["type"] for e in events[:2]] == ["delta", "delta"]
    done = events[-1]
    assert done["type"] == "done" and done["reply"] == "Hello there."


def test_chat_stream_requires_auth(client):
    assert client.post("/api/v1/assistant/chat/stream", json={"message": "hi"}).status_code == 401


def test_chat_stream_requires_messages(auth_client):
    assert auth_client.post("/api/v1/assistant/chat/stream", json={}).status_code == 422


def test_chat_streaming_default_is_post_and_owner_can_set(auth_client):
    assert auth_client.get("/api/v1/assistant/config").get_json()["stream"] is False
    assert auth_client.put("/api/v1/assistant/chat-settings", json={"stream": True}).get_json()["stream"] is True
    assert auth_client.get("/api/v1/assistant/config").get_json()["stream"] is True
