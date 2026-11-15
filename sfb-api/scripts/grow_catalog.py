"""Add parts and fitment for one vehicle.

Pass structured JSON, or catalog text you already have the right to use.
The Reader sends text to OpenAI. This script does not fetch other sites.

    python scripts/grow_catalog.py --make Porsche --model 911 --year 1995 --json parts.json
    python scripts/grow_catalog.py --make Porsche --model 911 --year 1995 --text page.txt
"""

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load_parts(path):
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("parts", [])
    if not isinstance(payload, list):
        raise SystemExit("JSON must be a list of parts or an object with a parts array.")
    return payload


def main():
    parser = argparse.ArgumentParser(description="Grow parts and fitment for one vehicle.")
    parser.add_argument("--make", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--year", required=True, type=int)
    parser.add_argument("--engine", default="")
    parser.add_argument("--generation", default="")
    parser.add_argument("--body-style", default="")
    parser.add_argument("--source", default="growth")
    parser.add_argument("--json", dest="json_path", type=Path, help="Structured parts file.")
    parser.add_argument("--text", dest="text_path", type=Path, help="Catalog text for the Reader.")
    args = parser.parse_args()

    if bool(args.json_path) == bool(args.text_path):
        raise SystemExit("Pass exactly one of --json or --text.")

    load_dotenv(ROOT / ".env")
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "sfb_api.settings")
    import django

    django.setup()

    from sfb_api.api.growth import AmbiguousVehicle, grow_vehicle

    vehicle = {
        "year": args.year,
        "make": args.make,
        "model": args.model,
        "engine": args.engine.strip() or None,
        "generation": args.generation.strip() or None,
        "body_style": args.body_style.strip() or None,
    }
    try:
        if args.json_path:
            result = grow_vehicle(vehicle, parts=load_parts(args.json_path), source=args.source)
        else:
            result = grow_vehicle(
                vehicle,
                text=args.text_path.read_text(encoding="utf-8"),
                source=args.source,
            )
    except AmbiguousVehicle as exc:
        print("Several vehicles match. Re-run with --engine. Options:")
        for item in exc.vehicles:
            print(f"  {item.get('year')} {item.get('make')} {item.get('model')} {item.get('engine') or ''}")
        raise SystemExit(1) from exc

    print(
        f"vehicle {result['vehicle']['id']} "
        f"parts_created {result['parts_created']} "
        f"fitment_created {result['fitment_created']}"
    )


if __name__ == "__main__":
    main()
