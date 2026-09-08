import asyncio
import ipaddress
import ssl
import threading
import time
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import certifi
import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app.runtime import http_tls
from app.runtime.http_tls import create_runtime_ssl_context


def test_runtime_context_requires_certificate_and_hostname_verification():
    context = asyncio.run(create_runtime_ssl_context())

    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_runtime_context_retains_certifi_root_set():
    context = asyncio.run(create_runtime_ssl_context())
    expected = ssl.create_default_context(cafile=certifi.where())

    assert set(context.get_ca_certs(binary_form=True)) == set(
        expected.get_ca_certs(binary_form=True)
    )


def test_runtime_context_is_isolated_per_call():
    first = asyncio.run(create_runtime_ssl_context())
    second = asyncio.run(create_runtime_ssl_context())

    first.minimum_version = ssl.TLSVersion.TLSv1_3

    assert first is not second
    assert second.minimum_version != ssl.TLSVersion.TLSv1_3


def test_runtime_context_initialization_failure_is_sanitized(monkeypatch):
    def fail_to_build():
        raise OSError("C:/customer/private/ca.pem")

    monkeypatch.setattr(http_tls, "_build_runtime_ssl_context", fail_to_build)

    with pytest.raises(
        httpx.ConnectError, match="^Unable to initialize TLS verification$"
    ) as captured:
        asyncio.run(create_runtime_ssl_context())

    assert "customer" not in str(captured.value)


def test_tls_initialization_does_not_delay_asyncio_run_shutdown(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def blocked_build():
        started.set()
        assert release.wait(3)
        return ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    monkeypatch.setattr(http_tls, "_build_runtime_ssl_context", blocked_build)

    async def attempt():
        async with asyncio.timeout(0.05):
            await create_runtime_ssl_context()

    before = time.monotonic()
    try:
        with pytest.raises(TimeoutError):
            asyncio.run(attempt())
        assert started.is_set()
        assert time.monotonic() - before < 0.5
    finally:
        release.set()


def test_runtime_context_preserves_initialization_timeout(monkeypatch):
    def timeout_during_build():
        raise TimeoutError("initialization timed out")

    monkeypatch.setattr(http_tls, "_build_runtime_ssl_context", timeout_during_build)
    with pytest.raises(TimeoutError, match="initialization timed out"):
        asyncio.run(create_runtime_ssl_context())


@pytest.mark.parametrize(
    ("trusted_server", "server_host", "should_connect"),
    [
        (True, "127.0.0.1", True),
        (False, "127.0.0.1", False),
        (True, "localhost", False),
    ],
    ids=["trusted-ca", "untrusted-ca", "wrong-hostname"],
)
def test_runtime_context_verifies_local_tls_peer(
    tmp_path, monkeypatch, trusted_server, server_host, should_connect
):
    trusted_ca_key, trusted_ca = _create_ca("trusted runtime test CA")
    untrusted_ca_key, untrusted_ca = _create_ca("untrusted runtime test CA")
    issuer_key = trusted_ca_key if trusted_server else untrusted_ca_key
    issuer = trusted_ca if trusted_server else untrusted_ca
    server_key, server_certificate = _create_server_certificate(
        issuer_key, issuer
    )
    ca_path = tmp_path / "trusted-ca.pem"
    certificate_path = tmp_path / "server.pem"
    key_path = tmp_path / "server-key.pem"
    ca_path.write_bytes(trusted_ca.public_bytes(serialization.Encoding.PEM))
    certificate_path.write_bytes(
        server_certificate.public_bytes(serialization.Encoding.PEM)
    )
    key_path.write_bytes(
        server_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    monkeypatch.setattr(http_tls.certifi, "where", lambda: str(ca_path))

    server, thread = _start_tls_server(certificate_path, key_path)

    async def request():
        context = await create_runtime_ssl_context()
        async with httpx.AsyncClient(verify=context, trust_env=False) as client:
            return await client.get(
                f"https://{server_host}:{server.server_port}/"
            )

    try:
        if should_connect:
            response = asyncio.run(request())
            assert response.status_code == 200
            assert response.json() == {"status": "ok"}
        else:
            with pytest.raises(httpx.ConnectError):
                asyncio.run(request())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _create_ca(common_name):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
        .sign(key, hashes.SHA256())
    )
    return key, certificate


def _create_server_certificate(ca_key, ca_certificate):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "runtime test server")]
    )
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_certificate.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            False,
        )
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
        .sign(ca_key, hashes.SHA256())
    )
    return key, certificate


def _start_tls_server(certificate_path, key_path):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'{"status":"ok"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *_args):
            return

    class TestServer(ThreadingHTTPServer):
        daemon_threads = True

    server = TestServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate_path, key_path)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread
