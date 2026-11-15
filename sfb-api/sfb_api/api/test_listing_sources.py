import os
import unittest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "sfb_api.settings")

import django

django.setup()

from sfb_api.api.assessment import part_number_of
from sfb_api.api.ebay_client import EbayBrowseClient, EbayNotConfigured, clear_item_cache, listing_facts
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
            {"facebook", "lkq", "carpart", "row52"},
        )
        self.assertNotIn("carpart", ids)
        self.assertNotIn("row52", ids)

    def test_the_named_sites_are_available(self):
        ids = {row["id"] for row in accessible_sources()}
        for source_id in (
            "ebay", "oldcarsdata", "rockauto", "craigslist", "reddit", "rennlist",
            "thesamba", "pelicanparts", "bimmerforums", "ferrarichat",
            "jaglovers", "landroverforums", "hemmings", "bringatrailer",
            "forabodiesonly", "grassrootsmotorsports",
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
        self.assertEqual(found["reader"][0]["link"], found["listings"][0]["url"])
        self.assertEqual(found["reader"][0]["part_name"], found["listings"][0]["title"])

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

    def test_a_source_price_survives_a_blank_reader_price(self):
        facts = listing_facts({
            "itemId": "v1|99|0",
            "price": {"value": "42.00", "currency": "USD"},
            "shippingOptions": [
                {"shippingCostType": "CALCULATED"},
                {"shippingCostType": "FIXED", "shippingCost": {"value": "8.50", "currency": "USD"}},
            ],
            "product": {
                "mpns": ["99906705309"],
                "epid": "123",
                "aspects": [{"localizedName": "Interchange Part Number", "localizedValues": ["9A700758500"]}],
            },
        })
        self.assertEqual(facts["price_amount"], "42.00")
        self.assertIsNone(listing_facts({})["price_amount"])
        self.assertIsNone(listing_facts({"shippingOptions": [{"shippingCostType": "CALCULATED"}]})["shipping_amount"])

        class Ebay:
            def search_parts(self, _query, limit=10):
                return [{
                    "title": "Genuine Porsche caliper bolt",
                    "itemWebUrl": "https://www.ebay.com/itm/99",
                    "itemId": facts["item_id"],
                    "price": {"value": facts["price_amount"], "currency": "USD"},
                    "shippingOptions": [{"shippingCost": {"value": "8.50", "currency": "USD"}}],
                    "product": {"mpns": ["99906705309"], "epid": "123"},
                }]

        def read(_raw, _vehicle):
            return [{
                "part_name": "Caliper bolt",
                "oem_number": "Not listed",
                "price": "Not listed",
                "condition": "Used",
                "fitment_vehicles": ["Porsche 911"],
                "source": "ebay",
                "link": "https://www.ebay.com/itm/99",
            }]

        def get_status(url, **_kwargs):
            class Response:
                status_code = 200
                text = ""
                headers = {}

            return Response()

        found = find_listings(
            "caliper",
            make="Porsche",
            ebay=Ebay(),
            old_cars=lambda _text: [],
            get=get_status,
            pause=lambda _seconds: None,
            read=read,
        )
        row = found["reader"][0]
        self.assertEqual(row["price"], "42.00 USD")
        self.assertEqual(row["price_amount"], "42.00")
        self.assertEqual(row["shipping_amount"], "8.50")
        self.assertEqual(row["oem_number"], "99906705309")
        self.assertEqual(row["item_id"], "v1|99|0")
        stored = found["listings"][0]
        self.assertEqual(stored["price_amount"], "42.00")
        self.assertIsNotNone(stored["identifiers"])

    def test_shared_sites_run_for_a_make_that_fills_the_forum_cap(self):
        called = []

        def get_status(url, **_kwargs):
            called.append(url)

            class Response:
                headers = {}

                def __init__(self, body):
                    self.status_code = 200
                    self.text = body

            if "site%3Ahemmings.com" in url:
                return Response(
                    '<a href="https://www.hemmings.com/classifieds/caliper">brake caliper for sale</a>'
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
        for domain in ("hemmings.com", "bringatrailer.com"):
            self.assertTrue(any(domain in url for url in brave), domain)
        for domain in ("car-part.com", "row52.com"):
            self.assertFalse(any(domain in url for url in brave), domain)
        self.assertTrue(any(row.get("site") == "hemmings.com" for row in found["listings"]))
        self.assertLessEqual(sum("rennlist.com" in url or "pelicanparts.com" in url or "planet-9.com" in url or "flatsixes.com" in url for url in brave), 4)

    def test_item_specifics_supply_a_part_number_the_search_summary_omitted(self):
        facts = listing_facts({
            "itemId": "v1|1|0",
            "price": {"value": "10.00", "currency": "USD"},
            "localizedAspects": [
                {"name": "Brand", "value": "Porsche"},
                {"name": "Manufacturer Part Number", "value": "99906705309"},
            ],
        })
        self.assertEqual(facts["price_amount"], "10.00")
        self.assertEqual(
            facts["identifiers"],
            [{
                "kind": "mpn",
                "type": "mpn",
                "source": "seller",
                "value": "99906705309",
                "matches": True,
                "verified": False,
            }],
        )
        self.assertEqual(part_number_of({"identifiers": facts["identifiers"]}), "99906705309")

        class Ebay:
            def __init__(self):
                self.detail_calls = []

            def search_parts(self, _query, limit=10):
                return [{
                    "title": "Genuine Porsche caliper bolt",
                    "itemWebUrl": "https://www.ebay.com/itm/1",
                    "itemId": "v1|1|0",
                    "price": {"value": "10.00", "currency": "USD"},
                }]

            def get_item(self, item_id, timeout=6):
                self.detail_calls.append(item_id)
                return {
                    "price": {"value": "99.00", "currency": "USD"},
                    "localizedAspects": [
                        {"name": "Manufacturer Part Number", "value": "99906705309"},
                    ],
                }

        ebay = Ebay()
        found = find_listings(
            "brake caliper",
            make="Porsche",
            ebay=ebay,
            old_cars=lambda _text: [],
            get=lambda *_args, **_kwargs: type("Response", (), {"status_code": 200, "text": "", "headers": {}})(),
            pause=lambda _seconds: None,
            read=lambda _raw, _vehicle: None,
        )
        row = next(item for item in found["listings"] if item["source"] == "ebay")
        self.assertEqual(ebay.detail_calls, ["v1|1|0"])
        self.assertEqual(row["price_amount"], "10.00")
        self.assertEqual(row["identifiers"][0]["value"], "99906705309")
        self.assertEqual(row["identifiers"][0]["source"], "seller")
        self.assertEqual(row["identifiers"][0]["kind"], "mpn")
        self.assertTrue(row["identifiers"][0]["matches"])
        self.assertFalse(row["identifiers"][0]["verified"])
        self.assertEqual(part_number_of(row), "99906705309")
        self.assertEqual(found["reader"][0]["oem_number"], "Not listed")
        self.assertEqual(row["assessment"]["part_number"], "99906705309")
        self.assertEqual(row["assessment"]["market"]["status"], "conflict")
        self.assertIn("caliper bolt", row["assessment"]["summary"])
        self.assertIn("Seller manufacturer part number 99906705309, not verified.", row["assessment"]["summary"])
        self.assertEqual(row["price_amount"], "10.00")

    def test_a_search_returns_collected_listings_when_time_runs_out(self):
        called = {"read": 0, "detail": 0, "brave": 0}

        class Ebay:
            def search_parts(self, _query, limit=10):
                return [{
                    "title": "Genuine Porsche caliper bolt",
                    "itemWebUrl": "https://www.ebay.com/itm/1",
                    "itemId": "v1|1|0",
                    "price": {"value": "10.00", "currency": "USD"},
                }]

            def get_item(self, _item_id, timeout=6):
                called["detail"] += 1
                return {}

        def get_status(url, **_kwargs):
            if "search.brave.com" in url:
                called["brave"] += 1
            class Response:
                status_code = 200
                text = ""
                headers = {}
            return Response()

        def read(_raw, _vehicle):
            called["read"] += 1
            return [{"part_name": "from the reader", "link": "https://www.ebay.com/itm/1"}]

        found = find_listings(
            "brake caliper",
            make="Porsche",
            ebay=Ebay(),
            old_cars=lambda _text: [],
            get=get_status,
            pause=lambda _seconds: None,
            read=read,
            now=lambda: 0,
            budget_seconds=0,
        )
        self.assertEqual(called["detail"], 0)
        self.assertEqual(called["brave"], 0)
        self.assertEqual(called["read"], 0)
        self.assertEqual(found["listings"][0]["price_amount"], "10.00")
        self.assertEqual(found["reader"][0]["part_name"], "Genuine Porsche caliper bolt")
        self.assertTrue(any(row["source"] == "deadline" for row in found["skipped"]))

    def test_an_instruction_is_kept_and_excluded_from_matching(self):
        instruction = 'Use with 3/8" drive ratchet/socket handle'
        facts = listing_facts({
            "itemId": "v1|2|0",
            "price": {"value": "15.19", "currency": "USD"},
            "localizedAspects": [
                {"name": "Manufacturer Part Number", "value": instruction},
            ],
        })
        self.assertEqual(facts["identifiers"], [{
            "kind": "text",
            "type": "text",
            "source": "seller",
            "value": instruction,
            "matches": False,
            "verified": False,
        }])
        self.assertEqual(part_number_of({"identifiers": facts["identifiers"]}), "")

        class Ebay:
            def search_parts(self, _query, limit=10):
                return [{
                    "title": "Caliper wind back tool",
                    "itemWebUrl": "https://www.ebay.com/itm/2",
                    "itemId": "v1|2|0",
                    "price": {"value": "15.19", "currency": "USD"},
                }]

            def get_item(self, _item_id, timeout=6):
                return {
                    "price": {"value": "40.00", "currency": "USD"},
                    "localizedAspects": [
                        {"name": "Manufacturer Part Number", "value": instruction},
                    ],
                }

        found = find_listings(
            "brake caliper",
            make="Porsche",
            ebay=Ebay(),
            old_cars=lambda _text: [],
            get=lambda *_args, **_kwargs: type("Response", (), {"status_code": 200, "text": "", "headers": {}})(),
            pause=lambda _seconds: None,
            read=lambda _raw, _vehicle: None,
        )
        row = next(item for item in found["listings"] if item["source"] == "ebay")
        self.assertEqual(row["price_amount"], "15.19")
        self.assertEqual(row["identifiers"][0]["value"], instruction)
        self.assertFalse(row["identifiers"][0]["matches"])
        self.assertEqual(found["reader"][0]["oem_number"], "Not listed")
        self.assertIsNone(row["assessment"]["part_number"])
        self.assertEqual(row["assessment"]["market"]["status"], "conflict")
        self.assertIn("wind back tool", row["assessment"]["summary"])
        self.assertIn("Seller entered " + instruction, row["assessment"]["summary"])
        self.assertNotIn("no recent asking comparison", row["assessment"]["summary"])

    def test_item_details_continue_while_time_remains_and_stop_before_the_deadline(self):
        calls = []
        clock = {"t": 0}

        class Ebay:
            def search_parts(self, _query, limit=10):
                return [
                    {
                        "title": f"Caliper {index}",
                        "itemWebUrl": f"https://www.ebay.com/itm/{index}",
                        "itemId": f"v1|{index}|0",
                        "price": {"value": "10.00", "currency": "USD"},
                    }
                    for index in range(1, 7)
                ]

            def get_item(self, item_id, timeout=6):
                calls.append((item_id, timeout))
                clock["t"] += 3
                return {
                    "localizedAspects": [
                        {"name": "Manufacturer Part Number", "value": "15626K"},
                    ],
                }

        wide = find_listings(
            "brake caliper",
            make="Porsche",
            ebay=Ebay(),
            old_cars=lambda _text: [],
            get=lambda *_args, **_kwargs: type("Response", (), {"status_code": 200, "text": "", "headers": {}})(),
            pause=lambda _seconds: None,
            read=lambda _raw, _vehicle: None,
            now=lambda: clock["t"],
            budget_seconds=55,
        )
        self.assertEqual(len(calls), 6)
        ebay_rows = [item for item in wide["listings"] if item["source"] == "ebay"]
        self.assertTrue(all(item["price_amount"] == "10.00" for item in ebay_rows))
        self.assertTrue(all(part_number_of(item) == "15626K" for item in ebay_rows))
        self.assertTrue(all(item["identifiers"][0]["verified"] is False for item in ebay_rows))
        self.assertTrue(all(item["identifiers"][0]["value"] == "15626K" for item in ebay_rows))

        calls.clear()
        clock["t"] = 0

        class SlowEbay(Ebay):
            def get_item(self, item_id, timeout=6):
                calls.append(item_id)
                clock["t"] += 20
                return {}

        tight = find_listings(
            "brake caliper",
            make="Porsche",
            ebay=SlowEbay(),
            old_cars=lambda _text: [],
            get=lambda *_args, **_kwargs: type("Response", (), {"status_code": 200, "text": "", "headers": {}})(),
            pause=lambda _seconds: None,
            read=lambda _raw, _vehicle: None,
            now=lambda: clock["t"],
            budget_seconds=55,
        )
        self.assertEqual(calls, ["v1|1|0", "v1|2|0"])
        self.assertEqual(len(tight["listings"]), 6)
        self.assertEqual(tight["reader"][0]["part_name"], "Caliper 1")
        self.assertTrue(any(row["source"] == "deadline" for row in tight["skipped"]))

    def test_cached_item_details_are_used_when_there_is_no_time_to_call(self):
        calls = []

        class Ebay:
            def search_parts(self, _query, limit=10):
                return [
                    {
                        "title": "Cached caliper bolt",
                        "itemWebUrl": "https://www.ebay.com/itm/cached",
                        "itemId": "v1|cached|0",
                        "price": {"value": "15.08", "currency": "USD"},
                    },
                    {
                        "title": "Uncached caliper bolt",
                        "itemWebUrl": "https://www.ebay.com/itm/new",
                        "itemId": "v1|new|0",
                        "price": {"value": "5.96", "currency": "USD"},
                    },
                ]

            def peek_item(self, item_id):
                if item_id == "v1|cached|0":
                    return {
                        "localizedAspects": [
                            {"name": "Manufacturer Part Number", "value": "99906705309"},
                        ],
                    }
                return None

            def get_item(self, item_id, timeout=6):
                calls.append(item_id)
                return {}

        found = find_listings(
            "brake caliper",
            make="Porsche",
            ebay=Ebay(),
            old_cars=lambda _text: [],
            get=lambda *_args, **_kwargs: type("Response", (), {"status_code": 200, "text": "", "headers": {}})(),
            pause=lambda _seconds: None,
            read=lambda _raw, _vehicle: None,
            now=lambda: 0,
            budget_seconds=0,
        )
        self.assertEqual(calls, [])
        cached = next(item for item in found["listings"] if item["item_id"] == "v1|cached|0")
        fresh = next(item for item in found["listings"] if item["item_id"] == "v1|new|0")
        self.assertEqual(cached["price_amount"], "15.08")
        self.assertEqual(cached["identifiers"][0]["value"], "99906705309")
        self.assertEqual(cached["identifiers"][0]["type"], "mpn")
        self.assertTrue(cached["identifiers"][0]["matches"])
        self.assertFalse(cached["identifiers"][0]["verified"])
        self.assertEqual(fresh.get("identifiers") or [], [])
        self.assertEqual(part_number_of(cached), "99906705309")
        self.assertEqual(cached["request_match"]["status"], "conflict")

    def test_a_second_item_read_uses_the_cache(self):
        clear_item_cache()
        client = EbayBrowseClient.__new__(EbayBrowseClient)
        client._token = "token"
        client._deadline = 10**12
        calls = []

        def _request(_url, params=None, timeout=15):
            calls.append(timeout)

            class Response:
                status_code = 200

                def raise_for_status(self):
                    return None

                def json(self):
                    return {
                        "itemId": "v1|cached|0",
                        "localizedAspects": [
                            {"name": "Manufacturer Part Number", "value": "8076"},
                        ],
                    }

            return Response()

        client._request = _request
        first = client.get_item("v1|cached|0", timeout=6)
        second = client.get_item("v1|cached|0", timeout=6)
        self.assertEqual(calls, [6])
        self.assertEqual(first, second)
        self.assertEqual(client.peek_item("v1|cached|0"), first)
        clear_item_cache()

    def test_a_seller_sku_stays_out_of_the_manufacturer_number(self):
        facts = listing_facts({
            "itemId": "v1|3|0",
            "localizedAspects": [
                {"name": "SKU", "value": "15626K"},
                {"name": "Manufacturer Part Number", "value": "99906705309"},
            ],
        })
        kinds = {item["kind"]: item for item in facts["identifiers"]}
        self.assertEqual(kinds["sku"]["value"], "15626K")
        self.assertFalse(kinds["sku"]["matches"])
        self.assertFalse(kinds["sku"]["verified"])
        self.assertEqual(kinds["mpn"]["source"], "seller")
        self.assertFalse(kinds["mpn"]["verified"])
        self.assertEqual(part_number_of({"identifiers": facts["identifiers"]}), "99906705309")

    def test_the_reader_product_can_reject_a_vague_title(self):
        class Ebay:
            def search_parts(self, _query, limit=10):
                return [{
                    "title": "Porsche item",
                    "itemWebUrl": "https://www.ebay.com/itm/4",
                    "itemId": "v1|4|0",
                    "price": {"value": "20.00", "currency": "USD"},
                    "product": {"mpns": ["ABC123"]},
                }]

        found = find_listings(
            "brake caliper",
            make="Porsche",
            model="911",
            year="2015",
            ebay=Ebay(),
            old_cars=lambda _text: [],
            get=lambda *_args, **_kwargs: type("Response", (), {"status_code": 200, "text": "", "headers": {}})(),
            pause=lambda _seconds: None,
            read=lambda _raw, _vehicle: [{
                "part_name": "brake caliper bolt",
                "offered": "brake caliper bolt",
                "link": "https://www.ebay.com/itm/4",
            }],
            confirm_fitment=lambda _number, _vehicle: True,
        )
        row = found["listings"][0]
        self.assertEqual(row["request_match"]["status"], "conflict")
        self.assertIn("caliper bolt", row["assessment"]["summary"])
        self.assertNotEqual(row["assessment"]["market"]["status"], "limited")
