"""On-demand EC2 prices.

AWS publishes the full price list as one ~480 MB file per region, far too much to download from a desktop app. The
AWS pricing calculator reads a small per-region, per-operating-system file instead (about 60 KB compressed) and this
module uses that: it is public, needs no credentials and has the same prices. It is not a documented API, so a change
on AWS's side can break it; the plugin then says so and keeps using the last good copy, kept for a week in the
plugin's cache folder. AWS China uses the documented Price List Query API and AWS credentials.
"""
import gzip
import json
import time
import urllib.parse
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .awscreds import CredentialSource, CredentialsError
from .safe import require_https
from .sigv4 import sign

BASE_URL = "https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/ec2/USD/current/ec2-ondemand-without-sec-sel"
CHINA_PRICE_API_URL = "https://api.pricing.cn-northwest-1.amazonaws.com.cn/"
CACHE_MAX_AGE_S = 7 * 86400
MAX_DOWNLOAD_BYTES = 30 * 1024 * 1024  # the real files are about 1 MB uncompressed; refuse anything absurd
HOURS_PER_MONTH = 730  # what AWS uses for monthly estimates

# The names the price files use for each region (checked against the live files; the order is AWS's, not ours).
REGION_NAMES = {
    "us-east-1": "US East (N. Virginia)", "us-east-2": "US East (Ohio)", "us-west-1": "US West (N. California)",
    "us-west-2": "US West (Oregon)", "af-south-1": "Africa (Cape Town)", "ap-east-1": "Asia Pacific (Hong Kong)",
    "ap-south-1": "Asia Pacific (Mumbai)", "ap-south-2": "Asia Pacific (Hyderabad)",
    "ap-southeast-1": "Asia Pacific (Singapore)", "ap-southeast-2": "Asia Pacific (Sydney)",
    "ap-southeast-3": "Asia Pacific (Jakarta)", "ap-southeast-4": "Asia Pacific (Melbourne)",
    "ap-southeast-5": "Asia Pacific (Malaysia)", "ap-southeast-7": "Asia Pacific (Thailand)",
    "ap-northeast-1": "Asia Pacific (Tokyo)", "ap-northeast-2": "Asia Pacific (Seoul)",
    "ap-northeast-3": "Asia Pacific (Osaka)", "ca-central-1": "Canada (Central)", "ca-west-1": "Canada West (Calgary)",
    "eu-central-1": "EU (Frankfurt)", "eu-central-2": "EU (Zurich)", "eu-west-1": "EU (Ireland)",
    "eu-west-2": "EU (London)", "eu-west-3": "EU (Paris)", "eu-north-1": "EU (Stockholm)", "eu-south-1": "EU (Milan)",
    "eu-south-2": "EU (Spain)", "il-central-1": "Israel (Tel Aviv)", "me-south-1": "Middle East (Bahrain)",
    "me-central-1": "Middle East (UAE)", "mx-central-1": "Mexico (Central)", "sa-east-1": "South America (Sao Paulo)",
    "us-gov-east-1": "AWS GovCloud (US-East)", "us-gov-west-1": "AWS GovCloud (US)",
    "cn-north-1": "China (Beijing)", "cn-northwest-1": "China (Ningxia)",
}


class PriceError(Exception):
    """The prices for a region could not be had."""


@dataclass
class PriceTable:
    prices: dict  # instance type -> per-hour price in currency
    note: str = ""  # set when the table is older than it should be, and why
    currency: str = "USD"


class Price(float):
    """A sortable numeric price whose currency stays available to the table formatter."""

    def __new__(cls, value, currency="USD"):
        result = super().__new__(cls, value)
        result.currency = currency
        return result


def currency_for_region(region):
    return "CNY" if str(region).startswith("cn-") else "USD"


def price_url(region, os_name):
    name = REGION_NAMES.get(region)
    if name is None:
        raise PriceError(f"no price list is known for the region {region or '(unknown)'}")
    if region.startswith("cn-"):
        return CHINA_PRICE_API_URL
    return f"{BASE_URL}/{urllib.parse.quote(name)}/{os_name}/index.json"


def parse_price_file(raw):
    """{instance type: USD per hour} from a calculator price file (gzip or plain JSON)."""
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    prices = {}
    for entries in (json.loads(raw).get("regions") or {}).values():
        for entry in entries.values():
            if entry.get("Pre Installed S/W", "NA") != "NA" or not entry.get("Instance Type"):
                continue  # SQL Server and the like cost extra: only the plain operating system
            try:
                prices[entry["Instance Type"]] = float(entry["price"])
            except (KeyError, TypeError, ValueError):
                continue
    return prices


