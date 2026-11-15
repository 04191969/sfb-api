import os
import unittest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "sfb_api.settings")

import django

django.setup()

from sfb_api.api.catalog_fetch import CatalogBlocked, CatalogMissing, fetch_catalog
from sfb_api.api.catalog_parse import (
    guess_category,
    parse_design911,
    parse_fitment_text,
    parse_rockauto_listing,
    parse_showmetheparts,
    rockauto_category_links,
    rockauto_engine_links,
    rockauto_part_links,
)
from sfb_api.api.catalog_route import (
    SourceReport,
    accept_fitment,
    compact_catalog_text,
    confidence_label,
    expand_years,
    fetch_decision,
    keep_new_matches,
    mark_collected_parts,
    normalize_reader_parts,
    retry_wait,
)
from sfb_api.api.catalog_shape import fitment_vehicles
from sfb_api.api.ebay_client import build_query, listing_matches, price_filter, token_deadline, token_is_current

ROCKAUTO_TREE = """
<a class="navlabellink" href="/en/catalog/porsche,1987,911,3.2l+h6,1262797">3.2L H6</a>
<a class="navlabellink" href="/en/catalog/porsche,1987,911,3.2l+h6,1262797,brake+&amp;+wheel+hub">Brake &amp; Wheel Hub</a>
<a class="navlabellink" href="/en/catalog/porsche,1987,911,3.2l+h6,1262797,brake+&amp;+wheel+hub,brake+pad,1684">Brake Pad</a>
"""

ROCKAUTO_LISTING = """
<div id="listingcontainer[8]" class="listing-inner">
  <span class="listing-final-manufacturer">DYNAMIC FRICTION</span>
  <span class="listing-final-partnumber">1311060809</span>
</div>
"""

DESIGN = """
<div class="product-item">
  <h3 class="product-name"><a>Front Brake Pad Set</a></h3>
  <span class="sku">91135194903</span>
  <ul class="suitable-for"><li>1986 Porsche 944</li></ul>
</div>
"""

SHOWME = """
<div class="part-result">
  <span class="part-name">Water Pump</span>
  <span class="part-number">93010601105</span>
  <ul class="also-fits"><li>1987 Porsche 911</li></ul>
</div>
"""


class ParseTests(unittest.TestCase):
    def test_category_uses_the_part_words(self):
        self.assertEqual(guess_category("Front Brake Pad"), "Brakes")
        self.assertEqual(guess_category("Fastener kit"), "Other")

    def test_fitment_text_reads_year_make_model(self):
        self.assertEqual(parse_fitment_text("1986 Porsche 944"), [
            {"year": 1986, "make": "Porsche", "model": "944"},
        ])

    def test_fitment_range_is_capped(self):
        years = [row["year"] for row in parse_fitment_text("1980-1995 Porsche 911")]
        self.assertEqual(years, list(range(1980, 1988)))

    def test_rockauto_tree_separates_engine_category_and_part(self):
        engines = rockauto_engine_links(ROCKAUTO_TREE, "Porsche", 1987, "911")
        self.assertEqual(engines[0]["engine"], "3.2l h6")
        self.assertEqual(len(rockauto_category_links(ROCKAUTO_TREE)), 1)
        parts = rockauto_part_links(ROCKAUTO_TREE)
        self.assertEqual(parts[0]["part_name"], "Brake Pad")
        self.assertEqual(parts[0]["category"], "Brakes")

    def test_rockauto_listing_keeps_the_part_type_and_brand(self):
        part = parse_rockauto_listing(ROCKAUTO_LISTING, "Brake Pad", "Brakes")
        self.assertEqual(part["part_name"], "Brake Pad")
        self.assertEqual(part["brand"], "DYNAMIC FRICTION")
        self.assertIsNone(part["oem_part_number"])

    def test_design911_keeps_the_oem_and_other_cars(self):
        parts = parse_design911(DESIGN)
        self.assertEqual(parts[0]["oem_part_number"], "91135194903")
        self.assertEqual(parts[0]["also_fits"][0]["model"], "944")

    def test_showmetheparts_script_shell_has_no_parts(self):
        html = '<script id="microloader" src="Core/microloader.js"></script><body></body>'
        self.assertEqual(parse_showmetheparts(html), [])

    def test_showmetheparts_rows_are_read_when_present(self):
        parts = parse_showmetheparts(SHOWME)
        self.assertEqual(parts[0]["part_name"], "Water Pump")
        self.assertEqual(parts[0]["also_fits"][0]["year"], 1987)


class EbayQueryTests(unittest.TestCase):
    def test_oem_query_includes_the_make(self):
        query = build_query(
            {"part_name": "Brake Pad", "oem_part_number": "91135194903"},
            {"year": 1987, "make": "Porsche", "model": "911"},
        )
        self.assertEqual(query, "91135194903 Porsche")

    def test_name_query_includes_the_car(self):
        query = build_query(
            {"part_name": "Brake Pad"},
            {"year": 1987, "make": "Porsche", "model": "911"},
        )
        self.assertEqual(query, "Brake Pad 1987 Porsche 911")

    def test_relevance_accepts_oem_or_name_and_make(self):
        part = {"part_name": "Brake Pad", "oem_part_number": "91135194903"}
        vehicle = {"make": "Porsche", "model": "911"}
        self.assertTrue(listing_matches({"title": "Genuine 91135194903 pad"}, part, vehicle))
        self.assertTrue(listing_matches({"title": "Porsche Brake Pad set"}, part, vehicle))
        self.assertFalse(listing_matches({"title": "Honda civic seat"}, part, vehicle))

    def test_price_filter_uses_the_browse_syntax(self):
        self.assertEqual(price_filter(250), "price:[..250.0],priceCurrency:USD")
        self.assertIsNone(price_filter(None))


