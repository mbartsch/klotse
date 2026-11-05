"""On-demand EC2 prices without an AWS login.

AWS publishes the full price list as one ~480 MB file per region, far too much to download from a desktop app. The
AWS pricing calculator reads a small per-region, per-operating-system file instead (about 60 KB compressed) and this
module uses that: it is public, needs no credentials and has the same prices. It is not a documented API, so a change
on AWS's side can break it; the plugin then says so and keeps using the last good copy, kept for a week in the
plugin's cache folder.
"""
import gzip
import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .safe import require_https

BASE_URL = "https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/ec2/USD/current/ec2-ondemand-without-sec-sel"
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
}


class PriceError(Exception):
    """The prices for a region could not be had."""


@dataclass
class PriceTable:
    prices: dict  # instance type -> USD per hour
    note: str = ""  # set when the table is older than it should be, and why


def price_url(region, os_name):
    name = REGION_NAMES.get(region)
    if name is None:
        raise PriceError(f"no price list is known for the region {region or '(unknown)'}")
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


def download(url, timeout=30):
    request = urllib.request.Request(require_https(url), headers={"User-Agent": "Klotse-aws", "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310 - https only, checked above
        data = response.read(MAX_DOWNLOAD_BYTES + 1)
    if len(data) > MAX_DOWNLOAD_BYTES:
        raise PriceError("the price file is unexpectedly large")
    return data


def on_demand_prices(region, os_name, cache_dir, fetch=download, now=time.time):
    """The price table for a region and operating system: from the cache when it is under a week old, else downloaded.

    A failed download falls back to an older cached copy (the table's `note` says so); with no copy it raises PriceError.
    """
    url = price_url(region, os_name)
    path = Path(cache_dir) / f"ondemand-{region}-{os_name}.json" if cache_dir else None
    cached = None
    if path is not None and path.is_file():
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            cached = cached if isinstance(cached.get("prices"), dict) else None
        except (OSError, ValueError, AttributeError):
            cached = None
    age = now() - cached["fetched"] if cached else None
    if cached and 0 <= age < CACHE_MAX_AGE_S:
        return PriceTable(cached["prices"])
    try:
        prices = parse_price_file(fetch(url))
        if not prices:
            raise PriceError("the price file had no prices in it")
    except (OSError, ValueError, PriceError) as error:  # urllib errors are OSErrors; bad JSON is a ValueError
        if cached:
            return PriceTable(cached["prices"], f"using prices from {int(age // 86400)} days ago: {error}")
        raise PriceError(f"could not download the prices for {region}: {error}") from error
    if path is not None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"fetched": now(), "prices": prices}), encoding="utf-8")
        except OSError:
            pass  # a cache that cannot be written only costs a download next time
    return PriceTable(prices)
