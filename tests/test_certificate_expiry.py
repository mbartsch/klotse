from datetime import datetime, timezone

from klotse.k8s.resources import custom_columns, expiry_text, map_custom_object

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)

CERT = {
    "metadata": {"name": "web-tls", "namespace": "shop", "creationTimestamp": "2026-01-01T00:00:00Z"},
    "spec": {"secretName": "web-tls-secret", "issuerRef": {"name": "letsencrypt", "kind": "ClusterIssuer"},
             "dnsNames": ["a.example.com", "b.example.com"]},
    "status": {"notAfter": "2027-01-01T00:00:00Z", "renewalTime": "2026-12-02T00:00:00Z",
               "conditions": [{"type": "Ready", "status": "True"}]}}


def test_expiry_text_gives_the_date_first_and_the_distance():
    assert expiry_text("2026-12-01T10:00:00Z", NOW) == "2026-12-01 10:00 (in 23d)"
    assert expiry_text("2026-11-07T20:00:00Z", NOW) == "2026-11-07 20:00 (in under a day)"
    assert expiry_text("2026-11-07T06:00:00Z", NOW) == "2026-11-07 06:00 (expired today)"
    assert expiry_text("2026-11-01T12:00:00Z", NOW) == "2026-11-01 12:00 (expired 6d ago)"
    assert expiry_text(None, NOW) == "" and expiry_text("not a date", NOW) == ""


def test_expiry_texts_sort_by_date():
    texts = [expiry_text(s, NOW) for s in ("2027-01-01T00:00:00Z", "2026-11-20T00:00:00Z", "2026-11-01T00:00:00Z")]
    assert sorted(texts) == [texts[2], texts[1], texts[0]]


def test_a_certificate_row_shows_expiry_secret_and_issuer():
    row = map_custom_object(CERT, group="cert-manager.io", kind="Certificate")
    assert row.secret == "web-tls-secret" and row.issuer == "letsencrypt (ClusterIssuer)"
    assert row.dns == "a.example.com, b.example.com" and row.ready == "✔ Ready"
    assert row.not_after.startswith("2027-01-01 00:00 (") and row.renewal.startswith("2026-12-02 00:00 (")
    assert [h for h, _ in custom_columns("cert-manager.io", "Certificate")][:4] == ["Name", "Namespace", "Ready", "Expires"]


def test_a_bare_certificate_does_not_break_the_list():
    row = map_custom_object({"metadata": {"name": "x", "namespace": "n"}}, group="cert-manager.io", kind="Certificate")
    assert row.name == "x" and row.not_after == "" and row.ready == ""
