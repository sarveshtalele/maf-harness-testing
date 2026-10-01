"""Read public Azure retail prices; no subscription credentials required."""
import argparse
import datetime
import json
from pathlib import Path
import urllib.parse
import urllib.request

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--region", default="eastus")
args = parser.parse_args()
if not args.region.isalnum():
    parser.error("Use an ARM region name such as eastus or centralindia.")
query = f"serviceName eq 'Azure App Service' and armRegionName eq '{args.region}' and priceType eq 'Consumption'"
url = "https://prices.azure.com/api/retail/prices?" + urllib.parse.urlencode({"$filter": query, "currencyCode": "USD"})
items = []
while url:
    # Follow pagination only on the documented HTTPS API host.
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != "prices.azure.com":
        raise ValueError("Unexpected pricing pagination host")
    with urllib.request.urlopen(url, timeout=60) as response:
        page = json.load(response)
    items.extend(x for x in page["Items"] if x.get("skuName") in {"B1", "B2", "B3"}
                 and x.get("productName") == "Azure App Service Basic Plan - Linux"
                 and x.get("type") == "Consumption" and x.get("isPrimaryMeterRegion")
                 and x.get("unitOfMeasure") == "1 Hour")
    url = page.get("NextPageLink")
if not items:
    raise SystemExit("No matching Linux Basic meters found; do not reuse another region's price.")
data = {"checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "region": args.region,
        "currency": "USD", "hours_per_month": 730, "source": "https://prices.azure.com/api/retail/prices", "items": items}
path = Path(__file__).with_name("retail-prices.json")
path.write_text(json.dumps(data, indent=2) + "\n")
for item in sorted(items, key=lambda x: x["skuName"]):
    print(f'{item["skuName"]}: ${item["retailPrice"]:.3f}/hour; ${item["retailPrice"] * 730:.2f}/730 hours')