def _query_china_prices(region, os_name, profile="", credentials=None, send=None, now=time.time):
    """Query the authenticated China Price List API, paging its results instead of downloading the 161 MB offer file."""
    if credentials is None:
        try:
            credentials = CredentialSource(profile).get()
        except CredentialsError as error:
            raise PriceError(f"China prices need AWS credentials with pricing:GetProducts permission: {error}") from None
    if send is None:
        send = _send_price_query
    filters = [
        {"Type": "TERM_MATCH", "Field": "regionCode", "Value": region},
        {"Type": "TERM_MATCH", "Field": "operatingSystem", "Value": os_name},
        {"Type": "TERM_MATCH", "Field": "tenancy", "Value": "Shared"},
        {"Type": "TERM_MATCH", "Field": "preInstalledSw", "Value": "NA"},
        {"Type": "TERM_MATCH", "Field": "licenseModel", "Value": "No License required"},
        {"Type": "TERM_MATCH", "Field": "productFamily", "Value": "Compute Instance"},
    ]
    token, prices = None, {}
    while True:
        payload = {"ServiceCode": "AmazonEC2", "Filters": filters, "FormatVersion": "aws_v1", "MaxResults": 100}
        if token:
            payload["NextToken"] = token
        response = send(payload, credentials, now())
        try:
            results = response.get("PriceList", [])
            for item in results:
                item = json.loads(item) if isinstance(item, str) else item
                product = item.get("product") or {}
                attrs = product.get("attributes") or {}
                instance_type = attrs.get("instanceType")
                if (product.get("productFamily") != "Compute Instance" or not instance_type
                        or attrs.get("regionCode") != region or attrs.get("operatingSystem") != os_name
                        or attrs.get("tenancy") != "Shared" or attrs.get("preInstalledSw", "NA") not in ("NA", "None")
                        or attrs.get("licenseModel") not in ("No License required", "NA", None)):
                    continue
                for offer in ((item.get("terms") or {}).get("OnDemand") or {}).values():
                    for dimension in (offer.get("priceDimensions") or {}).values():
                        if dimension.get("unit") not in ("Hrs", "Hours") or dimension.get("startingRange", "0") != "0":
                            continue
                        amount = (dimension.get("pricePerUnit") or {}).get("CNY")
                        if amount is not None:
                            prices[instance_type] = float(amount)
            token = response.get("NextToken")
        except (AttributeError, TypeError, ValueError) as error:
            raise PriceError(f"AWS returned an unreadable China price response: {type(error).__name__}") from None
        if not token:
            return prices


def _send_price_query(payload, credentials, timestamp):
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    headers = {"Content-Type": "application/x-amz-json-1.1", "X-Amz-Target": "AWSPriceListService.GetProducts"}
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(timestamp))
    headers = sign("POST", CHINA_PRICE_API_URL, headers, body, credentials.access_key, credentials.secret_key,
                   "cn-northwest-1", "pricing", stamp, credentials.token or None)
    request = urllib.request.Request(require_https(CHINA_PRICE_API_URL), data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # nosec B310 - fixed HTTPS AWS endpoint
            return json.loads(response.read(5_000_000))
    except urllib.error.HTTPError as error:
        try:
            detail = json.loads(error.read(64_000)).get("message", "")
        except (ValueError, AttributeError):
            detail = ""
        reason = f"{error.code} {detail}".strip()
        raise PriceError(f"China Price List API refused the request ({reason})") from None
    except (OSError, ValueError) as error:
        raise PriceError(f"China Price List API request failed: {type(error).__name__}") from None


def download(url, timeout=30):
    request = urllib.request.Request(require_https(url), headers={"User-Agent": "Klotse-aws", "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310 - https only, checked above
        data = response.read(MAX_DOWNLOAD_BYTES + 1)
    if len(data) > MAX_DOWNLOAD_BYTES:
        raise PriceError("the price file is unexpectedly large")
    return data


def on_demand_prices(region, os_name, cache_dir, fetch=download, now=time.time, profile="", query_china=None):
    """The price table for a region and operating system: from the cache when it is under a week old, else downloaded.

    A failed download falls back to an older cached copy (the table's `note` says so); with no copy it raises PriceError.
    """
    url = price_url(region, os_name)
    cache_key = f"ondemand-{region}-{os_name}-query" if region.startswith("cn-") else f"ondemand-{region}-{os_name}"
    path = Path(cache_dir) / f"{cache_key}.json" if cache_dir else None
    cached = None
    if path is not None and path.is_file():
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            cached = cached if isinstance(cached.get("prices"), dict) else None
        except (OSError, ValueError, AttributeError):
            cached = None
    age = now() - cached["fetched"] if cached else None
    currency = currency_for_region(region)
    if cached and 0 <= age < CACHE_MAX_AGE_S:
        return PriceTable(cached["prices"], currency=currency)
    try:
        if region.startswith("cn-"):
            prices = (query_china or _query_china_prices)(region, os_name, profile=profile, now=now)
        else:
            prices = parse_price_file(fetch(url))
        if not prices:
            raise PriceError("the price file had no prices in it")
    except (OSError, ValueError, PriceError) as error:  # urllib errors are OSErrors; bad JSON is a ValueError
        if cached:
            return PriceTable(cached["prices"], f"using prices from {int(age // 86400)} days ago: {error}", currency)
        raise PriceError(f"could not download the prices for {region}: {error}") from error
    if path is not None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"fetched": now(), "prices": prices}), encoding="utf-8")
        except OSError:
            pass  # a cache that cannot be written only costs a download next time
    return PriceTable(prices, currency=currency)
