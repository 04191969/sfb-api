import os
import unittest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "sfb_api.settings")

import django

django.setup()

from sfb_api.api.ebay_client import EbayNotConfigured
from sfb_api.api.listing_find import find_listings
from sfb_api.api.listing_sources import (
    CL_CITIES,
    accessible_sources,
    bing_site_url,
    craigslist_city_url,
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
  <link>https://www.thesamba.com/vw/classifieds/996</link>
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
        query = bing_site_url("IMS bearing")
        self.assertIn("site%3Athesamba.com", query)
        self.assertIn("site%3Arow52.com", query)
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
        self.assertTrue(any(row["source"] == "thesamba" for row in found["listings"]))
        self.assertTrue(any(row["source"] == "rennlist" for row in found["listings"]))
        self.assertIn("thesamba", seen["raw"])
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