class RouteTests(unittest.TestCase):
    def test_confidence_tiers(self):
        self.assertEqual(confidence_label(1), "Confirmed")
        self.assertEqual(confidence_label(0.9), "Confirmed")
        self.assertEqual(confidence_label(0.85), "High")
        self.assertEqual(confidence_label(0.5), "Possible")
        self.assertIsNone(confidence_label(0.49))
        self.assertFalse(accept_fitment(0.4))

    def test_year_span_is_capped(self):
        self.assertEqual(expand_years(1980, 1995), list(range(1980, 1988)))

    def test_reader_drops_the_source_car_and_a_weak_fit(self):
        payload = {
            "parts": [{
                "part_name": "Water Pump",
                "category": "Cooling",
                "oem_numbers": ["93010601105"],
                "also_fits": [
                    {"year_start": 1987, "year_end": 1987, "make": "Porsche", "model": "911", "confidence": 1},
                    {"year_start": 1986, "year_end": 1986, "make": "Porsche", "model": "944", "confidence": 0.4},
                    {
                        "year_start": 1997,
                        "year_end": 1998,
                        "make": "Porsche",
                        "model": "Boxster",
                        "confidence": 0.85,
                        "qualifier": "M96 only",
                    },
                ],
            }]
        }
        parts = normalize_reader_parts(payload, {"year": 1987, "make": "Porsche", "model": "911"})
        years = [fit["year"] for fit in parts[0]["also_fits"]]
        self.assertEqual(years, [1997, 1998])
        self.assertEqual(parts[0]["oem_part_number"], "93010601105")
        self.assertEqual(parts[0]["also_fits"][0]["qualifier"], "M96 only")

    def test_matcher_skips_known_cars_and_low_scores(self):
        payload = {
            "potential_matches": [
                {"year_start": 1987, "year_end": 1987, "make": "Porsche", "model": "911", "confidence": 0.95, "reasoning": "same car"},
                {"year_start": 1986, "year_end": 1986, "make": "Porsche", "model": "944", "confidence": 0.3, "reasoning": "guess"},
                {"year_start": 1997, "year_end": 1997, "make": "Porsche", "model": "Boxster", "confidence": 0.8, "reasoning": "shared pump"},
            ]
        }
        kept = keep_new_matches(payload, [{"year": 1987, "make": "Porsche", "model": "911"}])
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["model"], "Boxster")
        self.assertEqual(kept[0]["match_method"], "matcher_cross_ref")

    def test_parsed_page_is_confirmed(self):
        marked = mark_collected_parts([{
            "part_name": "Brake Pad",
            "oem_part_number": "91135194903",
            "also_fits": [{"year": 1986, "make": "Porsche", "model": "944"}],
        }])
        self.assertEqual(marked[0]["match_method"], "exact_oem")
        self.assertEqual(marked[0]["also_fits"][0]["confidence"], 1.0)

    def test_excerpt_stays_within_the_reader_cap(self):
        text = compact_catalog_text([{"part_name": "Pad", "category": "Brakes"}], "https://example.test/pad")
        self.assertIn("Source: https://example.test/pad", text)
        self.assertLessEqual(len(text), 8000)

    def test_collection_loop_retries_once_then_stops(self):
        self.assertEqual(fetch_decision(429, 0), "retry")
        self.assertEqual(fetch_decision(429, 1), "stop")
        self.assertEqual(fetch_decision(403, 0), "stop")
        self.assertEqual(fetch_decision(404, 0), "skip")
        self.assertEqual(fetch_decision(200, 0), "ok")
        self.assertEqual(retry_wait("7200"), 60)

    def test_a_fetch_timeout_is_retried_once(self):
        calls = []

        def get(url, **kwargs):
            calls.append(url)
            if len(calls) == 1:
                raise __import__("httpx").ConnectError("timed out")
            return __import__("httpx").Response(200, text="<html>ok</html>")

        html = fetch_catalog("https://example.test/part", pause=lambda _seconds: None, get=get)
        self.assertEqual(html, "<html>ok</html>")
        self.assertEqual(len(calls), 2)

    def test_a_missing_page_is_skipped(self):
        def get(url, **kwargs):
            return __import__("httpx").Response(404, text="missing")

        with self.assertRaises(CatalogMissing):
            fetch_catalog("https://example.test/gone", pause=lambda _seconds: None, get=get)

    def test_token_refreshes_120_seconds_early(self):
        deadline = token_deadline(1_000, 7200)
        self.assertEqual(deadline, 1_000 + 7200 - 120)
        self.assertTrue(token_is_current(deadline - 1, deadline))
        self.assertFalse(token_is_current(deadline, deadline))

    def test_supplier_report_counts_new_and_existing(self):
        report = SourceReport("rockauto")
        report.usable = 2
        report.rejected = 1
        report.add_save({"part_created": True, "fitment_created": 1, "fitment_updated": 0})
        report.add_save({"part_created": False, "fitment_created": 0, "fitment_updated": 1})
        self.assertIn("2 usable records; 1 rejected", report.line())
        self.assertIn("new parts 1, existing parts 1", report.line())

    def test_low_confidence_fitment_is_hidden(self):
        rows = [
            {"part_id": "p", "vehicle_id": "a", "confidence": 0.4, "source": "matcher"},
            {"part_id": "p", "vehicle_id": "b", "confidence": 0.85, "source": "matcher", "match_method": "matcher_cross_ref"},
        ]
        vehicles = {
            "a": {"id": "a", "year": 1986, "make": "Porsche", "model": "944"},
            "b": {"id": "b", "year": 1997, "make": "Porsche", "model": "Boxster"},
        }
        listed = fitment_vehicles(rows, vehicles)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["confidence_label"], "High")


if __name__ == "__main__":
    unittest.main()
