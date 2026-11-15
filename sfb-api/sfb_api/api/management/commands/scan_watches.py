"""Search eBay for active watches and email the owner when a new listing appears."""

import os
from datetime import datetime, timezone

import httpx
from django.core.mail import send_mail
from django.core.management.base import BaseCommand

from sfb_api.api.catalog import (
    finish_scan,
    list_active_watches,
    remember_listing,
)
from sfb_api.api.ebay_client import EbayBrowseClient, EbayNotConfigured, build_query, listing_matches

WATCH_URL = "https://bright-web-workshop-37.lovable.app/watches"


class Command(BaseCommand):
    help = "Scan eBay for active watches. Railway cron: 0 */6 * * * python manage.py scan_watches"

    def handle(self, *args, **options):
        try:
            ebay = EbayBrowseClient()
        except EbayNotConfigured as exc:
            self.stderr.write(str(exc))
            return

        watches = list_active_watches()
        self.stdout.write(f"Scanning {len(watches)} active watches...")
        kept = 0
        rejected = 0
        for watch in watches:
            try:
                found, skipped = self.scan_one(ebay, watch)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429:
                    self.stderr.write("eBay rate limit reached. This scan stops and the next run retries.")
                    break
                rejected += 1
                self.stderr.write(f"  {watch.get('id')}: {exc}")
                continue
            except Exception as exc:
                rejected += 1
                self.stderr.write(f"  {watch.get('id')}: {exc}")
                continue
            kept += found
            rejected += skipped
        self.stdout.write(f"ebay: {kept} usable records; {rejected} rejected")

    def scan_one(self, ebay, watch):
        part = watch.get("parts") or {}
        vehicle = watch.get("vehicles") or {}
        if not part.get("part_name"):
            self.stderr.write(f"  Skip {watch.get('id')}: part is missing")
            return 0, 1
        query = build_query(part, vehicle)
        if not query:
            return 0, 1
        self.stdout.write(f"  Searching: {query}")
        listings = ebay.search_parts(query, max_price=watch.get("max_price"), limit=10)
        new_finds = 0
        skipped = 0
        for item in listings:
            if not listing_matches(item, part, vehicle):
                skipped += 1
                continue
            if remember_listing(watch["id"], item):
                new_finds += 1
        finish_scan(watch["id"], datetime.now(timezone.utc).isoformat())
        if new_finds and watch.get("user_email"):
            self.notify(watch, part, vehicle, new_finds)
        return new_finds, skipped

    def notify(self, watch, part, vehicle, count):
        vehicle_label = " ".join(
            str(piece) for piece in (vehicle.get("year"), vehicle.get("make"), vehicle.get("model"))
            if piece not in (None, "")
        )
        subject = f"SFB Alert: {count} listing(s) found for {part.get('part_name')}"
        body = (
            f"We found {count} new eBay listing(s) for:\n\n"
            f"Part: {part.get('part_name')}\n"
            f"Vehicle: {vehicle_label}\n\n"
            f"View your results at: {WATCH_URL}\n"
        )
        send_mail(
            subject,
            body,
            os.environ.get("DEFAULT_FROM_EMAIL", "alerts@sfb.app"),
            [watch["user_email"]],
            fail_silently=True,
        )
        self.stdout.write(f"  Notified {watch['user_email']}: {count} new results")
