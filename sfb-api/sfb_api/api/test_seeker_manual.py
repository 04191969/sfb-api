import os
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "sfb_api.settings")

import django

django.setup()

from rest_framework.test import APIRequestFactory

from sfb_api.api.seeker_manual import (
    active_seeker_job,
    blank_manual_row,
    decide_manual_seeker,
    review_result,
)
from sfb_api.api.seeker_markets import assigned_component
from sfb_api.api.views import seeker_discovery, seeker_run

NOW = datetime(2026, 11, 19, 23, 0, tzinfo=timezone.utc)
ONLINE = (NOW - timedelta(seconds=10)).isoformat()
STALE = (NOW - timedelta(minutes=10)).isoformat()


def _worker(seen):
    return [{"worker_id": "seeker-30308", "seen_at": seen, "code_version": "split-bea3977-seeker-memory-1", "v2_version": "7dc98a6"}]


class AssignmentTests(unittest.TestCase):
    def test_verified_blank_job_belongs_to_seeker(self):
        row = blank_manual_row(NOW)
        self.assertEqual(row["search_query"], None)
        self.assertEqual(row["job_kind"], "consumer_search")
        self.assertEqual(row["trigger"], "user")
        self.assertIsNone(row["market_id"])
        self.assertEqual(assigned_component(row), "seeker")

    def test_typed_query_belongs_to_live_matches(self):
        self.assertEqual(
            assigned_component({"job_kind": "consumer_search", "search_query": "Fender", "market_id": None, "trigger": "user"}),
            "live_matches",
        )

    def test_market_job_stays_on_seeker(self):
        self.assertEqual(
            assigned_component({"job_kind": "market_discovery", "search_query": "Market discovery\nMarket: Fender", "market_id": "m1"}),
            "seeker",
        )


class DecisionTests(unittest.TestCase):
    def test_consumer_query_is_rejected_without_a_live_matches_row(self):
        decision = decide_manual_seeker("Fender", [], _worker(ONLINE), True, NOW)
        self.assertEqual(decision["action"], "reject_query")
        self.assertNotIn("row", decision)

    def test_offline_worker_does_not_create_a_job(self):
        decision = decide_manual_seeker(None, [], _worker(STALE), True, NOW)
        self.assertEqual(decision["action"], "offline")
        self.assertIn("not sent to Live Matches", decision["error"])

    def test_unknown_worker_does_not_create_a_job(self):
        decision = decide_manual_seeker("", [], [], True, NOW)
        self.assertEqual(decision["action"], "offline")
        self.assertIn("unknown", decision["error"])

    def test_online_worker_creates_the_verified_row(self):
        decision = decide_manual_seeker(None, [], _worker(ONLINE), True, NOW)
        self.assertEqual(decision["action"], "create")
        self.assertEqual(assigned_component(decision["row"]), "seeker")
        self.assertEqual(decision["worker"]["code_version"], "split-bea3977-seeker-memory-1")

    def test_active_seeker_job_is_reused(self):
        existing = {"id": "job-1", "status": "pending", "job_kind": "consumer_search", "trigger": "user", "market_id": None, "search_query": None, "requested_at": "2026-11-19T22:00:00+00:00"}
        decision = decide_manual_seeker(None, [existing], _worker(ONLINE), True, NOW)
        self.assertEqual(decision["action"], "existing")
        self.assertEqual(decision["run"]["id"], "job-1")

    def test_live_matches_job_does_not_block_seeker(self):
        live = {"id": "live-1", "status": "running", "job_kind": "consumer_search", "trigger": "user", "market_id": None, "search_query": "Fender", "requested_at": "2026-11-19T22:00:00+00:00"}
        decision = decide_manual_seeker(None, [live], _worker(ONLINE), True, NOW)
        self.assertEqual(decision["action"], "create")

    def test_paused_scheduled_job_does_not_block_a_manual_run(self):
        scheduled = {"id": "sched-1", "status": "pending", "job_kind": "market_discovery", "trigger": "schedule", "market_id": "m1", "search_query": "Market discovery", "requested_at": "2026-11-19T22:00:00+00:00"}
        self.assertIsNone(active_seeker_job([scheduled], paused=True))
        decision = decide_manual_seeker(None, [scheduled], _worker(ONLINE), True, NOW)
        self.assertEqual(decision["action"], "create")

    def test_running_seeker_job_blocks_another(self):
        running = {"id": "run-1", "status": "running", "job_kind": "consumer_search", "trigger": "user", "market_id": None, "search_query": None, "requested_at": "2026-11-19T22:00:00+00:00"}
        decision = decide_manual_seeker(None, [running], _worker(ONLINE), True, NOW)
        self.assertEqual(decision["run"]["id"], "run-1")

    def test_review_does_not_approve_or_introduce(self):
        review = review_result(
            {"match_id": "match-1", "result_summary": "V2 Send saved for review after 1 assessments; no introduction sent"},
            {"id": "match-1", "status": "pending_review", "match_decision": "no_send"},
        )
        self.assertEqual(review["status"], "pending_review")
        self.assertEqual(review["match_decision"], "no_send")
        self.assertFalse(review["introduction_sent"])
        self.assertIn("no introduction sent", review["result_summary"])


