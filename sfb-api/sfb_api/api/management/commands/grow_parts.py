"""Read public catalog pages and store parts that are actually in the HTML.

The loop follows Nu World: one page at a time, a pause between requests,
a retry on a timeout, a stop when the host refuses, and a tally of usable
rows against rejected ones. A repeat run updates a fitment score when the
new one is higher. The reader and matcher run only when OPENAI_API_KEY is set.
"""

import time

from django.conf import settings
from django.core.management.base import BaseCommand

from sfb_api.api.catalog import known_fits, link_inferred_fits, save_collected_part
from sfb_api.api.catalog import _missing_fitment_column
from sfb_api.api.catalog_fetch import CatalogBlocked, CatalogMissing, fetch_catalog
from sfb_api.api.catalog_parse import (
    DESIGN911,
    ROCKAUTO,
    SHOWMETHEPARTS,
    SOURCES,
    design911_url,
    parse_design911,
    parse_rockauto_listing,
    rockauto_category_links,
    rockauto_engine_links,
    rockauto_part_links,
    rockauto_vehicle_url,
)
from sfb_api.api.catalog_route import (
    SourceReport,
    compact_catalog_text,
    mark_collected_parts,
)
from sfb_api.api.supabase_client import get_supabase

DELAYS = {ROCKAUTO: 2, DESIGN911: 3, SHOWMETHEPARTS: 3}
MATCHER_LIMIT = 3


