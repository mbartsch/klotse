"""Synthetic loopback attack servers; no persistent keys, external endpoints or disabled TLS verification."""
import datetime
import http.server
import ipaddress
import socket
import ssl
import threading
import urllib.request
from contextlib import ExitStack

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from klotse.k8s import process_env


@pytest.fixture
def attack_servers(tmp_path, monkeypatch):
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)
    previous_proxy, previous_bypass = process_env.app_proxy()
    process_env.set_app_proxy(None)
    stack = ExitStack()
    original_connect = socket.socket.connect

    def loopback_only(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            assert ipaddress.ip_address(address[0]).is_loopback, "security tests forbid external network connections"
        return original_connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", loopback_only)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Klotse synthetic test CA")])
    now = datetime.datetime.now(datetime.timezone.utc)
    ca = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=1))
          .not_valid_after(now + datetime.timedelta(days=1)).add_extension(x509.BasicConstraints(ca=True, path_length=0), True)
          .sign(key, hashes.SHA256()))
    leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    leaf = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
            .issuer_name(subject).public_key(leaf_key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=1)).not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"),
                                                       x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False)
            .sign(key, hashes.SHA256()))
    cert, private = tmp_path / "server.pem", tmp_path / "server-key.pem"
    cert.write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    private.write_bytes(leaf_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                               serialization.NoEncryption()))
    private.chmod(0o600)
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(cert, private)
    client_context = ssl.create_default_context(cadata=ca.public_bytes(serialization.Encoding.PEM).decode())
    original_opener = urllib.request.build_opener
    trusted = [True]

    def opener(*handlers):
        return original_opener(*handlers, urllib.request.HTTPSHandler(context=client_context if trusted[0] else None))

    monkeypatch.setattr(urllib.request, "build_opener", opener)

    def start(tls=False):
        routes, seen, release = {}, [], threading.Event()

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - stdlib API
                seen.append((self.path, dict(self.headers)))
                status, headers, body = routes.get(self.path, (200, {}, b'{"status":"success","data":{"result":[]}}'))
                if status == "malformed":
                    self.connection.sendall(b"NOT HTTP\r\n\r\n")
                    self.close_connection = True
                    return
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if headers.get("X-Stall"):
                    release.wait(3)
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError, ssl.SSLError):
                    pass  # the tested client may correctly reject or time out

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        if tls:
            server.socket = server_context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.02), daemon=True)
        thread.start()

        def close():
            release.set()
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
            assert not thread.is_alive()

        stack.callback(close)
        return f"{'https' if tls else 'http'}://127.0.0.1:{server.server_port}", routes, seen

    start.trusted = trusted
    try:
        yield start
    finally:
        stack.close()
        process_env.set_app_proxy(previous_proxy, previous_bypass)
