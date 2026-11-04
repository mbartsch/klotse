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
    head = data if isinstance(data, bytes) else str(data).encode("utf-8", "replace")
    if re.search(rb"<!(?:DOCTYPE|ENTITY)", head, re.IGNORECASE):
        raise ET.ParseError("unexpected DTD in the answer")
    return ET.fromstring(data)  # nosec B314 - checked above
