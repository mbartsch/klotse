"""Just enough of an X.509 certificate to show when it expires and who it is for (pure Python, no third-party library).

Only the public certificate is read (`tls.crt`), never a private key. Anything unexpected gives None: this is a convenience for a
list column, not a validator, so it never claims a certificate is valid, only reports the dates it carries."""
import base64
import re
from datetime import datetime, timezone

MAX_DER = 64 * 1024
PEM = re.compile(rb"-----BEGIN CERTIFICATE-----(.+?)-----END CERTIFICATE-----", re.S)
OID_CN, OID_SAN = b"\x55\x04\x03", b"\x55\x1d\x11"


def _tlv(buf, pos):
    """(tag, content start, content end) of the element at `pos`; ValueError when it runs past the buffer."""
    if pos + 2 > len(buf):
        raise ValueError("truncated")
    tag, length = buf[pos], buf[pos + 1]
    pos += 2
    if length & 0x80:
        count = length & 0x7F
        if count == 0 or count > 4 or pos + count > len(buf):
            raise ValueError("bad length")
        length = int.from_bytes(buf[pos:pos + count], "big")
        pos += count
    if pos + length > len(buf):
        raise ValueError("truncated")
    return tag, pos, pos + length


def _children(buf, start, end):
    pos = start
    while pos < end:
        tag, a, b = _tlv(buf, pos)
        yield tag, a, b
        pos = b


def _time(buf, tag, a, b):
    text = buf[a:b].decode("ascii", "replace")
    if tag == 0x17 and re.fullmatch(r"\d{12}Z", text):
        year = int(text[:2])
        return datetime(2000 + year if year < 50 else 1900 + year, int(text[2:4]), int(text[4:6]), int(text[6:8]), int(text[8:10]),
                        int(text[10:12]), tzinfo=timezone.utc)
    if tag == 0x18 and re.fullmatch(r"\d{14}Z", text):
        return datetime(int(text[:4]), int(text[4:6]), int(text[6:8]), int(text[8:10]), int(text[10:12]), int(text[12:14]),
                        tzinfo=timezone.utc)
    raise ValueError("unknown time format")


def _common_name(buf, a, b):
    for _, sa, sb in _children(buf, a, b):  # each RDN is a SET
        for _, pa, pb in _children(buf, sa, sb):  # of SEQUENCE { OID, value }
            parts = list(_children(buf, pa, pb))
            if len(parts) == 2 and buf[parts[0][1]:parts[0][2]] == OID_CN:
                return buf[parts[1][1]:parts[1][2]].decode("utf-8", "replace")
    return ""


def _alt_names(buf, a, b):
    names = []
    for _, ea, eb in _children(buf, a, b):  # [3] holds a SEQUENCE of extensions
        for _, xa, xb in _children(buf, ea, eb):  # each extension: SEQUENCE { OID, [critical], OCTET STRING }
            parts = list(_children(buf, xa, xb))
            if parts and buf[parts[0][1]:parts[0][2]] == OID_SAN:
                _, oa, ob = parts[-1]
                _, sa, sb = _tlv(buf, oa)  # the OCTET STRING holds a SEQUENCE of GeneralName
                names += [buf[na:nb].decode("ascii", "replace") for tag, na, nb in _children(buf, sa, sb) if tag == 0x82]
    return names


def parse_certificate(der):
    """{"not_before", "not_after" (aware datetimes), "subject" (common name), "issuer", "names" (DNS subject alternative names)}
    for a DER certificate, or None when it cannot be read."""
    try:
        if not der or len(der) > MAX_DER:
            return None
        _, a, b = _tlv(der, 0)
        _, ta, tb = _tlv(der, a)  # tbsCertificate
        items = list(_children(der, ta, tb))
        if items and items[0][0] == 0xA0:  # the optional version
            items = items[1:]
        # serial, signature, issuer, validity, subject, subjectPublicKeyInfo, then optional fields
        if len(items) < 6:
            return None
        issuer, validity, subject = items[2], items[3], items[4]
        dates = list(_children(der, validity[1], validity[2]))
        if len(dates) != 2:
            return None
        extensions = next((i for i in items[6:] if i[0] == 0xA3), None)
        return {"not_before": _time(der, *dates[0]), "not_after": _time(der, *dates[1]),
                "subject": _common_name(der, subject[1], subject[2]), "issuer": _common_name(der, issuer[1], issuer[2]),
                "names": _alt_names(der, extensions[1], extensions[2]) if extensions else []}
    except (ValueError, IndexError):
        return None


def first_certificate(pem_or_bytes):
    """The first certificate of a PEM bundle (the leaf in a normal tls.crt), parsed; None when there is none."""
    data = pem_or_bytes if isinstance(pem_or_bytes, (bytes, bytearray)) else str(pem_or_bytes).encode()
    found = PEM.search(bytes(data))
    if not found:
        return None
    try:
        return parse_certificate(base64.b64decode(b"".join(found.group(1).split()), validate=True))
    except ValueError:
        return None
