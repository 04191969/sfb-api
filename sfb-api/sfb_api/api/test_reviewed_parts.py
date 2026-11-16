import os
import unittest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "sfb_api.settings")

import django

django.setup()

from sfb_api.api.ebay_client import EbayNotConfigured
from sfb_api.api.listing_find import find_listings
from sfb_api.api.reviewed_parts import (
    APPLICATIONS,
    cross_claims,
    discovery_for,
    matching_applications,
)

BARE = {
    "part_name": "fuel tank",
    "year": "1965",
    "make": "Ford",
    "model": "Mustang",
    "capacity_gallons": "16",
    "assembly": "bare fuel tank; no pump or sender",
    "installation": "stock replacement tank",
    "filler_neck": "not attached",
}


def _quiet_search(**details):
    class Response:
        status_code = 403
        text = ""
        headers = {}

    return find_listings(
        details.pop("part_name"),
        ebay=EbayNotConfigured("eBay keys are not set."),
        old_cars=lambda _text: [],
        get=lambda *_args, **_kwargs: Response(),
        pause=lambda _seconds: None,
        read=lambda *_args: [],
        **details,
    )


class ReviewedPartsTests(unittest.TestCase):
    def test_eight_conditional_applications_keep_separate_identities(self):
        self.assertEqual(len(APPLICATIONS), 8)
        self.assertEqual(len({row["review_id"] for row in APPLICATIONS}), 8)
        for row in APPLICATIONS:
            self.assertEqual(row["identifier_type"], "aftermarket_manufacturer_part_number")
            self.assertIsNone(row["oem_part_number"])
            self.assertFalse(row["price_eligible"])
            self.assertEqual(row["fitment"], "conditional")

    def test_a_bare_mustang_tank_matches_only_its_capacity_and_assembly(self):
        matched = matching_applications(BARE)
        self.assertEqual([row["part_number"] for row in matched], ["19-507"])
        self.assertEqual(matched[0]["brand"], "Holley Sniper EFI")

        other = dict(BARE)
        other["model"] = "Cougar"
        self.assertEqual(matching_applications(other), [])

        unknown = dict(BARE)
        unknown["capacity_gallons"] = "unknown"
        self.assertEqual(matching_applications(unknown), [])

        pumped = dict(BARE)
        pumped["assembly"] = "tank with fuel pump and sender"
        pumped["installation"] = "EFI conversion"
        pumped["fuel_pump_lph"] = "255"
        pumped["filler_neck"] = "not included; source requires existing/replacement neck"
        self.assertEqual([row["part_number"] for row in matching_applications(pumped)], ["19-102"])

        larger = dict(BARE)
        larger["year"] = "1969"
        larger["capacity_gallons"] = "22"
        self.assertEqual([row["part_number"] for row in matching_applications(larger)], ["19-519"])

    def test_the_camry_kit_stays_on_the_2015_physical_install(self):
        request = {
            "part_name": "dash kit",
            "year": "2015",
            "make": "Toyota",
            "model": "Camry",
            "assembly": "dash installation kit: trim panel, brackets, pocket and screws",
            "radio_format": "ISO DIN with pocket or ISO DDIN",
            "scope": "physical dash mounting only; wiring, antenna and steering-wheel interfaces separate",
        }
        matched = matching_applications(request)
        self.assertEqual([row["part_number"] for row in matched], ["99-8249"])
        later = dict(request)
        later["year"] = "2016"
        self.assertEqual(matching_applications(later), [])
        wiring = dict(request)
        wiring["part_name"] = "wiring harness"
        self.assertEqual(matching_applications(wiring), [])
        incomplete = dict(request)
        incomplete["scope"] = ""
        self.assertEqual(matching_applications(incomplete), [])

    def test_a_shorter_brand_name_is_not_the_manufacturer_brand(self):
        request = dict(BARE)
        request["brand"] = "Holley"
        self.assertEqual(matching_applications(request), [])

    def test_discovery_leads_do_not_establish_fitment_or_interchange(self):
        leads = discovery_for("ACV", "1513-01")
        self.assertEqual(len(leads), 1)
        self.assertFalse(leads[0]["fitment_eligible"])
        self.assertFalse(leads[0]["price_eligible"])
        self.assertIsNone(leads[0]["oem_part_number"])
        self.assertEqual(discovery_for("Other Brand", "1513-01"), [])
        self.assertEqual(discovery_for("", "1513-01"), [])

        moss = discovery_for("Moss Motors", "180-767")
        self.assertEqual(moss[0]["identifier_type"], "supplier_catalog_number")
        self.assertIsNone(moss[0]["oem_part_number"])
        self.assertFalse(moss[0]["fitment_eligible"])

        claims = cross_claims("Dorman", "576-078")
        self.assertTrue(claims)
        self.assertTrue(all(item["replacement_confirmed"] is False for item in claims))
        self.assertTrue(all(item["price_eligible"] is False for item in claims))
        self.assertEqual(discovery_for("GENERAL MOTORS", "978294"), [])

    def test_a_search_reports_the_conditional_application_without_a_price_comparison(self):
        found = _quiet_search(**BARE)
        self.assertEqual([row["part_number"] for row in found["applications"]], ["19-507"])
        self.assertFalse(found["applications"][0]["price_eligible"])
        self.assertIsNone(found["applications"][0]["oem_part_number"])
        self.assertEqual(found["offer_outcome"]["code"], "no_matching_offer")
        self.assertEqual(found["discovery"], [])

        labeled = _quiet_search(part_name="connector", brand="ACV", part_number="1513-01")
        self.assertEqual(labeled["applications"], [])
        self.assertFalse(labeled["discovery"][0]["fitment_eligible"])
        self.assertEqual(labeled["offer_outcome"]["code"], "no_matching_offer")
