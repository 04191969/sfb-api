import os
import unittest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "sfb_api.settings")

import django

django.setup()

from sfb_api.api.ebay_client import EbayNotConfigured
from sfb_api.api.listing_find import find_listings
from sfb_api.api.listing_sources import (
    CL_CITIES,
    FORUM_LIMIT,
    accessible_sources,
    brave_forum_url,
    brave_results,
    craigslist_city_url,
    get_forum_sites,
    get_subs_for_vehicle,
    is_for_sale,
    keep_listing,
    reddit_posts,
    reddit_search_url,
    rockauto_rss_url,
    rss_items,
    source_for_url,
    unavailable_sources,
)

FEED = """<?xml version="1.0"?>
<rss><channel>
<item>
  <title>996 IMS bearing for sale</title>
  <link>https://www.pelicanparts.com/porsche/996</link>
  <description>Used bearing, local pickup.</description>
</item>
</channel></rss>
"""


class SourceListTests(unittest.TestCase):
    def test_marketplace_and_lkq_are_not_searched(self):
        ids = {row["id"] for row in accessible_sources()}
        self.assertNotIn("facebook", ids)
        self.assertNotIn("lkq", ids)
        self.assertEqual(
            {row["id"] for row in unavailable_sources()},
            {"facebook", "lkq"},
        )

    def test_the_named_sites_are_available(self):
        ids = {row["id"] for row in accessible_sources()}
        for source_id in (
            "ebay", "oldcarsdata", "rockauto", "craigslist", "reddit", "rennlist",
            "thesamba", "pelicanparts", "bimmerforums", "ferrarichat",
            "jaglovers", "landroverforums", "hemmings", "bringatrailer",
            "forabodiesonly", "grassrootsmotorsports", "carpart", "row52",
        ):
            self.assertIn(source_id, ids)

    def test_urls_match_the_access_method(self):
        city = craigslist_city_url("sfbay", "brake caliper")
        self.assertTrue(city.startswith("https://sfbay.craigslist.org/search/pta?"))
        self.assertIn("format=rss", city)
        self.assertIn("brake+caliper", city)
        self.assertEqual(len(CL_CITIES), 15)
        reddit = reddit_search_url("Porsche", "IMS bearing")
        self.assertIn("/r/Porsche/search.json", reddit)
        self.assertIn("restrict_sr=on", reddit)
        porsche = get_subs_for_vehicle("Porsche", "911")
        self.assertIn("AutoPartsSales", porsche)
        self.assertIn("Porsche911", porsche)
        self.assertLessEqual(len(porsche), 15)
        self.assertNotIn("Miata", porsche)
        self.assertIn("projectcar", get_subs_for_vehicle("Mercedes-Benz"))
        self.assertTrue(is_for_sale("[FS] IMS bearing"))
        self.assertFalse(is_for_sale("How do I replace an IMS bearing?"))
        self.assertIsNone(rockauto_rss_url(""))
        self.assertIn("carcode=1190933", rockauto_rss_url("1190933"))
        porsche_sites = get_forum_sites("Porsche")
        self.assertEqual(porsche_sites[0], "rennlist.com")
        self.assertNotIn("bimmerforums.com", porsche_sites)
        searched = porsche_sites[:FORUM_LIMIT]
        self.assertEqual(FORUM_LIMIT, 4)
        self.assertIn("pelicanparts.com", searched)
        for domain in ("car-part.com", "row52.com", "hemmings.com", "bringatrailer.com"):
            self.assertNotIn(domain, porsche_sites)
        mercedes = get_forum_sites("Mercedes-Benz")
        self.assertEqual(mercedes[3], "grassrootsmotorsports.com")
        self.assertNotIn("hemmings.com", mercedes)
        self.assertIn("thesamba.com", get_forum_sites("Volkswagen"))
        self.assertIn("ferrarichat.com", get_forum_sites("Ferrari"))
        self.assertIn("corvetteforums.com", get_forum_sites("Cadillac"))
        self.assertIn("AlfaRomeo", get_subs_for_vehicle("Alfa Romeo"))
        query = brave_forum_url("brake caliper", "r3vlimited.com")
        self.assertTrue(query.startswith("https://search.brave.com/search?"))
        self.assertIn("site%3Ar3vlimited.com", query)
        self.assertIn("%22for+sale%22", query)
        self.assertNotIn("miata.net", query)
        page = (
            '<a href="https://www.pelicanparts.com/porsche/caliper">'
            "Front brake caliper for sale</a>"
            '<a href="https://www.ebay.com/itm/1">brake caliper</a>'
        )
        found = brave_results(page, "pelicanparts.com")
        self.assertEqual(found[0]["url"], "https://www.pelicanparts.com/porsche/caliper")
        self.assertEqual(len(found), 1)
        self.assertEqual(source_for_url("https://www.thesamba.com/vw/1"), "thesamba")

    def test_rss_keeps_a_part_mention(self):
        items = rss_items(FEED)
        self.assertEqual(items[0]["title"], "996 IMS bearing for sale")
        self.assertTrue(keep_listing(items[0], "IMS bearing"))
        self.assertFalse(keep_listing(items[0], "caliper"))


