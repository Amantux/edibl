"""Pinned-IP transport: TLS-verify against the ORIGINAL hostname, fail closed.

``app.services.pinned_transport`` connects to the RESOLVED address of the
configured base URL but must still prove WHICH host it is talking to: the TLS
servername / SNI and the certificate-chain hostname check both use the ORIGINAL
URL hostname, never the bare pinned IP.

These tests pin a real TLS server (self-signed cert for ``localhost``) and drive
the transport against it, so the mismatch / unresolvable-host behaviour is proven
over a genuine TLS handshake rather than with a mock.
"""
import socket
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from app.services.pinned_transport import pinned_client, pin_base_url


def _fake_resolve_127(host, port, type=None, proto=0, flags=0):
    """Map any hostname to the loopback address — the pinned-IP scenario."""
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port or 80))]


@pytest.fixture(scope="module")
def tls_server(tmp_path_factory):
    """A real TLS server whose certificate is for ``localhost``.

    Yields ``(port, server, verify_ctx)``.
    """
    d = tmp_path_factory.mktemp("pinned_tls")
    cert, key = d / "server.pem", d / "server.key"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
         "-keyout", str(key), "-out", str(cert), "-subj", "/CN=localhost",
         "-addext", "subjectAltName=DNS:localhost"],
        check=True, capture_output=True, timeout=120,
    )
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(cert), str(key))
    verify_ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    verify_ctx.load_verify_locations(str(cert))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'{"models": []}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1], server, verify_ctx
    server.shutdown()
    server.server_close()
    thread.join(timeout=10)


def test_pinned_connection_with_matching_hostname_succeeds(tls_server):
    """Positive control: the pinned transport DOES verify — against the original
    hostname — when the certificate matches. Without this the refusal test below
    could pass vacuously."""
    port, _server, verify_ctx = tls_server
    with pinned_client(f"https://localhost:{port}", timeout=10, verify=verify_ctx) as client:
        r = client.get("/api/tags")
        r.raise_for_status()
        assert r.json()["models"] == []


def test_pinned_connection_to_ip_with_foreign_cert_is_refused(tls_server, monkeypatch):
    """A pinned connection whose certificate is for a DIFFERENT hostname must be
    refused (fail closed) — TLS-verify uses the ORIGINAL host, never the pinned IP."""
    port, _server, verify_ctx = tls_server
    monkeypatch.setattr(socket, "getaddrinfo", _fake_resolve_127)
    # The configured host resolves (via our fake resolver) to the pinned IP, but
    # the TLS cert on that IP is for `localhost` — a different hostname.
    with pytest.raises(httpx.ConnectError) as excinfo:
        with pinned_client(f"https://edibl-pinned.example:{port}", timeout=10, verify=verify_ctx) as client:
            client.get("/api/tags")
    # Fail CLOSED: the refusal is a TLS certificate-verification failure …
    assert "SSL" in str(excinfo.value) or "CERTIFICATE" in str(excinfo.value).upper()


def test_unresolvable_host_fails_closed():
    """An unresolvable host must never yield a silent permit — fail closed at connect."""
    with pytest.raises(ValueError):
        pinned_client("http://edibl-unresolvable.invalid:8080", timeout=5)
