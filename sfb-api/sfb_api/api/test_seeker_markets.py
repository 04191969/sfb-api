import os
import unittest
from datetime import datetime, timezone

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "sfb_api.settings")

import django

django.setup()

from sfb_api.api.seeker_markets import (
    NOT_AVAILABLE,
    WORKER_UNKNOWN,
    assemble_markets,
    category_for,
    WORKER_REVISION,
    classify_heartbeat,
    display_name,
    is_market_discovery,
    market_detail,
    market_scope,
    present_run,
)


NOW = datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc)


class NameTests(unittest.TestCase):
    def test_literal_none_is_removed(self):
        self.assertEqual(display_name("Klipsch", "None"), "Klipsch")
        self.assertEqual(display_name("None", "None", "None None"), "")

    def test_categories(self):
        self.assertEqual(category_for("Epiphone", "USA Map"), "Musical Instruments")
        self.assertEqual(category_for("Porsche", "911"), "Automotive")
        self.assertEqual(category_for("Basler", "acA2550-14gm"), "Uncategorized")


class MarketAssemblyTests(unittest.TestCase):
    def test_confirmed_duplicates_share_one_market_and_old_ids_still_resolve(self):
        sections = [
            {"id": "a", "make": "Epiphone", "model": "USA Map", "name": "Epiphone USA Map", "created_at": "2026-11-16T12:00:00+00:00"},
            {"id": "b", "make": "Epiphone", "model": "USA Map guitar", "name": "Epiphone USA Map guitar", "created_at": "2026-11-16T13:00:00+00:00"},
            {"id": "c", "make": "Fender", "model": "ST-62G-spl", "name": "Fender ST-62G-spl", "created_at": "2026-11-16T03:00:00+00:00"},
            {"id": "d", "make": "Fender", "model": "ST62G-SPL", "name": "Fender ST62G-SPL", "created_at": "2026-11-16T03:01:00+00:00"},
            {"id": "e", "make": "Epiphone", "model": "USA Map Guitar Reissue 2005", "name": "Epiphone USA Map Guitar Reissue 2005", "created_at": "2026-11-16T13:50:00+00:00"},
        ]
        sellers = [
            {"id": "s1", "listing_text": "Found for sale\nEpiphone", "price": 850, "source_platform": "reverb", "source_url": "https://reverb.com/item/1"},
            {"id": "s2", "listing_text": "Listing ended\nFender", "price": 900, "source_platform": "reverb", "source_url": "https://reverb.com/item/2"},
            {"id": "s3", "listing_text": "Availability unconfirmed\nFender", "price": None, "source_platform": "facebook", "source_url": "https://facebook.com/marketplace/item/3"},
        ]
        buyers = [
            {"id": "u1", "source_platform": "sfb", "source_url": "https://searchfindbuy.local/requests/1", "post_text": "Item: Epiphone USA Map guitar"},
        ]
        matches = [
            {"id": "m1", "section_id": "b", "seller_listing_id": "s1", "buyer_post_id": "u1", "match_decision": "send", "ai_reasoning": "Product: matches", "created_at": "2026-11-16T14:00:00+00:00", "status": "pending_review"},
            {"id": "m2", "section_id": "c", "seller_listing_id": "s2", "buyer_post_id": "u1", "match_decision": "no_send", "ai_reasoning": "", "created_at": "2026-11-16T14:00:00+00:00", "status": "pending_review"},
            {"id": "m3", "section_id": "d", "seller_listing_id": "s3", "buyer_post_id": "u1", "match_decision": "send", "ai_reasoning": "Possible cross-post. Not counted as two instruments.", "created_at": "2026-11-16T14:00:00+00:00", "status": "pending_review"},
        ]
        runs = [
            {"search_query": "Item: Epiphone USA Map guitar", "result_summary": "Found 1 qualifying match(es)", "completed_at": "2026-11-16T14:10:00+00:00", "requested_at": "2026-11-16T14:00:00+00:00"},
        ]
        result = assemble_markets(sections, matches, buyers, sellers, runs, NOW)
        by_name = {market["name"]: market for market in result["markets"]}
        usa = by_name["Epiphone USA Map"]
        self.assertEqual(set(usa["alias_ids"]), {"a", "b"})
        self.assertEqual(usa["category"], "Musical Instruments")
        self.assertEqual(usa["verified_available_offers"], 1)
        self.assertEqual(usa["qualifying_matches_7d"], 1)
        self.assertEqual(usa["open_buyer_requests"], NOT_AVAILABLE)
        fender = by_name["Fender ST62G-SPL"]
        self.assertEqual(set(fender["alias_ids"]), {"c", "d"})
        self.assertEqual(fender["verified_available_offers"], 0)
        self.assertEqual(fender["unconfirmed_offers"], 0)
        self.assertEqual(fender["qualifying_matches_7d"], 0)
        self.assertIn("Epiphone USA Map Guitar Reissue 2005", by_name)
        self.assertTrue(any(item["name"] == "Epiphone USA Map" for item in result["review"]))
        self.assertEqual(result["worker"]["message"], WORKER_UNKNOWN)
        detail = market_detail("b", sections, matches, buyers, sellers, runs, NOW)
        self.assertEqual(detail["market"]["id"], usa["id"])
        self.assertEqual(detail["buyer_requests"][0]["origin"], "sfb_user")
        self.assertEqual(detail["seller_offers"][0]["availability"], "available")
        self.assertFalse(result["discovery_controls"]["available"])