class _Query:
    def __init__(self, db, name):
        self.db = db
        self.name = name
        self.filters = []
        self.allowed = None
        self.insert_row = None

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def in_(self, key, values):
        self.allowed = (key, set(values))
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, _count):
        return self

    def insert(self, row):
        self.insert_row = dict(row)
        return self

    def execute(self):
        if self.insert_row is not None:
            saved = dict(self.insert_row)
            saved["id"] = "created-1"
            self.db.setdefault(self.name, []).append(saved)
            self.db["inserted"].append(saved)
            return SimpleNamespace(data=[saved])
        rows = []
        for row in self.db.get(self.name, []):
            if all(row.get(key) == value for key, value in self.filters) and (
                self.allowed is None or row.get(self.allowed[0]) in self.allowed[1]
            ):
                rows.append(row)
        return SimpleNamespace(data=rows)


class _Client:
    def __init__(self, db):
        self.db = db

    def table(self, name):
        return _Query(self.db, name)


class ViewTests(unittest.TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.db = {
            "inserted": [],
            "seeker_global_pause": [{"id": "seeker", "paused": True}],
            "sfb_component_workers": [{
                "component": "seeker",
                "worker_id": "seeker-30308",
                "seen_at": datetime.now(timezone.utc).isoformat(),
                "code_version": "split-bea3977-seeker-memory-1",
                "v2_version": "7dc98a6",
            }],
            "seeker_runs": [],
            "matches": [],
        }

    def _post(self, view, path, payload):
        request = self.factory.post(path, payload, format="json")
        with patch("sfb_api.api.views.get_supabase", return_value=_Client(self.db)):
            return view(request)

    def test_discovery_creates_one_blank_seeker_job(self):
        response = self._post(seeker_discovery, "/api/seeker/discovery/", {})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["component"], "seeker")
        self.assertIsNone(response.data["search_query"])
        self.assertEqual(response.data["trigger"], "user")
        self.assertEqual(response.data["job_kind"], "consumer_search")
        self.assertEqual(response.data["job_id"], "created-1")
        self.assertEqual(response.data["worker"]["status"], "online")
        self.assertEqual(len(self.db["inserted"]), 1)
        self.assertIsNone(self.db["inserted"][0]["search_query"])

    def test_discovery_rejects_a_consumer_query_without_inserting(self):
        response = self._post(seeker_discovery, "/api/seeker/discovery/", {"search_query": "Fender"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["component"], "seeker")
        self.assertEqual(self.db["inserted"], [])

    def test_blank_run_endpoint_uses_the_seeker_route(self):
        response = self._post(seeker_run, "/api/seeker/run/", {"search_query": "  "})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["component"], "seeker")
        self.assertIsNone(response.data["search_query"])

    def test_typed_run_endpoint_still_stores_the_consumer_query(self):
        response = self._post(seeker_run, "/api/seeker/run/", {"search_query": "Fender"})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.db["inserted"][0]["search_query"], "Fender")
        self.assertNotIn("job_kind", self.db["inserted"][0])

    def test_second_click_returns_the_active_job(self):
        self.db["seeker_runs"] = [{
            "id": "already",
            "status": "pending",
            "job_kind": "consumer_search",
            "trigger": "user",
            "market_id": None,
            "search_query": None,
            "requested_at": "2026-11-19T22:00:00+00:00",
            "result_summary": None,
            "match_id": None,
        }]
        response = self._post(seeker_discovery, "/api/seeker/discovery/", {})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["duplicate"])
        self.assertEqual(response.data["job_id"], "already")
        self.assertEqual(self.db["inserted"], [])

    def test_offline_worker_is_reported_and_no_job_is_created(self):
        self.db["sfb_component_workers"][0]["seen_at"] = STALE
        response = self._post(seeker_discovery, "/api/seeker/discovery/", {})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data["worker"]["status"], "offline")
        self.assertIn("not sent to Live Matches", response.data["error"])
        self.assertEqual(self.db["inserted"], [])


if __name__ == "__main__":
    unittest.main()
