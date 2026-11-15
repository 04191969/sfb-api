"""Load vehicles.csv, parts.csv, and fitment.csv into Supabase.

Reads SUPABASE_URL and SUPABASE_KEY from the environment or sfb-api/.env.
The service role key is required. Run the SQL migration first.

    python scripts/import_catalog.py --dry-run
    python scripts/import_catalog.py
"""

import argparse
import csv
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sfb_api.api.catalog_shape import (  # noqa: E402
    dedupe_fitment,
    fitment_record,
    part_record,
    vehicle_record,
)

DOWNLOADS = Path.home() / "Downloads"
BATCH = 400


def load_csv(path):
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def prepare(vehicles_path, parts_path, fitment_path):
    vehicles = []
    skipped_vehicles = 0
    for row in load_csv(vehicles_path):
        record = vehicle_record(row)
        if record is None:
            skipped_vehicles += 1
        else:
            vehicles.append(record)

    parts = []
    skipped_parts = 0
    for row in load_csv(parts_path):
        record = part_record(row)
        if record is None:
            skipped_parts += 1
        else:
            parts.append(record)

    fitment = []
    skipped_fitment = 0
    part_ids = {row["id"] for row in parts}
    vehicle_ids = {row["id"] for row in vehicles}
    for row in load_csv(fitment_path):
        record = fitment_record(row)
        if record is None or record["part_id"] not in part_ids or record["vehicle_id"] not in vehicle_ids:
            skipped_fitment += 1
            continue
        fitment.append(record)
    fitment, dropped = dedupe_fitment(fitment)
    return {
        "vehicles": vehicles,
        "parts": parts,
        "fitment": fitment,
        "skipped_vehicles": skipped_vehicles,
        "skipped_parts": skipped_parts,
        "skipped_fitment": skipped_fitment + dropped,
        "duplicate_fitment": dropped,
    }


def upsert_batches(client, table, rows):
    for index in range(0, len(rows), BATCH):
        chunk = rows[index:index + BATCH]
        client.table(table).upsert(chunk, on_conflict="id").execute()
        print(f"  {table}: {min(index + BATCH, len(rows))}/{len(rows)}")


def main():
    parser = argparse.ArgumentParser(description="Import the fitment catalog CSVs into Supabase.")
    parser.add_argument("--vehicles", type=Path, default=DOWNLOADS / "vehicles.csv")
    parser.add_argument("--parts", type=Path, default=DOWNLOADS / "parts.csv")
    parser.add_argument("--fitment", type=Path, default=DOWNLOADS / "fitment.csv")
    parser.add_argument("--dry-run", action="store_true", help="Parse and count rows without writing.")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    for path in (args.vehicles, args.parts, args.fitment):
        if not path.is_file():
            raise SystemExit(f"Missing CSV: {path}")

    prepared = prepare(args.vehicles, args.parts, args.fitment)
    print(
        f"vehicles {len(prepared['vehicles'])} "
        f"(skipped {prepared['skipped_vehicles']})"
    )
    print(
        f"parts {len(prepared['parts'])} "
        f"(skipped {prepared['skipped_parts']})"
    )
    print(
        f"fitment {len(prepared['fitment'])} "
        f"(skipped {prepared['skipped_fitment']}, "
        f"duplicate pairs {prepared['duplicate_fitment']})"
    )
    if args.dry_run:
        return

    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_KEY", "")
    if not url or not key:
        raise SystemExit("Set SUPABASE_URL and SUPABASE_KEY in sfb-api/.env before importing.")

    from supabase import create_client

    client = create_client(url, key)
    print("Writing vehicles")
    upsert_batches(client, "vehicles", prepared["vehicles"])
    print("Writing parts")
    upsert_batches(client, "parts", prepared["parts"])
    print("Writing fitment")
    upsert_batches(client, "fitment", prepared["fitment"])
    print("Import finished.")


if __name__ == "__main__":
    main()