class DiscoveryControlTests(unittest.TestCase):
    def test_a_market_name_is_not_a_buyer_request(self):
        scope = market_scope("Epiphone USA Map", "market-1")
        self.assertTrue(is_market_discovery({"search_query": scope}))
        self.assertNotIn("Find sellers", scope)
        self.assertIn("not a buyer request", scope)

    def test_heartbeat_needs_a_fresh_row(self):
        self.assertEqual(classify_heartbeat([], NOW)["message"], WORKER_UNKNOWN)
        stale = classify_heartbeat([{"seen_at": "2026-11-16T14:00:00+00:00"}], NOW)
        self.assertEqual(stale["status"], "offline")
        fresh = classify_heartbeat(
            [{"seen_at": "2026-11-16T14:59:40+00:00", "revision": WORKER_REVISION}], NOW,
        )
        self.assertEqual(fresh["status"], "online")
        self.assertEqual(fresh["revision"], WORKER_REVISION)

    def test_control_stays_unavailable_until_the_matching_worker_is_online(self):
        offline = assemble_markets([], [], [], [], [], NOW, worker={"status": "offline", "message": "Worker offline", "revision": WORKER_REVISION}, discovery_ready=True)
        self.assertFalse(offline["discovery_controls"]["available"])
        old_worker = assemble_markets([], [], [], [], [], NOW, worker={"status": "online", "message": "Worker online", "revision": ""}, discovery_ready=True)
        self.assertFalse(old_worker["discovery_controls"]["available"])
        self.assertEqual(old_worker["discovery_controls"]["message"], "Market discovery not connected yet")
        online = assemble_markets([], [], [], [], [], NOW, worker={"status": "online", "message": "Worker online", "revision": WORKER_REVISION}, discovery_ready=True)
        self.assertTrue(online["discovery_controls"]["available"])

    def test_run_labels_do_not_invent_a_result(self):
        pending = present_run({"status": "pending"})
        self.assertEqual(pending["label"], "Queued — waiting for worker")
        self.assertFalse(pending["animate"])
        running = present_run({"status": "running"})
        self.assertEqual(running["label"], "Searching")
        self.assertTrue(running["animate"])
        done = present_run({"status": "completed", "result_summary": "Search incomplete — the page budget was used before every candidate was opened"})
        self.assertIn("Search incomplete", done["label"])
