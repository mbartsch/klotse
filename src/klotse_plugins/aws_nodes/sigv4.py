"""AWS Signature Version 4 (https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_sigv-create-signed-request.html),
just enough to call the EC2 query API without boto3: a request with a form body and no query string."""
import hashlib
import hmac
import urllib.parse

ALGORITHM = "AWS4-HMAC-SHA256"


def _hmac(key, text):
    return hmac.new(key, text.encode("utf-8"), hashlib.sha256).digest()


def sha256_hex(data):
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode("utf-8")).hexdigest()


def signing_key(secret_key, date, region, service):
    key = _hmac(("AWS4" + secret_key).encode("utf-8"), date)
    for part in (region, service, "aws4_request"):
        key = _hmac(key, part)
    return key


def sign(method, url, headers, payload, access_key, secret_key, region, service, amz_date, token=None):
    """The headers to send: `headers` plus X-Amz-Date, X-Amz-Security-Token (when there is a token) and Authorization.

    `headers` must include everything that is sent and should be signed (Content-Type, ...); Host is taken from the url.
    `amz_date` is "YYYYMMDDTHHMMSSZ" in UTC."""
    parts = urllib.parse.urlsplit(url)
    signed = {k.lower(): " ".join(str(v).split()) for k, v in headers.items()}
    signed["host"], signed["x-amz-date"] = parts.netloc, amz_date
    if token:
        signed["x-amz-security-token"] = token
    names = sorted(signed)
    canonical_headers = "".join(f"{name}:{signed[name]}\n" for name in names)
    signed_headers = ";".join(names)
    query = "&".join(f"{urllib.parse.quote(k, safe='-_.~')}={urllib.parse.quote(v, safe='-_.~')}"
                     for k, v in sorted(urllib.parse.parse_qsl(parts.query, keep_blank_values=True)))
    canonical_request = "\n".join([method.upper(), urllib.parse.quote(parts.path or "/", safe="/-_.~"), query,
                                   canonical_headers, signed_headers, sha256_hex(payload)])
    date = amz_date[:8]
    scope = f"{date}/{region}/{service}/aws4_request"
    to_sign = "\n".join([ALGORITHM, amz_date, scope, sha256_hex(canonical_request)])
    signature = hmac.new(signing_key(secret_key, date, region, service), to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    result = dict(headers)
    result["X-Amz-Date"] = amz_date
    if token:
        result["X-Amz-Security-Token"] = token
    result["Authorization"] = (f"{ALGORITHM} Credential={access_key}/{scope}, SignedHeaders={signed_headers}, "
                               f"Signature={signature}")
    return result
