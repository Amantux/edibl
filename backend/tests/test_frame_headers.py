"""Anti-clickjacking headers are keyed on the ingress PEER, not on auth mode.

DISABLE_AUTH is not a proxy for "behind ingress": ingress identity is honoured
even with auth enabled, so keying frame headers on auth mode blanked the HA
panel for disable_auth:false-behind-ingress AND left a standalone disable_auth
deployment unprotected. Ported from the myMeal security audit (sibling sweep).
"""
from app.config import Config
from app import create_app


def _noauth_client(tmp_path):
    class C(Config):
        DATA_DIR = str(tmp_path)
        DATABASE_URL = f"sqlite:///{tmp_path}/na.db"
        SECRET_KEY = "test-secret-key-that-is-long-enough-32b"
        DISABLE_AUTH = True
    return create_app(C).test_client()


def test_frame_headers_present_for_a_non_ingress_request(client):
    r = client.get("/api/v1/status")
    assert r.headers.get("X-Frame-Options") == "SAMEORIGIN"
    assert "frame-ancestors 'self'" in r.headers.get("Content-Security-Policy", "")


def test_frame_headers_omitted_from_the_ingress_peer(client):
    r = client.get("/api/v1/status",
                   environ_overrides={"REMOTE_ADDR": "172.30.32.2"})
    assert "X-Frame-Options" not in r.headers
    assert "frame-ancestors" not in r.headers.get("Content-Security-Policy", "")


def test_frame_headers_present_for_non_ingress_even_with_auth_disabled(tmp_path):
    r = _noauth_client(tmp_path).get("/api/v1/status")   # no ingress peer
    assert r.headers.get("X-Frame-Options") == "SAMEORIGIN"
    assert "frame-ancestors 'self'" in r.headers.get("Content-Security-Policy", "")