class Command(BaseCommand):
    help = "Read RockAuto or Design911 catalog pages into the parts and fitment tables."

    def add_arguments(self, parser):
        parser.add_argument("--make", required=True)
        parser.add_argument("--model", default=None)
        parser.add_argument("--year", type=int, default=0)
        parser.add_argument("--source", default=ROCKAUTO, choices=SOURCES)
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--max-pages", type=int, default=4)
        parser.add_argument("--limit", type=int, default=0, help="Stop after this many vehicles. 0 means all.")

    def handle(self, *args, **options):
        source = options["source"]
        self.report = SourceReport(source)
        self.delay = DELAYS[source]
        self.reader_note = False
        if source == SHOWMETHEPARTS:
            self.stderr.write(
                "ShowMeTheParts does not publish part rows. Its public page is a script "
                "shell, and the search URL returns 404, so nothing is fetched."
            )
            self.stdout.write(self.report.line())
            return
        try:
            self.collect(options)
        except CatalogBlocked as exc:
            self.stderr.write(f"Stopped. {exc}")
        self.stdout.write(self.report.line())

    def collect(self, options):
        source = options["source"]
        vehicles = self.vehicles(options["make"], options["model"], options["year"])
        if options["limit"]:
            vehicles = vehicles[: options["limit"]]
        self.stdout.write(
            f"Found {len(vehicles)} vehicles for {options['make']} {options['model'] or '(all models)'}"
        )
        for vehicle in vehicles:
            url = self.vehicle_url(vehicle, source)
            try:
                parts = self.parts_for(vehicle, source, options["max_pages"])
            except CatalogBlocked:
                raise
            parts = self.interpret(vehicle, parts, url)
            label = f"{vehicle['year']} {vehicle['make']} {vehicle['model']}"
            self.stdout.write(f"  {label}: found {len(parts)} parts")
            if options["dry_run"]:
                for part in parts:
                    oem = part.get("oem_part_number") or "none"
                    self.stdout.write(f"    {part['category']} | {part['part_name']} | OEM: {oem}")
                continue
            if not parts:
                continue
            stored = []
            try:
                for part in parts:
                    saved = save_collected_part(vehicle, part, f"growth_{source}", url)
                    self.report.add_save(saved)
                    stored.append(saved)
                self.infer_more(stored, url)
            except Exception as exc:
                if _missing_fitment_column(exc) or "match_log" in str(exc).lower():
                    self.stderr.write(
                        "Supabase is missing the reader columns. Run "
                        "supabase/migrations/20261114220000_reader_matcher.sql, then run this command again."
                    )
                    return
                raise
            created = sum(1 for item in stored if item["part_created"])
            self.stdout.write(
                f"    stored {created} new parts, {len(stored) - created} existing, "
                f"{sum(item['fitment_created'] for item in stored)} fitment rows"
            )

    def interpret(self, vehicle, parts, url):
        if not parts:
            return parts
        if not settings.OPENAI_API_KEY:
            if not self.reader_note:
                self.stdout.write(
                    "Reader kept the parsed page. Set OPENAI_API_KEY to score cross-car fits."
                )
                self.reader_note = True
            return mark_collected_parts(parts)
        from sfb_api.api.ai_reader import read_catalog

        try:
            read = read_catalog(compact_catalog_text(parts, url), url, vehicle)
        except Exception as exc:
            self.stderr.write(f"  Reader kept the parsed page: {exc}")
            return mark_collected_parts(parts)
        return read or mark_collected_parts(parts)

    def infer_more(self, stored, url):
        if not settings.OPENAI_API_KEY or not stored:
            return
        from sfb_api.api.ai_matcher import find_matches

        chosen = stored[:MATCHER_LIMIT]
        if len(stored) > MATCHER_LIMIT:
            self.stdout.write(f"    Matcher checked the first {MATCHER_LIMIT} parts.")
        for saved in chosen:
            part = saved["part"]
            numbers = [part.get("oem_part_number")] if part.get("oem_part_number") else []
            try:
                matches = find_matches(
                    part.get("part_name"),
                    part.get("category"),
                    numbers,
                    known_fits(saved["part_id"]),
                )
            except Exception as exc:
                self.stderr.write(f"    Matcher skipped {part.get('part_name')}: {exc}")
                continue
            for match in matches:
                created, updated = link_inferred_fits(saved["part_id"], match, url)
                self.report.fitment_new += created
                self.report.fitment_updated += updated

    def vehicles(self, make, model, year=0):
        query = (
            get_supabase()
            .table("vehicles")
            .select("id,year,make,model,engine,generation,body_style")
            .ilike("make", make.strip())
        )
        if model:
            query = query.ilike("model", model.strip())
        if year:
            query = query.eq("year", int(year))
        return query.order("year").limit(500).execute().data or []

    def vehicle_url(self, vehicle, source):
        if source == DESIGN911:
            return design911_url(vehicle["make"], vehicle["year"], vehicle["model"])
        return rockauto_vehicle_url(vehicle["make"], vehicle["year"], vehicle["model"])

    def read_page(self, url):
        try:
            html = fetch_catalog(url)
        except CatalogMissing as exc:
            self.report.rejected += 1
            self.stderr.write(f"  Skip {exc}")
            return None
        time.sleep(self.delay)
        return html

    def parts_for(self, vehicle, source, max_pages):
        if source == DESIGN911:
            html = self.read_page(design911_url(vehicle["make"], vehicle["year"], vehicle["model"]))
            if html is None:
                return []
            parts = parse_design911(html)
            if parts:
                self.report.usable += len(parts)
            else:
                self.report.rejected += 1
            return parts
        return self.rockauto_parts(vehicle, max_pages)

    def rockauto_parts(self, vehicle, max_pages):
        html = self.read_page(rockauto_vehicle_url(vehicle["make"], vehicle["year"], vehicle["model"]))
        if html is None:
            return []
        engines = rockauto_engine_links(html, vehicle["make"], vehicle["year"], vehicle["model"])
        wanted = (vehicle.get("engine") or "").casefold().replace(" ", "")
        if wanted:
            engines = [
                engine for engine in engines
                if engine["engine"].casefold().replace(" ", "") == wanted
            ]
        elif engines:
            engines = engines[:1]

        parts = []
        pages = 0
        for engine in engines:
            if pages >= max_pages:
                break
            engine_html = self.read_page(engine["url"])
            if engine_html is None:
                continue
            for category_url in rockauto_category_links(engine_html):
                if pages >= max_pages:
                    break
                category_html = self.read_page(category_url)
                if category_html is None:
                    continue
                for link in rockauto_part_links(category_html):
                    if pages >= max_pages:
                        break
                    listing = self.read_page(link["url"])
                    if listing is None:
                        continue
                    pages += 1
                    part = parse_rockauto_listing(listing, link["part_name"], link["category"])
                    if part:
                        parts.append(part)
                        self.report.usable += 1
                    else:
                        self.report.rejected += 1
        return parts
