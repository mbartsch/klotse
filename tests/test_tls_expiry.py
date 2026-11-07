import base64
from types import SimpleNamespace

from klotse.k8s.resources import SPEC_BY_KEY, map_secret, tls_summary
from klotse.k8s.x509 import first_certificate, parse_certificate

# A throw-away self-signed certificate (public part only), made with: openssl req -x509 -days 90 -subj /CN=shop.example.com
# -addext subjectAltName=DNS:shop.example.com,DNS:api.example.com,IP:10.0.0.1. openssl reads it as notBefore=Nov  7 06:46:18 2026 GMT,
# notAfter=Feb  5 06:46:18 2027 GMT.
PEM = """-----BEGIN CERTIFICATE-----
MIIDTTCCAjWgAwIBAgIUPEi9ScykJaGA/wwdOhoq9CaeEB4wDQYJKoZIhvcNAQEL
BQAwGzEZMBcGA1UEAwwQc2hvcC5leGFtcGxlLmNvbTAeFw0yNjExMDcwNjQ2MTha
Fw0yNzAyMDUwNjQ2MThaMBsxGTAXBgNVBAMMEHNob3AuZXhhbXBsZS5jb20wggEi
MA0GCSqGSIb3DQEBAQUAA4IBDwAwggEKAoIBAQDipKTZtog4JPw831X76zL6y8/O
MO9/0PI1UqfJB1BtN1UeSBQPKcmumUhZADCX+qv95yYwUBrNIh4MAzPKItQ2KeNR
/Ic9my2mP73Z4FsJh3yS4jw7JpnAXXQtJ1dt2KKmU9mawrC57vPh8G3BzlUAODHN
pLPxSiudXdNNJvGQ0qRX2JBrC052JLo+5eWOXcvZuZ/aTTAkQYoaBCVTmhleuiDq
Fxlo8nBwV9iYIo1m3lL1ZNxMq4vzjoG+lVp0vEaVcUVMYNw4YFOhihrM+Lo2dwQM
uUi78zUF7fTC6OCvy97XXg6pfWYffbtT6z4NNOizWUl4kR53pR/N/0iQXeV1AgMB
AAGjgYgwgYUwHQYDVR0OBBYEFEsRuB6ILml/6xB1ZhcaWDL85dqwMB8GA1UdIwQY
MBaAFEsRuB6ILml/6xB1ZhcaWDL85dqwMA8GA1UdEwEB/wQFMAMBAf8wMgYDVR0R
BCswKYIQc2hvcC5leGFtcGxlLmNvbYIPYXBpLmV4YW1wbGUuY29thwQKAAABMA0G
CSqGSIb3DQEBCwUAA4IBAQB5VGE8JMGTpEEIHLOQ/md5kIZm3JTGlVrL5ByfVyKp
1TdOuTU41yic9YbprkDZRQU+entuVs8k3wS1T9Hna38MNPv5aL0/uOx4rcr/Xux2
uhaePToUB8o7+iamDERhY3pMIS/5/G32WzIUZnELvobScGgqo2b4hgYUd0vy11CR
yRY2uNGgicFerTfnQKakkV9xx1X1+soRgx5cknBzooiLizzyGLNxOJIhOHOR9Too
czLNr1QuVPFoDnrNgf2PWA73n57oouey5P0NbtrhH/LDSneV5weR1DLKqMkQ3iPg
UMSspLMG/GoheAezOzv63HvTU+quZR2ylDJj4wOr4qLA
-----END CERTIFICATE-----
"""


def test_dates_names_and_issuer_match_what_openssl_reads():
    cert = first_certificate(PEM)
    assert cert["not_before"].isoformat() == "2026-11-07T06:46:18+00:00"
    assert cert["not_after"].isoformat() == "2027-02-05T06:46:18+00:00"
    assert cert["subject"] == "shop.example.com" and cert["issuer"] == "shop.example.com"
    assert cert["names"] == ["shop.example.com", "api.example.com"]  # the IP entry is not a DNS name


def test_the_first_certificate_of_a_bundle_is_used():
    assert first_certificate(PEM + PEM)["subject"] == "shop.example.com"


def test_damaged_input_gives_none_and_never_raises():
    assert first_certificate("") is None and first_certificate("not a certificate") is None
    assert first_certificate("-----BEGIN CERTIFICATE-----\n!!!!\n-----END CERTIFICATE-----") is None
    der = base64.b64decode("".join(PEM.splitlines()[1:-1]))
    for broken in (der[:40], b"\x30\x84\xff\xff\xff\xff", b"", der + b"\x00" * 70000):  # cut short, absurd length, empty, huge
        assert parse_certificate(broken) is None
    parse_certificate(der[:-30] + b"x" * 30)  # a damaged signature gives a result or None, but never raises


def secret(kind, data):
    meta = SimpleNamespace(name="web-tls", namespace="shop", uid="u", creation_timestamp=None, labels={}, annotations={})
    obj = SimpleNamespace(metadata=meta, type=kind, data=data, string_data=None, to_dict=lambda: {})
    return obj


def test_tls_summary_text():
    encoded = base64.b64encode(PEM.encode()).decode()
    expires, subject = tls_summary(encoded)
    assert expires.startswith("2027-02-05 06:46 (") and subject == "shop.example.com, api.example.com"
    assert tls_summary(None) == ("", "") and tls_summary("!!!") == ("", "")


def test_secrets_list_has_the_columns_and_only_tls_secrets_fill_them():
    assert [h for h, _ in SPEC_BY_KEY["secrets"].columns][-4:-1] == ["Keys", "Expires", "Subject"]
    from klotse.k8s import resources
    encoded = base64.b64encode(PEM.encode()).decode()
    real = resources.to_dict
    resources.to_dict = lambda o: {"metadata": {"name": "web-tls", "namespace": "shop"}, "data": dict(o.data)}
    try:
        tls = map_secret(secret("kubernetes.io/tls", {"tls.crt": encoded, "tls.key": "a2V5"}))
        other = map_secret(secret("Opaque", {"tls.crt": encoded}))
    finally:
        resources.to_dict = real
    assert tls.expires.startswith("2027-02-05") and tls.subject == "shop.example.com, api.example.com"
    assert other.expires == "" and other.subject == ""
    assert tls.raw["data"] == {"tls.crt": "<hidden>", "tls.key": "<hidden>"}  # the manifest stays masked
