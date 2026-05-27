#!/usr/bin/env python3
"""
Probe Onebox catalog endpoints to find where EUR prices per price_type / sector live.

Hits each candidate endpoint, walks the JSON response, and reports every field
whose path contains price / amount / value / fare / tariff / currency / cost.

Usage:
  python3 probe_prices.py                          # defaults: event 4587, session 240895
  python3 probe_prices.py --event 4587 --session 240895
  python3 probe_prices.py --dump                   # also print full JSON bodies

Requires .env with: ONEBOX_BASE_URL, ONEBOX_CHANNEL_ID, ONEBOX_CLIENT_ID, ONEBOX_CLIENT_SECRET
(same as onebox_test.py)
"""

import argparse
import json
import os
import sys
import warnings
warnings.filterwarnings("ignore")

import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL      = os.getenv("ONEBOX_BASE_URL", "").rstrip("/")
CHANNEL_ID    = os.getenv("ONEBOX_CHANNEL_ID", "")
CLIENT_ID     = os.getenv("ONEBOX_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("ONEBOX_CLIENT_SECRET", "")

PRICE_KEYS = ("price", "amount", "value", "fare", "tariff", "currency", "cost")

C = {
    "B":   "\033[1m",
    "G":   "\033[32m",
    "R":   "\033[31m",
    "Y":   "\033[33m",
    "C":   "\033[36m",
    "DIM": "\033[2m",
    "X":   "\033[0m",
}


def authenticate():
    if not all([BASE_URL, CHANNEL_ID, CLIENT_ID, CLIENT_SECRET]):
        print(f"{C['R']}Missing ONEBOX credentials in .env{C['X']}")
        print("Required: ONEBOX_BASE_URL, ONEBOX_CHANNEL_ID, ONEBOX_CLIENT_ID, ONEBOX_CLIENT_SECRET")
        sys.exit(1)
    r = requests.post(
        f"{BASE_URL}/oauth/token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type":    "client_credentials",
            "channel_id":    CHANNEL_ID,
            "client_id":     CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
        timeout=15,
    )
    if r.status_code != 200:
        print(f"{C['R']}Auth failed: HTTP {r.status_code} → {r.text[:200]}{C['X']}")
        sys.exit(1)
    return r.json().get("access_token") or r.json().get("token")


def walk(obj, path="$"):
    """Yield (json-path, leaf-value) for every primitive in obj."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from walk(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk(v, f"{path}[{i}]")
    else:
        yield path, obj


def find_price_fields(body):
    """Return list of (path, value) where any path segment contains a price keyword."""
    hits = []
    for path, val in walk(body):
        if any(k in path.lower() for k in PRICE_KEYS):
            hits.append((path, val))
    return hits


def probe(token, label, path):
    url = f"{BASE_URL}{path}"
    print(f"\n{C['B']}{C['C']}▶ {label}{C['X']}")
    print(f"  GET {url}")
    try:
        r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=15)
    except requests.RequestException as e:
        print(f"  {C['R']}Request failed: {e}{C['X']}")
        return None

    print(f"  Status: HTTP {r.status_code}")
    if r.status_code != 200:
        try:
            print(f"  Body:   {json.dumps(r.json())[:200]}")
        except ValueError:
            print(f"  Raw:    {r.text[:200]}")
        return None

    try:
        body = r.json()
    except ValueError:
        print(f"  {C['R']}Response is not JSON{C['X']}")
        return None

    hits = find_price_fields(body)
    if not hits:
        print(f"  {C['DIM']}No price-related fields found.{C['X']}")
    else:
        unique_keys = sorted({h[0].split(".")[-1].split("[")[0] for h in hits})
        print(f"  {C['G']}✓ Found {len(hits)} price-related fields ({len(unique_keys)} unique keys):{C['X']}")
        print(f"    keys: {', '.join(unique_keys)}")
        for path, val in hits[:25]:
            print(f"    {C['Y']}{path}{C['X']} = {val!r}")
        if len(hits) > 25:
            print(f"    {C['DIM']}… {len(hits) - 25} more{C['X']}")
    return body


def main():
    p = argparse.ArgumentParser(description="Probe Onebox endpoints for EUR price fields.")
    p.add_argument("--event",   default="4587",   help="Event ID (default 4587)")
    p.add_argument("--session", default="240895", help="Session ID (default 240895)")
    p.add_argument("--dump",    action="store_true", help="Also dump full JSON of each successful response")
    args = p.parse_args()

    print(f"{C['B']}Onebox price-field probe — event={args.event}, session={args.session}{C['X']}")
    token = authenticate()
    print(f"{C['G']}Authenticated.{C['X']}")

    probes = [
        ("Event detail",                  f"/catalog-api/v1/events/{args.event}"),
        ("Event sessions list",           f"/catalog-api/v1/events/{args.event}/sessions"),
        ("Session detail (event-scoped)", f"/catalog-api/v1/events/{args.event}/sessions/{args.session}"),
        ("Session direct",                f"/catalog-api/v1/sessions/{args.session}"),
        ("Session availability",          f"/catalog-api/v1/sessions/{args.session}/availability"),
    ]

    results = []
    for label, path in probes:
        body = probe(token, label, path)
        if body is not None and args.dump:
            print(f"\n  {C['DIM']}── full JSON ──{C['X']}")
            print(json.dumps(body, indent=2, ensure_ascii=False))
        results.append((label, body))

    print(f"\n{C['B']}{C['C']}══════════ Summary ══════════{C['X']}")
    ranked = []
    for label, body in results:
        if body is None:
            print(f"  {C['R']}✗{C['X']} {label}  — failed")
            continue
        hits = find_price_fields(body)
        unique = sorted({h[0].split('.')[-1].split('[')[0] for h in hits})
        ranked.append((label, hits))
        if hits:
            print(f"  {C['G']}✓{C['X']} {label}  — {len(hits)} hits, keys: {', '.join(unique[:10])}")
        else:
            print(f"  {C['Y']}∅{C['X']} {label}  — no price-like fields")

    ranked.sort(key=lambda x: -len(x[1]))
    if ranked and ranked[0][1]:
        print(f"\n{C['B']}{C['G']}→ Best candidate for per-tier prices: {ranked[0][0]}{C['X']}")
        print(f"  ({len(ranked[0][1])} price-related fields)")
        print(f"  Re-run with --dump to inspect the full JSON of all endpoints.")
    else:
        print(f"\n{C['Y']}No endpoint surfaced EUR amounts. Pricing likely lives in the API Distribution / Orders Management specs, which the docs portal renders client-side and weren't crawlable.{C['X']}")


if __name__ == "__main__":
    main()
