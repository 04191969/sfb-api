import os
import unittest
from datetime import date, timedelta

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "sfb_api.settings")

import django

django.setup()

from sfb_api.api.assessment import assess_listing, same_part
from sfb_api.api.evidence_store import MemoryEvidence, evidence_row
from sfb_api.api.listing_find import find_listings

TODAY = date(2026, 11, 15)


def asking(listing_id, part, price, days=0, **extra):
    observed = datetime_on(TODAY - timedelta(days=days))
    row = {
        "source": "ebay",
        "listing_id": listing_id,
        "part_number": part,
        "price_amount": price,
        "price_currency": "USD",
        "status": "asking",
        "observed_at": observed,
    }
    row.update(extra)
    return row


def datetime_on(day):
    return f"{day.isoformat()}T00:00:00+00:00"


class AssessmentTests(unittest.TestCase):
    def test_one_comparison_is_limited_and_a_wide_spread_still_shows(self):
        subject = asking("self", "ABC123", "50.00")
        one = assess_listing(subject, [subject, asking("other", "ABC123", "42.00", days=2)], today=TODAY)
        self.assertEqual(one["market"]["status"], "limited")
        self.assertEqual(one["market"]["count"], 1)
        self.assertEqual(one["market"]["reference_price"], "42.00")
        self.assertIsNone(one["market"]["spread"])
        self.assertIn("does not show whether prices are consistent", one["market"]["limitation"])

        wide = assess_listing(
            subject,
            [subject, asking("low", "ABC123", "10.00"), asking("high", "ABC123", "100.00")],
            today=TODAY,
        )
        self.assertEqual(wide["market"]["status"], "median")
        self.assertEqual(wide["market"]["count"], 2)
        self.assertEqual(wide["market"]["median"], "55.00")
        self.assertEqual(wide["market"]["spread"], "90.00")

    def test_no_comparison_needs_data_and_sold_prices_stay_separate(self):
        subject = asking("self", "ABC123", "50.00")
        alone = assess_listing(subject, [subject], today=TODAY)
        self.assertEqual(alone["market"]["status"], "needs_data")
        self.assertNotIn("purchase", alone)

        sold = asking("sale", "ABC123", "1.00")
        sold["status"] = "sold"
        sold["sold_at"] = datetime_on(TODAY - timedelta(days=10))
        mixed = assess_listing(subject, [subject, asking("ask", "ABC123", "40.00"), sold], today=TODAY)
        self.assertEqual(mixed["market"]["reference_price"], "40.00")
        self.assertEqual(mixed["sold"]["status"], "limited")
        self.assertEqual(mixed["sold"]["reference_price"], "1.00")

    def test_recency_part_identity_and_missing_costs(self):
        subject = asking("self", "ABC123", "50.00")
        stale = assess_listing(subject, [asking("old", "ABC123", "10.00", days=15)], today=TODAY)
        self.assertEqual(stale["market"]["status"], "needs_data")
        fresh = assess_listing(subject, [asking("edge", "ABC123", "10.00", days=14)], today=TODAY)
        self.assertEqual(fresh["market"]["status"], "limited")

        self.assertFalse(same_part(
            {"part_number": "AAA", "identifiers": [{"kind": "mpn", "value": "AAA"}]},
            {"part_number": "BBB", "identifiers": [{"kind": "gtin", "value": "AAA"}]},
            [],
        ))
        record = {
            "part_number": "AAA",
            "equivalent_part_number": "BBB",
            "configuration": "front",
            "side": "left",
            "assembly": "caliper",
        }
        bare = {"part_number": "AAA", "identifiers": [{"kind": "mpn", "value": "AAA"}]}
        qualified = dict(bare, configuration="front", side="left", assembly="caliper")
        other = {"part_number": "BBB"}
        self.assertFalse(same_part(bare, other, [record]))
        self.assertTrue(same_part(qualified, other, [record]))
        self.assertFalse(same_part(qualified, other, [{**record, "side": ""}]))

        costs = assess_listing(subject, [], today=TODAY)["costs"]
        self.assertEqual(costs["item_price"], "50.00")
        self.assertIsNone(costs["shipping"])
        self.assertIsNone(costs["amount_due"])
        self.assertIn("shipping", costs["missing"])
        self.assertIn("tax", costs["missing"])
        self.assertNotIn("import_costs", assess_listing(
            {**subject, "item_country": "US", "shipping_amount": "5", "shipping_currency": "USD", "tax_amount": "1", "tax_currency": "USD", "core_amount": "0", "core_refund": "0"},
            [],
            today=TODAY,
        )["costs"]["missing"])

    def test_the_same_listing_is_stored_once(self):
        store = MemoryEvidence()
        first = evidence_row({
            "source": "ebay",
            "item_id": "v1|1|0",
            "price_amount": "10.00",
            "price_currency": "USD",
            "identifiers": [{"kind": "mpn", "value": "ABC123"}],
        })
        second = evidence_row({
            "source": "ebay",
            "item_id": "v1|1|0",
            "price_amount": "12.00",
            "price_currency": "USD",
            "identifiers": [{"kind": "mpn", "value": "ABC123"}],
        })
        store.upsert([first, second])
        self.assertEqual(len(store.rows), 1)
        self.assertEqual(next(iter(store.rows.values()))["price_amount"], "12.00")

    def test_a_search_assesses_against_saved_evidence(self):
        store = MemoryEvidence()
        store.upsert([asking("earlier", "ABC123", "42.00")])

        class Ebay:
            def search_parts(self, _query, limit=10):
                return [{
                    "title": "Caliper bolt",
                    "itemWebUrl": "https://www.ebay.com/itm/9",
                    "itemId": "v1|9|0",
                    "price": {"value": "50.00", "currency": "USD"},
                    "shippingOptions": [{"shippingCostType": "CALCULATED"}],
                    "product": {"mpns": ["ABC123"]},
                    "itemLocation": {"country": "US"},
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
            read=lambda _raw, _vehicle: [{
                "part_name": "Caliper bolt",
                "price": "Not listed",
                "oem_number": "",
                "link": "https://www.ebay.com/itm/9",
                "source": "ebay",
            }],
            evidence=store,
            today=TODAY,
        )
        assessment = found["reader"][0]["assessment"]
        self.assertEqual(assessment["market"]["status"], "limited")
        self.assertEqual(assessment["market"]["reference_price"], "42.00")
        self.assertEqual(found["reader"][0]["price"], "50.00 USD")
        self.assertEqual(len(store.rows), 2)
        self.assertTrue(found["evidence_saved"])
