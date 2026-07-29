"""Chat latency metrics: every chat/stream request records a timing sample
(mode, provider, model, ttft for streams, total), exposed at /assistant/metrics."""
from app.services import assistant


def test_post_chat_records_a_metric(auth_client, monkeypatch):
    monkeypatch.setattr(assistant, "run_chat", lambda gid, msgs: {
        "reply": "hi", "actions": [], "provider": "ollama", "model": "m", "enabled": True})
    assert auth_client.post("/api/v1/assistant/chat", json={"message": "hello"}).status_code == 200
    m = auth_client.get("/api/v1/assistant/metrics").get_json()
    assert m["summary"]["post"]["count"] >= 1
    latest = m["recent"][0]
    assert latest["mode"] == "post" and latest["provider"] == "ollama" and latest["totalMs"] is not None


def test_stream_chat_records_ttft(auth_client, monkeypatch):
    def fake_stream(gid, msgs):
        yield {"type": "delta", "text": "hi "}
        yield {"type": "done", "reply": "hi there", "actions": [],
               "provider": "ollama", "model": "m", "enabled": True}
    monkeypatch.setattr(assistant, "run_chat_stream", fake_stream)
    r = auth_client.post("/api/v1/assistant/chat/stream", json={"message": "hi"})
    assert r.status_code == 200
    r.get_data()  # consume the stream so the generator (and done()) actually runs
    latest = auth_client.get("/api/v1/assistant/metrics").get_json()["recent"][0]
    assert latest["mode"] == "stream"
    assert latest["ttftMs"] is not None and latest["totalMs"] is not None


def test_metrics_requires_auth(client):
    assert client.get("/api/v1/assistant/metrics").status_code == 401
