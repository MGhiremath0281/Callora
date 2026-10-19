"""Shared CSV helpers for the tests."""

import io


def build_csv(header: list[str], rows: list[list]) -> bytes:
    lines = [",".join(f'"{c}"' for c in header)]
    for row in rows:
        lines.append(",".join(f'"{v}"' for v in row))
    return ("\n".join(lines) + "\n").encode("utf-8")


def upload(client, csv_bytes: bytes, headers: dict, filename: str = "leads.csv", mappings=None):
    data = {}
    if mappings is not None:
        import json

        # If mappings is already a string, pass it through as-is (it's raw JSON).
        # If it's a dict, serialize it.
        data["mappings"] = mappings if isinstance(mappings, str) else json.dumps(mappings)
    return client.post(
        "/api/v1/leads/import",
        files={"file": (filename, csv_bytes, "text/csv")},
        data=data,
        headers=headers,
    )


# A realistic real-estate style export with heterogeneous headers.
REALESTATE_HEADER = [
    "Customer Name",
    "Mobile Number",
    "Lead Source",
    "Property Budget",
    "Email Address",
    "Last Contacted",
    "Language",
]

REALESTATE_ROWS = [
    ["Rahul Sharma", "9876543210", "99acres", "₹75,00,000", "rahul@example.com", "2026-01-15", "hindi"],
    ["PRIYA PATEL", "+91 98123 45678", "Website", "1200000", "priya@example.com", "15/02/2026", "Hindi"],
    ["Rahul Sharma", "9876543210", "99acres", "7500000", "rahul@example.com", "", ""],
    ["Bad Phone", "123", "website", "abc", "not-an-email", "garbage-date", "english"],
]
