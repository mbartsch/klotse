"""Checks on data that comes from outside before it is used: node labels (a cluster's administrators write them), URLs and
XML answers. Found by the static security checks (bandit) and a review of what reaches an AWS request."""
import re
import xml.etree.ElementTree as ET  # nosec B405 - parse_xml below is the only entry, and it refuses DTDs and entities

ParseError = ET.ParseError  # what parse_xml raises, for callers

# "us-east-1", "eu-west-2", "us-gov-west-1", "cn-north-1", "ap-southeast-4": lower-case words and a number, nothing that can
# change a host name. The region comes from a node label, which whoever runs the cluster can set to anything; it is put in
# the host name of a signed AWS request (the session token travels in its headers), so it must be checked first.
REGION = re.compile(r"[a-z]{2}(?:-[a-z]+)+-\d{1,2}")


def valid_region(region):
    return isinstance(region, str) and len(region) <= 30 and REGION.fullmatch(region) is not None


def require_https(url):
    """The URL unchanged when it is https, else ValueError (urllib would also open file: and ftp: addresses)."""
    if not str(url).lower().startswith("https://"):
        raise ValueError("only https addresses are used")
    return url


def parse_xml(data):
    """Parse an XML answer from AWS. AWS never sends a DTD or entity declarations, so any is refused instead of parsed
    (entity expansion is the way a hostile XML document exhausts memory)."""
    if isinstance(data, bytes):
        # AWS answers UTF-8, but XML parsers also accept UTF-16/32. Normalize those
        # encodings before scanning so declarations cannot hide behind NUL bytes.
        if data.startswith(b"\xff\xfe\x00\x00") or data.startswith(b"<\x00\x00\x00"):
            head = data.decode("utf-32-le" if not data.startswith(b"\xff\xfe\x00\x00") else "utf-32")
        elif data.startswith(b"\x00\x00\xfe\xff") or data.startswith(b"\x00\x00\x00<"):
            head = data.decode("utf-32-be" if not data.startswith(b"\x00\x00\xfe\xff") else "utf-32")
        elif data.startswith(b"\xff\xfe") or data.startswith(b"<\x00"):
            head = data.decode("utf-16-le" if not data.startswith(b"\xff\xfe") else "utf-16")
        elif data.startswith(b"\xfe\xff") or data.startswith(b"\x00<"):
            head = data.decode("utf-16-be" if not data.startswith(b"\xfe\xff") else "utf-16")
        else:
            head = data.decode("utf-8-sig", "replace")
    else:
        head = str(data)
    if re.search(r"<!(?:DOCTYPE|ENTITY)", head, re.IGNORECASE):
        raise ET.ParseError("unexpected DTD in the answer")
    return ET.fromstring(data)  # nosec B314 - checked above
