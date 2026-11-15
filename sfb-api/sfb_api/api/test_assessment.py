import os
import unittest
from datetime import date, timedelta

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "sfb_api.settings")

import django

django.setup()

from sfb_api.api.assessment import assess_listing, part_number_of, same_part
from sfb_api.api.evidence_store import MemoryEvidence, evidence_row
from sfb_api.api.listing_find import find_listings
from sfb_api.api.request_match import classify_listing

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
        store.upsert([asking("earlier", "ABC123", "42.00", title="Front brake caliper")])

        class Ebay:
            def search_parts(self, _query, limit=10, sort=None):
                return [{
                    "title": "Front brake caliper",
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
            model="911",
            year="2015",
            variant="Turbo",
            brake_configuration="iron",
            axle="front",
            side="left",
            ebay=Ebay(),
            old_cars=lambda _text: [],
            get=get_status,
            pause=lambda _seconds: None,
            read=lambda _raw, _vehicle: [{
                "part_name": "Front brake caliper",
                "price": "Not listed",
                "oem_number": "",
                "link": "https://www.ebay.com/itm/9",
                "source": "ebay",
            }],
            evidence=store,
            today=TODAY,
            confirm_fitment=lambda number, _vehicle: number == "ABC123",
        )
        assessment = found["reader"][0]["assessment"]
        self.assertEqual(assessment["market"]["status"], "limited")
        self.assertEqual(assessment["market"]["reference_price"], "42.00")
        self.assertEqual(found["reader"][0]["price"], "50.00 USD")
        self.assertEqual(len(store.rows), 2)
        self.assertTrue(found["evidence_saved"])

    def test_related_products_are_not_the_requested_caliper(self):
        vehicle = {"year": "2015", "make": "Porsche", "model": "911"}
        samples = {
            "Disc Brake Caliper Repair Kit": "repair kit",
            "Disc Brake Caliper Piston": "caliper piston",
            "GENUINE PORSCHE Brake Caliper Bolt": "caliper bolt",
            "Car Rear Disc Brake Piston Caliper Wind Back Tool": "back tool",
            'Car Rear Disc Brake Piston Caliper Wind Back 3/8" Drive Tool': "3/8-inch drive tool",
        }
        for title, offered in samples.items():
            match = classify_listing("brake caliper", {"title": title}, vehicle)
            self.assertEqual(match["status"], "conflict", title)
            self.assertIn(offered, match["offered"])
        held = classify_listing("brake caliper", {"title": "Front Left Brake Caliper"}, vehicle)
        self.assertEqual(held["status"], "uncertain")
        self.assertIn("Held for clarification", held["reason"])
        self.assertIn("2015 Porsche 911", held["reason"])
        for title in ("", "Porsche item", "Brake Caliper Kit", "Complete Caliper Kit"):
            match = classify_listing("brake caliper", {"title": title}, vehicle)
            self.assertEqual(match["status"], "uncertain", title)
            self.assertNotIn("not the requested", match["reason"])
        kit = classify_listing("brake caliper", {"title": "Brake Caliper Kit"}, vehicle)
        self.assertIn("complete brake caliper or a kit", kit["reason"])
        fraction = classify_listing(
            "brake caliper",
            {"title": 'Wind Back 3/8" Drive Tool'},
            vehicle,
        )
        self.assertEqual(fraction["status"], "conflict")
        self.assertEqual(fraction["offered"], "3/8-inch drive tool")
        hardware = classify_listing("brake caliper", {"title": "Brake caliper hardware kit"}, vehicle)
        self.assertEqual(hardware["status"], "conflict")
        kits = classify_listing(
            "brake caliper",
            {"title": "2 pc Centric Front Brake Caliper Repair Kits for 2014-2019 Porsche 911"},
            vehicle,
        )
        self.assertEqual(kits["status"], "conflict")
        self.assertIn("repair kits", kits["offered"])
        self.assertIn("variant, brake configuration, axle, and side", held["reason"])
        rebuild = classify_listing(
            "brake caliper",
            {"title": "Rear brake caliper rebuild kit for Porsche 911 GT3 2015-2020"},
            vehicle,
        )
        self.assertEqual(rebuild["status"], "conflict")
        self.assertIn("rebuild kit", rebuild["offered"])
        discussion = classify_listing(
            "brake caliper",
            {
                "title": "Under hood rubber grommets for brake-cooling ducts",
                "offered_name": "brake caliper",
                "source": "rennlist",
            },
            vehicle,
        )
        self.assertEqual(discussion["status"], "uncertain")
        self.assertEqual(discussion["offered"], "")
        self.assertIn("does not establish what is offered", discussion["reason"])
        sale = classify_listing(
            "brake caliper",
            {"title": "Porsche Brake Caliper Blow Out Sale", "source": "rennlist"},
            vehicle,
        )
        self.assertEqual(sale["status"], "uncertain")
        self.assertEqual(sale["offered"], "brake caliper")
        forum_sale = evidence_row({
            "source": "rennlist",
            "url": "https://rennlist.com/forums/market/sale",
            "title": "Porsche Brake Caliper Blow Out Sale",
            "status": "sold",
        })
        self.assertEqual(forum_sale["status"], "asking")
        self.assertIsNone(forum_sale["sold_at"])

    def test_supported_interchange_establishes_fitment_without_catalog(self):
        from sfb_api.api.evidence_store import add_assessment

        claim = {
            "kind": "mpn", "type": "mpn", "source": "seller",
            "value": "BBB", "matches": True, "verified": False,
        }
        record = {
            "part_number": "AAA",
            "equivalent_part_number": "BBB",
            "configuration": "front",
            "side": "left",
            "assembly": "caliper",
            "variant": "Turbo",
            "brake_configuration": "iron",
        }
        qualifiers = {"configuration": "front", "side": "left", "assembly": "caliper"}

        def listing(item_id, title, price, **extra):
            row = {
                "source": "ebay",
                "item_id": item_id,
                "url": f"https://www.ebay.com/itm/{item_id}",
                "title": title,
                "price_amount": price,
                "price_currency": "USD",
                "identifiers": [dict(claim)],
            }
            row.update(extra)
            return row

        request = {
            "part_name": "brake caliper",
            "year": "2015",
            "make": "Porsche",
            "model": "911",
            "variant": "Turbo",
            "brake_configuration": "iron",
            "axle": "front",
            "side": "left",
        }
        found = {
            "request": request,
            "listings": [
                listing("1", "Front left brake caliper", "80.00", **qualifiers),
                listing("2", "Front left brake caliper", "100.00", **qualifiers),
            ],
            "reader": [],
        }
        add_assessment(
            found,
            evidence=MemoryEvidence(interchange=[record]),
            confirm_fitment=lambda *_args: False,
            today=TODAY,
        )
        first = found["listings"][0]
        self.assertEqual(first["request_match"]["status"], "qualifying")
        self.assertEqual(first["request_match"]["fitment"], "interchange")
        self.assertFalse(first["identifiers"][0]["verified"])
        self.assertEqual(first["assessment"]["market"]["status"], "limited")
        self.assertEqual(first["assessment"]["market"]["reference_price"], "100.00")
        self.assertIn("not verified", first["assessment"]["summary"])

        bare = {
            "request": request,
            "listings": [listing("3", "Front left brake caliper", "80.00")],
            "reader": [],
        }
        add_assessment(
            bare,
            evidence=MemoryEvidence(interchange=[record]),
            confirm_fitment=lambda *_args: False,
            today=TODAY,
        )
        self.assertEqual(bare["listings"][0]["request_match"]["status"], "uncertain")
        self.assertIn("fitment", bare["listings"][0]["request_match"]["reason"])

        bolt = {
            "request": request,
            "listings": [listing("4", "Brake caliper bolt", "15.00", **qualifiers)],
            "reader": [],
        }
        add_assessment(
            bolt,
            evidence=MemoryEvidence(interchange=[record]),
            confirm_fitment=lambda *_args: False,
            today=TODAY,
        )
        self.assertEqual(bolt["listings"][0]["request_match"]["status"], "conflict")

        coarse = {
            "request": {
                "part_name": "brake caliper",
                "year": "2015",
                "make": "Porsche",
                "model": "911",
            },
            "listings": [
                listing("5", "Front left brake caliper", "80.00", **qualifiers),
                listing("6", "Front left brake caliper", "100.00", **qualifiers),
            ],
            "reader": [],
        }
        add_assessment(
            coarse,
            evidence=MemoryEvidence(interchange=[record]),
            confirm_fitment=lambda *_args: True,
            today=TODAY,
        )
        self.assertEqual(coarse["listings"][0]["request_match"]["status"], "uncertain")
        self.assertIn("does not specify", coarse["listings"][0]["request_match"]["reason"])
        self.assertNotIn(coarse["listings"][0]["assessment"]["market"]["status"], ("limited", "median"))

        other_variant = dict(request)
        other_variant["variant"] = "GT3"
        other_variant["axle"] = "rear"
        mismatched = {
            "request": other_variant,
            "listings": [listing("7", "Front left brake caliper", "80.00", **qualifiers)],
            "reader": [],
        }
        add_assessment(
            mismatched,
            evidence=MemoryEvidence(interchange=[record]),
            confirm_fitment=lambda *_args: True,
            today=TODAY,
        )
        self.assertEqual(mismatched["listings"][0]["request_match"]["status"], "uncertain")
        self.assertNotIn(mismatched["listings"][0]["assessment"]["market"]["status"], ("limited", "median"))

        unknown = dict(request)
        unknown["variant"] = "unknown"
        unresolved = {
            "request": unknown,
            "listings": [listing("8", "Front left brake caliper", "80.00", **qualifiers)],
            "reader": [],
        }
        add_assessment(
            unresolved,
            evidence=MemoryEvidence(interchange=[record]),
            confirm_fitment=lambda *_args: True,
            today=TODAY,
        )
        reason = unresolved["listings"][0]["request_match"]["reason"]
        self.assertEqual(unresolved["listings"][0]["request_match"]["status"], "uncertain")
        self.assertIn("Variant remains unresolved", reason)
        self.assertNotIn(unresolved["listings"][0]["assessment"]["market"]["status"], ("limited", "median"))

    def test_structured_fitment_confirms_the_caliper_and_holds_previous_numbers(self):
        from sfb_api.api.evidence_store import add_assessment, catalog_confirms
        from sfb_api.api.sourced_fitment import oem_numbers_for_request, previous_numbers

        request = {
            "part_name": "brake caliper",
            "year": "2015",
            "make": "Porsche",
            "model": "911",
            "variant": "Turbo",
            "brake_configuration": "iron",
            "axle": "front",
            "side": "left",
        }
        self.assertEqual(oem_numbers_for_request(request), ["9P1615123L"])
        self.assertTrue(catalog_confirms("9P1615123L", request))
        self.assertFalse(catalog_confirms("99135142713", request))
        self.assertFalse(catalog_confirms("99135142712", request))
        previous = previous_numbers("9P1615123L")
        self.assertEqual(
            [item["number"] for item in previous],
            ["99135142714", "99135142713", "99135142712"],
        )
        self.assertTrue(all(item["replacement_confirmed"] is False for item in previous))
        for number in ("99135142714", "99135142713", "99135142712"):
            self.assertNotIn(number, oem_numbers_for_request(request))
        other = dict(request)
        other["variant"] = "Turbo S"
        self.assertEqual(oem_numbers_for_request(other), [])
        self.assertFalse(catalog_confirms("9P1615123L", other))
        ceramic = dict(request)
        ceramic["brake_configuration"] = "PCCB"
        self.assertFalse(catalog_confirms("9P1615123L", ceramic))

        claim = {
            "kind": "mpn", "type": "mpn", "source": "seller",
            "value": "9P1615123L", "matches": True, "verified": False,
        }
        found = {
            "request": request,
            "listings": [{
                "source": "ebay",
                "item_id": "caliper",
                "url": "https://www.ebay.com/itm/caliper",
                "title": "Porsche 911 Turbo front left brake caliper",
                "price_amount": "900.00",
                "price_currency": "USD",
                "identifiers": [dict(claim)],
                "configuration": "front",
                "side": "left",
            }],
            "reader": [],
        }
        add_assessment(found, evidence=MemoryEvidence(), confirm_fitment=None, today=TODAY)
        match = found["listings"][0]["request_match"]
        self.assertEqual(match["status"], "qualifying")
        self.assertEqual(match["fitment"], "catalog")
        self.assertTrue(found["listings"][0]["identifiers"][0]["verified"])
        self.assertEqual(found["listings"][0]["assessment"]["market"]["status"], "needs_data")
        self.assertEqual(found["offer_outcome"]["code"], "needs_comparison")
        self.assertIn(
            "not enough comparison evidence",
            found["listings"][0]["assessment"]["summary"],
        )
        self.assertNotIn("No matching offer", found["listings"][0]["assessment"]["summary"])

        old_number = dict(claim)
        old_number["value"] = "99135142713"
        previous_offer = {
            "request": request,
            "listings": [{
                "source": "ebay",
                "item_id": "old",
                "url": "https://www.ebay.com/itm/old",
                "title": "Porsche 911 Turbo front left brake caliper",
                "price_amount": "400.00",
                "price_currency": "USD",
                "identifiers": [old_number],
                "configuration": "front",
                "side": "left",
            }],
            "reader": [],
        }
        add_assessment(previous_offer, evidence=MemoryEvidence(), today=TODAY)
        self.assertEqual(previous_offer["listings"][0]["request_match"]["status"], "uncertain")
        self.assertNotEqual(
            previous_offer["listings"][0]["assessment"]["market"]["status"],
            "limited",
        )
        self.assertEqual(previous_offer["offer_outcome"]["code"], "no_matching_offer")
        self.assertEqual(previous_offer["offer_outcome"]["message"], "No matching offer found.")

    def test_a_bolt_with_the_same_number_does_not_set_the_caliper_price(self):
        caliper = {
            "source": "ebay",
            "item_id": "caliper",
            "title": "Front brake caliper",
            "price_amount": "80.00",
            "price_currency": "USD",
            "identifiers": [{
                "kind": "mpn", "type": "mpn", "source": "seller",
                "value": "ABC123", "matches": True, "verified": False,
            }],
            "request_match": {"status": "qualifying", "reason": "", "offered": "brake caliper", "fitment": "established"},
        }
        bolt = asking("bolt", "ABC123", "15.00", title="Brake caliper bolt")
        result = assess_listing(caliper, [caliper, bolt], today=TODAY, qualifying_keys={("ebay", "caliper")})
        self.assertEqual(result["market"]["status"], "needs_data")
        self.assertIsNone(result["market"]["reference_price"])

        bolt["identifiers"] = [dict(item) for item in caliper["identifiers"]]
        bolt["request_match"] = classify_listing("brake caliper", bolt)
        rejected = assess_listing(bolt, [caliper, bolt], today=TODAY, qualifying_keys={("ebay", "caliper")})
        self.assertEqual(rejected["market"]["status"], "conflict")
        self.assertEqual(rejected["part_number"], "ABC123")
        self.assertIn("not verified", rejected["summary"])

    def test_unconfirmed_fitment_is_held_and_confirmed_offers_are_compared(self):
        claim = [{
            "kind": "mpn", "type": "mpn", "source": "seller",
            "value": "ABC123", "matches": True, "verified": False,
        }]
        first = {
            "source": "ebay", "item_id": "one", "title": "Front brake caliper",
            "price_amount": "80.00", "price_currency": "USD", "identifiers": claim,
        }
        second = {
            "source": "ebay", "item_id": "two", "title": "Front brake caliper",
            "price_amount": "100.00", "price_currency": "USD",
            "identifiers": [dict(item) for item in claim],
        }
        vehicle = {"year": "2015", "make": "Porsche", "model": "911", "part_name": "brake caliper"}
        for item in (first, second):
            item["request_match"] = classify_listing("brake caliper", item, vehicle, fitment_established=False)
        held = assess_listing(first, [first, second], today=TODAY, qualifying_keys=set())
        self.assertEqual(held["market"]["status"], "uncertain")
        self.assertIn("Held for clarification", held["summary"])
        self.assertEqual(part_number_of(first), "ABC123")

        keys = {("ebay", "one"), ("ebay", "two")}
        compared_subject = dict(first)
        compared_subject["request_match"] = classify_listing("brake caliper", first, vehicle, True)
        compared = assess_listing(compared_subject, [
            asking("one", "ABC123", "80.00"),
            asking("two", "ABC123", "100.00"),
        ], today=TODAY, qualifying_keys=keys)
        self.assertEqual(compared["market"]["status"], "limited")
        self.assertEqual(compared["market"]["reference_price"], "100.00")