class FindTests(unittest.TestCase):
    def test_a_blocked_feed_does_not_stop_the_other_sources(self):
        def get_status(url, **_kwargs):
            class Response:
                headers = {}

                def __init__(self, status, body):
                    self.status_code = status
                    self.text = body

            if "craigslist" in url:
                return Response(403, "")
            return Response(200, FEED)

        seen = {}

        def read(raw, vehicle):
            seen["raw"] = raw
            seen["make"] = vehicle.get("make")
            return [{"part_name": "IMS bearing"}]

        found = find_listings(
            "IMS bearing",
            make="Porsche",
            ebay=EbayNotConfigured("eBay keys are not set."),
            old_cars=lambda _text: [],
            get=get_status,
            pause=lambda _seconds: None,
            read=read,
        )
        reasons = {row["source"]: row["reason"] for row in found["skipped"]}
        self.assertIn("craigslist", reasons)
        self.assertIn("rockauto", reasons)
        self.assertTrue(any(row["source"] == "rennlist" for row in found["listings"]))
        self.assertIn("pelicanparts.com", seen["raw"])
        self.assertNotIn("bimmerforums.com", seen["raw"])
        self.assertEqual(seen["make"], "Porsche")
        self.assertEqual(found["reader"][0]["part_name"], "IMS bearing")

    def test_a_reader_failure_keeps_the_listings(self):
        def get_status(url, **_kwargs):
            class Response:
                status_code = 200
                text = FEED
                headers = {}

            return Response()

        def read(_raw, _vehicle):
            raise RuntimeError("reader down")

        found = find_listings(
            "IMS bearing",
            ebay=EbayNotConfigured("eBay keys are not set."),
            old_cars=lambda _text: [],
            get=get_status,
            pause=lambda _seconds: None,
            read=read,
        )
        self.assertTrue(found["listings"])
        self.assertIsNone(found["reader"])

    def test_a_refused_city_does_not_hide_the_next_city_or_reddit(self):
        sale = """<?xml version="1.0"?>
<rss><channel>
<item>
  <title>[FS] IMS bearing</title>
  <link>https://chicago.craigslist.org/pta/1</link>
  <description>996 bearing, cash only.</description>
</item>
</channel></rss>
"""
        reddit = """{"data":{"children":[
          {"data":{"title":"[WTS] IMS bearing","permalink":"/r/Porsche/comments/1","selftext":"Fits a 996."}},
          {"data":{"title":"IMS bearing discussion","permalink":"/r/Porsche/comments/2","selftext":"What tool?"}}
        ]}}"""

        def get_status(url, **_kwargs):
            class Response:
                headers = {}

                def __init__(self, status, body):
                    self.status_code = status
                    self.text = body

            if "sfbay.craigslist" in url:
                return Response(403, "")
            if "chicago.craigslist" in url:
                return Response(200, sale)
            if "/r/Porsche/search.json" in url:
                return Response(200, reddit)
            if "reddit.com" in url:
                return Response(200, '{"data":{"children":[]}}')
            return Response(200, FEED)

        found = find_listings(
            "IMS bearing",
            make="Porsche",
            ebay=EbayNotConfigured("eBay keys are not set."),
            old_cars=lambda _text: [],
            get=get_status,
            pause=lambda _seconds: None,
            read=lambda _raw, _vehicle: None,
        )
        cities = [row.get("city") for row in found["listings"] if row["source"] == "craigslist"]
        subs = [row.get("subreddit") for row in found["listings"] if row["source"] == "reddit"]
        self.assertIn("chicago", cities)
        self.assertNotIn("sfbay", cities)
        self.assertIn("Porsche", subs)
        self.assertEqual(len(subs), 1)
        self.assertEqual(reddit_posts(reddit, "Porsche")[0]["title"], "[WTS] IMS bearing")

    def test_a_brave_refusal_stops_the_remaining_forums(self):
        page = (
            '<a href="https://rennlist.com/forums/market/1">'
            "FS brake caliper for a 911</a>"
        )
        called = []

        def get_status(url, **_kwargs):
            called.append(url)

            class Response:
                headers = {}

                def __init__(self, status, body):
                    self.status_code = status
                    self.text = body

            if "craigslist" in url:
                return Response(403, "")
            if "reddit.com" in url:
                return Response(200, '{"data":{"children":[]}}')
            if "search.brave.com" in url and "rennlist.com" in url:
                return Response(200, page)
            if "search.brave.com" in url:
                return Response(429, "")
            return Response(200, FEED)

        found = find_listings(
            "brake caliper",
            make="Porsche",
            ebay=EbayNotConfigured("eBay keys are not set."),
            old_cars=lambda _text: [],
            get=get_status,
            pause=lambda _seconds: None,
            read=lambda _raw, _vehicle: None,
        )
        brave = [url for url in called if "search.brave.com" in url]
        self.assertEqual(len(brave), 2)
        self.assertIn("rennlist.com", brave[0])
        self.assertTrue(any(row.get("site") == "rennlist.com" for row in found["listings"]))
        self.assertTrue(any(row["source"] == "brave" for row in found["skipped"]))
        self.assertNotIn("bimmerforums.com", " ".join(brave))
        self.assertNotIn("car-part.com", " ".join(brave))

    def test_ebay_returns_up_to_twenty_listings(self):
        class Ebay:
            def search_parts(self, _query, limit=10):
                self.limit = limit
                return [
                    {"title": f"brake caliper {index}", "itemWebUrl": f"https://www.ebay.com/itm/{index}"}
                    for index in range(limit)
                ]

        ebay = Ebay()

        def get_status(url, **_kwargs):
            class Response:
                status_code = 200
                text = ""
                headers = {}

            if "search.brave.com" in url or "reddit.com" in url or "craigslist" in url:
                return Response()
            return Response()

        found = find_listings(
            "brake caliper",
            make="Porsche",
            model="911",
            year="2015",
            ebay=ebay,
            old_cars=lambda _text: [],
            get=get_status,
            pause=lambda _seconds: None,
            read=lambda _raw, _vehicle: None,
        )
        ebay_rows = [row for row in found["listings"] if row["source"] == "ebay"]
        self.assertEqual(ebay.limit, 20)
        self.assertEqual(len(ebay_rows), 20)

    def test_shared_sites_run_for_a_make_that_fills_the_forum_cap(self):
        called = []

        def get_status(url, **_kwargs):
            called.append(url)

            class Response:
                headers = {}

                def __init__(self, body):
                    self.status_code = 200
                    self.text = body

            if "site%3Acar-part.com" in url:
                return Response(
                    '<a href="https://www.car-part.com/caliper">brake caliper for sale</a>'
                )
            return Response("")

        found = find_listings(
            "brake caliper",
            make="Porsche",
            ebay=EbayNotConfigured("eBay keys are not set."),
            old_cars=lambda _text: [],
            get=get_status,
            pause=lambda _seconds: None,
            read=lambda _raw, _vehicle: None,
        )
        brave = [url for url in called if "search.brave.com" in url]
        for domain in ("car-part.com", "row52.com", "hemmings.com", "bringatrailer.com"):
            self.assertTrue(any(domain in url for url in brave), domain)
        self.assertTrue(any(row.get("site") == "car-part.com" for row in found["listings"]))
        self.assertLessEqual(sum("rennlist.com" in url or "pelicanparts.com" in url or "planet-9.com" in url or "flatsixes.com" in url for url in brave), 4)
