import csv
import os
import unittest
from pathlib import Path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "sfb_api.settings")

import django

django.setup()

from sfb_api.api.billing import subscription_payload
from sfb_api.api.catalog_shape import (
    cascade,
    clean_tokens,
    cross_car_matches,
    dedupe_fitment,
    fitment_record,
    fitment_vehicles,
    group_parts,
    name_matches,
    normalize_extracted_parts,
    part_record,
    subscription_is_paid,
    vehicle_record,
)
from sfb_api.api.growth import AmbiguousVehicle

DOWNLOADS = Path.home() / "Downloads"


class CascadeTests(unittest.TestCase):
    def setUp(self):
        self.rows = [
            {"make": "Porsche", "model": "911", "year": 1974},
            {"make": "Porsche", "model": "911", "year": 1975},
            {"make": "Porsche", "model": "Boxster", "year": 1997},
            {"make": "BMW", "model": "M3", "year": 1995},
        ]

    def test_makes_are_sorted_and_unique(self):
        self.assertEqual(cascade(self.rows), {"makes": ["BMW", "Porsche"]})

    def test_models_keep_the_stored_spelling(self):
        result = cascade(self.rows, make="porsche")
        self.assertEqual(result["make"], "Porsche")
        self.assertEqual(result["models"], ["911", "Boxster"])

    def test_years_are_sorted(self):
        result = cascade(self.rows, make="Porsche", model="911")
        self.assertEqual(result["years"], [1974, 1975])


class FitmentShapeTests(unittest.TestCase):
    def test_parts_group_by_category(self):
        fitment = [
            {"part_id": "p1", "source": "design911", "confirmed_at": None},
            {"part_id": "p2", "source": "design911", "confirmed_at": None},
        ]
        parts = {
            "p1": {"id": "p1", "part_name": "Brake Caliper", "category": "Brakes"},
            "p2": {"id": "p2", "part_name": "Brake Pad", "category": "Brakes"},
        }
        categories, count = group_parts(fitment, parts)
        self.assertEqual(count, 2)
        self.assertEqual(
            [part["part_name"] for part in categories[0]["parts"]],
            ["Brake Caliper", "Brake Pad"],
        )

    def test_cross_car_omits_the_source_vehicle(self):
        parts = [{"id": "p1", "part_name": "Brake Caliper", "category": "Brakes"}]
        fitment = [
            {"part_id": "p1", "vehicle_id": "mine", "source": "design911"},
            {"part_id": "p1", "vehicle_id": "other", "source": "design911"},
        ]
        vehicles = {
            "mine": {"id": "mine", "year": 1974, "make": "Porsche", "model": "911"},
            "other": {"id": "other", "year": 1997, "make": "Porsche", "model": "Boxster"},
        }
        matches, count = cross_car_matches(parts, fitment, vehicles, "mine")
        self.assertEqual(count, 1)
        self.assertEqual(matches[0]["also_fits"][0]["model"], "Boxster")
        listed = fitment_vehicles(fitment, vehicles)
        self.assertEqual(len(listed), 2)

    def test_part_search_requires_every_token(self):
        self.assertTrue(name_matches("Front Brake Caliper", "brake caliper"))
        self.assertFalse(name_matches("Brake Pad", "brake caliper"))
        self.assertEqual(clean_tokens("brake+caliper"), ["brake", "caliper"])


class SeedTests(unittest.TestCase):
    def test_seed_counts_and_duplicate_pair(self):
        vehicles_path = DOWNLOADS / "vehicles.csv"
        parts_path = DOWNLOADS / "parts.csv"
        fitment_path = DOWNLOADS / "fitment.csv"
        if not vehicles_path.is_file():
            self.skipTest("seed CSVs are not in Downloads")

        with vehicles_path.open(newline="", encoding="utf-8-sig") as handle:
            vehicles = [vehicle_record(row) for row in csv.DictReader(handle)]
        with parts_path.open(newline="", encoding="utf-8-sig") as handle:
            parts = [part_record(row) for row in csv.DictReader(handle)]
        with fitment_path.open(newline="", encoding="utf-8-sig") as handle:
            fitment = [fitment_record(row) for row in csv.DictReader(handle)]

        self.assertEqual(len(vehicles), 1307)
        self.assertTrue(all(vehicles))
        self.assertEqual(len(parts), 190)
        self.assertTrue(all(parts))
        self.assertEqual(len(fitment), 225)
        kept, dropped = dedupe_fitment(fitment)
        self.assertEqual(dropped, 1)
        self.assertEqual(len(kept), 224)


class RouteTests(unittest.TestCase):
    def test_dropdowns_are_public_and_fitment_keeps_the_api_key(self):
        from django.urls import resolve
        from rest_framework.permissions import AllowAny

        from sfb_api.api import views_catalog
        from sfb_api.api.permissions import SfbApiKeyPermission

        self.assertIn(AllowAny, views_catalog.vehicle_list.cls.permission_classes)
        self.assertIn(AllowAny, views_catalog.stripe_webhook.cls.permission_classes)
        self.assertIn(SfbApiKeyPermission, views_catalog.fitment_for_part.cls.permission_classes)
        self.assertIn(SfbApiKeyPermission, views_catalog.cross_car.cls.permission_classes)
        self.assertIn(SfbApiKeyPermission, views_catalog.watch.cls.permission_classes)

        for path in (
            "/api/vehicles/",
            "/api/vehicles/search",
            "/api/parts/",
            "/api/parts/search",
            "/api/fitment/",
            "/api/fitment/cross-car",
            "/api/watch",
            "/api/catalog/grow/",
            "/api/billing/webhook/",
        ):
            resolve(path)


class GrowthAndBillingTests(unittest.TestCase):
    def test_reader_rows_drop_the_source_car_and_blank_names(self):
        parts = normalize_extracted_parts(
            [
                {"part_name": "  Brake Caliper ", "category": "Brakes", "oem_part_number": "99335142101"},
                {"part_name": ""},
                {
                    "part_name": "Control Arm",
                    "also_fits": [
                        {"year": 1974, "make": "Porsche", "model": "911"},
                        {"year": 1997, "make": "Porsche", "model": "Boxster"},
                        {"year": "nope", "make": "Porsche", "model": "928"},
                    ],
                },
            ],
            {"year": 1974, "make": "Porsche", "model": "911"},
        )
        self.assertEqual(len(parts), 2)
        self.assertEqual(parts[0]["oem_part_number"], "99335142101")
        self.assertEqual(parts[1]["also_fits"], [
            {"year": 1997, "make": "Porsche", "model": "Boxster", "engine": None},
        ])

    def test_paid_status_mapping(self):
        self.assertTrue(subscription_is_paid({"tier": "paid", "status": "trialing"}))
        self.assertFalse(subscription_is_paid({"tier": "paid", "status": "canceled"}))
        row = subscription_payload("user-1", "active", "cus_1", "sub_1")
        self.assertEqual(row["tier"], "paid")
        self.assertEqual(row["stripe_subscription_id"], "sub_1")

    def test_ambiguous_vehicle_keeps_the_options(self):
        options = [{"id": "a"}, {"id": "b"}]
        with self.assertRaises(AmbiguousVehicle) as caught:
            raise AmbiguousVehicle(options)
        self.assertEqual(caught.exception.vehicles, options)


if __name__ == "__main__":
    unittest.main()
