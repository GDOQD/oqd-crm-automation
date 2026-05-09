"""
Zoho CRM audit script.

Connects to Zoho CRM, pulls all records from key modules, writes them to CSVs,
and prints a summary. Designed to run as a GitHub Action.
"""

import csv
import os
import sys
from pathlib import Path

import requests

# --- Config from environment (GitHub Secrets) ---
CLIENT_ID = os.environ["ZOHO_CLIENT_ID"]
CLIENT_SECRET = os.environ["ZOHO_CLIENT_SECRET"]
REFRESH_TOKEN = os.environ["ZOHO_REFRESH_TOKEN"]
API_DOMAIN = os.environ["ZOHO_API_DOMAIN"]
ACCOUNTS_DOMAIN = os.environ["ZOHO_ACCOUNTS_DOMAIN"]

# Zoho CRM API version. v2 is long-stable across all datacenters.
API_VERSION = "v2"

# Modules to audit. Start with the standard B2B four.
MODULES = ["Leads", "Contacts", "Accounts", "Deals"]

# Where to write CSVs (relative to repo root)
OUTPUT_DIR = Path("audit_output")


def get_access_token():
    """Exchange refresh token for a fresh access token."""
    url = f"{ACCOUNTS_DOMAIN}/oauth/v2/token"
    data = {
        "grant_type": "refresh_token",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": REFRESH_TOKEN,
    }
    r = requests.post(url, data=data, timeout=30)
    r.raise_for_status()
    payload = r.json()
    if "access_token" not in payload:
        raise RuntimeError(f"No access_token in response: {payload}")
    return payload["access_token"]


def fetch_module_records(module, access_token):
    """Fetch all records from a Zoho module, paginating through results."""
    headers = {"Authorization": f"Zoho-oauthtoken {access_token}"}
    records = []
    page = 1
    per_page = 200

    while True:
        url = f"{API_DOMAIN}/crm/{API_VERSION}/{module}"
        params = {"page": page, "per_page": per_page}
        r = requests.get(url, headers=headers, params=params, timeout=30)

        if r.status_code == 204:
            # No content - module is empty
            break
        if r.status_code == 401:
            raise RuntimeError(f"Auth failed on {module}: {r.text}")
        if not r.ok:
            # Surface the actual Zoho error message
            raise RuntimeError(
                f"{module} request failed: HTTP {r.status_code} - {r.text}"
            )

        payload = r.json()
        batch = payload.get("data", [])
        records.extend(batch)

        info = payload.get("info", {})
        if not info.get("more_records"):
            break
        page += 1

    return records


def fetch_module_fields(module, access_token):
    """Get the list of fields (including custom) defined on a module."""
    headers = {"Authorization": f"Zoho-oauthtoken {access_token}"}
    url = f"{API_DOMAIN}/crm/{API_VERSION}/settings/fields"
    params = {"module": module}
    r = requests.get(url, headers=headers, params=params, timeout=30)
    if not r.ok:
        raise RuntimeError(
            f"{module} fields request failed: HTTP {r.status_code} - {r.text}"
        )
    return r.json().get("fields", [])


def flatten_record(record):
    """Flatten nested fields (like Owner, Account_Name) into simple values for CSV."""
    flat = {}
    for key, value in record.items():
        if isinstance(value, dict):
            # e.g. {"name": "Jagdeep", "id": "..."} -> just take "name"
            flat[key] = value.get("name") or value.get("id") or str(value)
        elif isinstance(value, list):
            flat[key] = "; ".join(str(v) for v in value)
        else:
            flat[key] = value
    return flat


def write_csv(module, records):
    """Write records to a CSV in OUTPUT_DIR."""
    OUTPUT_DIR.mkdir(exist_ok=True)
    path = OUTPUT_DIR / f"{module.lower()}.csv"

    if not records:
        path.write_text("(no records)\n", encoding="utf-8")
        return path

    flat_records = [flatten_record(r) for r in records]
    # Union of all keys, preserving rough order from the first record
    all_keys = list(flat_records[0].keys())
    for rec in flat_records[1:]:
        for k in rec.keys():
            if k not in all_keys:
                all_keys.append(k)

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=all_keys, extrasaction="ignore")
        writer.writeheader()
        for rec in flat_records:
            writer.writerow(rec)

    return path


def summarize(module, records, fields):
    """Print a summary of what we found in this module."""
    print(f"\n=== {module} ===")
    print(f"  Records: {len(records)}")
    print(f"  Fields defined: {len(fields)}")

    custom_fields = [f for f in fields if f.get("custom_field")]
    if custom_fields:
        print(f"  Custom fields ({len(custom_fields)}):")
        for f in custom_fields:
            print(f"    - {f.get('api_name')} ({f.get('data_type')})")

    # Module-specific summaries
    if module == "Deals" and records:
        stages = {}
        for r in records:
            stage = r.get("Stage", "(no stage)")
            stages[stage] = stages.get(stage, 0) + 1
        print("  Deals by stage:")
        for stage, count in sorted(stages.items(), key=lambda x: -x[1]):
            print(f"    - {stage}: {count}")

    if module == "Leads" and records:
        statuses = {}
        for r in records:
            status = r.get("Lead_Status", "(no status)")
            statuses[status] = statuses.get(status, 0) + 1
        print("  Leads by status:")
        for status, count in sorted(statuses.items(), key=lambda x: -x[1]):
            print(f"    - {status}: {count}")


def main():
    print("Authenticating with Zoho CRM...")
    access_token = get_access_token()
    print("✓ Got access token\n")

    print(f"Auditing modules: {', '.join(MODULES)}")
    print(f"Using API version: {API_VERSION}")

    for module in MODULES:
        try:
            records = fetch_module_records(module, access_token)
            fields = fetch_module_fields(module, access_token)
            csv_path = write_csv(module, records)
            summarize(module, records, fields)
            print(f"  → wrote {csv_path}")
        except Exception as e:
            print(f"  ✗ Error on {module}: {e}", file=sys.stderr)

    print("\n=== Audit complete ===")
    print(f"CSVs in: {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
