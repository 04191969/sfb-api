import unittest
from pathlib import Path


def load_worker():
    path = Path(__file__).resolve().parents[3] / "worker" / "SFB_Seeker_v2_env.bat"
    lines = path.read_text(encoding="utf-8").splitlines()
    if lines and lines[0].startswith("@echo"):
        lines = lines[1:]
    namespace = {"__name__": "seeker_rules"}
    exec(compile("\n".join(lines), str(path), "exec"), namespace)
    return namespace


class Gate:
    def __init__(self, error=None, data=None):
        self.error = error
        self.data = data
        self.calls = []

    def rpc(self, name, args):
        self.calls.append(name)
        return self

    def execute(self):
        if self.error:
            raise self.error
        return self


class Query:
    def __init__(self, gate, table):
        self.gate = gate
        self.table = table
        self.filters = {}
        self.op = "select"

    def select(self, *_args, **_kwargs):
        self.op = "select"
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def update(self, row):
        self.op = "update"
        self.gate.updates.append((self.table, row))
        return self

    def execute(self):
        if self.op == "update":
            return Gate(data=[])
        return Gate(data=list(self.gate.rows.get(self.table, [])))


class Database:
    def __init__(self, rows):
        self.rows = rows
        self.updates = []

    def table(self, name):
        return Query(self, name)


class ReservationRulesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.worker = load_worker()

    def test_timeout_and_permission_do_not_fall_back(self):
        worker = self.worker
        timed_out = Gate(error=TimeoutError("The read operation timed out"))
        with self.assertRaises(worker["ReservationClosed"]) as caught:
            worker["_reserve_ai"](timed_out, True)
        self.assertEqual(caught.exception.reason, "AI reservation timed out")
        self.assertNotEqual(caught.exception.reason, "file")

        denied = Gate(error=Exception("permission denied for function reserve_seeker_ai"))
        with self.assertRaises(worker["ReservationClosed"]) as caught:
            worker["_reserve_ai"](denied, True)
        self.assertEqual(caught.exception.reason, "AI reservation was denied")

        with self.assertRaises(worker["ClaimClosed"]) as caught:
            worker["claim_next_run"](Gate(error=TimeoutError("canceling statement due to statement timeout")))
        self.assertEqual(caught.exception.reason, "job claim timed out")
        with self.assertRaises(worker["ClaimClosed"]) as caught:
            worker["claim_next_run"](Gate(error=Exception("42501 permission denied")))
        self.assertEqual(caught.exception.reason, "job claim was denied")

    def test_cap_stop_is_an_incomplete_reason(self):
        worker = self.worker
        with self.assertRaises(worker["ReservationClosed"]) as caught:
            worker["_reserve_ai"](Gate(data=None), True)
        summary = worker["search_outcome"]([{"qualifying": True}], caught.exception.reason)
        self.assertEqual(
            summary,
            "Found 1 qualifying match(es). Search incomplete — daily OpenAI cap of 40 reached",
        )
        self.assertNotIn("No qualifying match", summary)

    def test_a_missing_function_is_the_only_fallback(self):
        worker = self.worker
        missing = Gate(error=Exception("Could not find the function public.reserve_seeker_ai in the schema cache"))
        self.assertEqual(worker["_reserve_ai"](missing, True), ("file", None))
        self.assertEqual(
            worker["claim_next_run"](Gate(error=Exception("Could not find the function public.claim_next_seeker_run"))),
            "fallback",
        )


class MarketDiscoveryPathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.worker = load_worker()

    def _run(self, ai=None, blocked=False, listings=None, page=None, searches_enabled=True):
        worker = self.worker
        saved = []
        pairs = []
        searches = []
        evaluations = []
        offer = {
            "title": "Epiphone USA Map guitar",
            "text": "Epiphone USA Map guitar for sale",
            "url": "https://reverb.com/item/97393148-epiphone",
            "platform": "reverb",
            "price": 850,
            "shipping": 0,
            "availability": "available",
            "qualifying": True,
        }
        if listings is None:
            listings = [offer]
        if page is None:
            page = "<html><body>" + ("Buy it now. Add to cart. Epiphone USA Map guitar. " * 20) + "</body></html>"
        database = Database({
            "sections": [
                {"id": "market-1", "name": "Epiphone USA Map", "make": "Epiphone", "model": "USA Map"},
                {"id": "alias-9", "name": "Epiphone USA Map guitar", "make": "Epiphone", "model": "USA Map guitar"},
            ],
            "matches": [{
                "buyer_post_id": "buyer-1",
                "seller_listing_id": "seller-1",
                "section_id": "alias-9",
            }],
            "buyer_posts": [{
                "id": "buyer-1",
                "section_id": None,
                "username": "Existing buyer",
                "post_text": "Looking for an Epiphone USA Map guitar",
                "source_url": "https://sfb.example/request",
                "source_platform": "sfb",
            }],
            "seller_listings": [{
                "id": "seller-1",
                "section_id": None,
                "username": "Existing offer",
                "listing_text": "Found for sale\nEpiphone USA Map guitar",
                "source_url": "https://ebay.example/existing",
                "source_platform": "ebay",
                "price": 800,
                "shipping": 0,
                "availability": "available",
            }],
        })

        def search_wtb(query, _seen, max_results=15):
            searches.append(query)
            if not searches_enabled:
                return []
            if query.startswith('"Epiphone USA Map guitar"'):
                return [{
                    "title": "Buyer from offer",
                    "text": "WTB Epiphone USA Map guitar",
                    "url": "https://forum.example/from-offer",
                    "platform": "web",
                }]
            return [{
                "title": "Public WTB",
                "text": "WTB Epiphone USA Map",
                "url": "https://forum.example/wtb",
                "platform": "web",
            }]

        def remember(_sb, _table, url, row):
            saved.append((url, row))
            return "saved"

        def evaluate(*args, **_kwargs):
            evaluations.append(args[:2])
            if ai:
                return ai()
            if "Looking for an Epiphone USA Map guitar" in (args[0] or ""):
                return {"match": True, "confidence": 90, "reason": "same model", "make": "Epiphone", "model": "USA Map"}
            return {"match": False, "confidence": 10, "reason": "different request"}

        def push(_sb, buyer, seller, _result, section_id=None):
            pairs.append((buyer["url"], seller["url"], section_id))
            return "match-1", True

        worker["search_wtb_posts"] = search_wtb
        worker["search_listings"] = lambda *_args, **_kwargs: {"listings": listings, "incomplete": ""}
        worker["read_listing_html"] = lambda url: page
        worker["_save_source"] = remember
        worker["mark_seen"] = lambda *_args, **_kwargs: None
        worker["usage_blocked"] = lambda _sb: blocked
        worker["ai_evaluate_match"] = evaluate
        worker["push_match"] = push
        worker["run_market_discovery"](
            database, None, {},
            {
                "id": "run-1",
                "market_id": "market-1",
                "search_query": worker["market_scope_text"]("Epiphone USA Map", "market-1"),
            },
        )
        summary = database.updates[-1][1]["result_summary"]
        return saved, pairs, searches, summary, database, evaluations

    def test_existing_records_and_offer_buyer_search_are_used(self):
        saved, pairs, searches, summary, _database, _evaluations = self._run()
        saved_urls = {url for url, _row in saved}
        self.assertIn("https://forum.example/wtb", saved_urls)
        self.assertIn("https://forum.example/from-offer", saved_urls)
        self.assertIn("https://reverb.com/item/97393148-epiphone", saved_urls)
        self.assertTrue(all(row["section_id"] == "market-1" for _url, row in saved))
        self.assertFalse(any(str(row.get("post_text") or "").startswith("Market discovery\n") for _url, row in saved))
        self.assertTrue(any(query.startswith('"Epiphone USA Map guitar"') for query in searches))
        self.assertIn(
            ("https://sfb.example/request", "https://reverb.com/item/97393148-epiphone", "market-1"),
            pairs,
        )
        self.assertIn("qualifying match", summary)
        self.assertNotIn("Search incomplete", summary)

    def test_reservation_failure_records_the_reason(self):
        worker = self.worker

        def fail():
            raise worker["ReservationClosed"]("AI reservation timed out")

        _saved, pairs, _searches, summary, _database, _evaluations = self._run(ai=fail)
        self.assertEqual(pairs, [])
        self.assertEqual(summary, "Search incomplete — AI reservation timed out")

    def test_cap_stop_records_incomplete_after_saving_discoveries(self):
        saved, pairs, _searches, summary, _database, _evaluations = self._run(blocked=True)
        self.assertTrue(saved)
        self.assertEqual(pairs, [])
        self.assertEqual(summary, "Search incomplete — daily OpenAI cap of 40 reached")

    def test_legacy_record_without_section_id_follows_its_match(self):
        worker = self.worker
        sections = [
            {"id": "market-1", "make": "Epiphone", "model": "USA Map"},
            {"id": "alias-9", "make": "Epiphone", "model": "USA Map guitar"},
            {"id": "other-2", "make": "Ford", "model": "Mustang"},
        ]
        buyers = [
            {"id": "legacy", "section_id": None, "source_url": "https://forum.example/legacy", "username": "Legacy", "post_text": "WTB", "source_platform": "web"},
            {"id": "mixed", "section_id": None, "source_url": "https://forum.example/mixed", "username": "Mixed", "post_text": "WTB", "source_platform": "web"},
        ]
        matches = [
            {"buyer_post_id": "legacy", "section_id": "alias-9"},
            {"buyer_post_id": "mixed", "section_id": "alias-9"},
            {"buyer_post_id": "mixed", "section_id": "other-2"},
        ]
        database = Database({
            "sections": sections,
            "matches": matches,
            "buyer_posts": buyers,
            "seller_listings": [],
        })
        found_buyers, found_sellers = worker["load_market_records"](database, "market-1")
        self.assertEqual([row["url"] for row in found_buyers], ["https://forum.example/legacy"])
        self.assertEqual(found_sellers, [])
        filled = [row for table, row in database.updates if table == "buyer_posts"]
        self.assertEqual(filled, [{"section_id": "market-1"}])

    def test_previously_available_listing_that_ended_is_not_a_new_match(self):
        ended = "<html><body>" + ("This listing has ended. Epiphone USA Map guitar. " * 12) + "</body></html>"
        _saved, pairs, _searches, summary, database, evaluations = self._run(
            listings=[], searches_enabled=False, page=ended,
        )
        self.assertEqual(pairs, [])
        self.assertEqual(evaluations, [])
        self.assertEqual(summary, "No qualifying match found in the sources checked")
        observed = [
            row for table, row in database.updates
            if table == "seller_listings" and "availability" in row
        ]
        self.assertEqual(observed[-1]["availability"], "ended")

    def test_unreadable_stored_page_stays_unconfirmed(self):
        worker = self.worker
        stale = worker["stored_seller"]({
            "id": "seller-1",
            "availability": "available",
            "source_url": "https://shop.example/item/old",
            "username": "Old",
            "listing_text": "Found for sale",
        })
        self.assertFalse(stale["qualifying"])
        checked = worker["refresh_stored_offer"](stale, lambda url: "nope")
        self.assertEqual(checked["availability"], "unconfirmed")
        self.assertFalse(checked["qualifying"])

    def test_unknown_current_price_or_shipping_does_not_qualify(self):
        worker = self.worker
        stale = worker["stored_seller"]({
            "id": "seller-1",
            "availability": "available",
            "source_url": "https://shop.example/item/old",
            "username": "Epiphone USA Map guitar $400 + $25 shipping",
            "listing_text": "Found for sale\nEpiphone USA Map guitar $400 + $25 shipping",
            "price": 400,
            "shipping": 25,
        })
        available = "<html><body>" + (
            "Buy it now. Add to cart. Epiphone USA Map guitar in stock. " * 8
        ) + "</body></html>"
        no_price = worker["refresh_stored_offer"](stale, lambda url: available)
        self.assertIsNone(no_price["price"])
        self.assertEqual(no_price["previous_price"], 400)
        self.assertEqual(no_price["availability"], "available")
        self.assertFalse(worker["qualifies"](no_price, "Budget: 850 plus shipping — firm maximum"))
        with_price = "<html><body>" + (
            "Buy it now. Add to cart. New Price $800. Epiphone USA Map guitar. " * 8
        ) + "</body></html>"
        no_shipping = worker["refresh_stored_offer"](stale, lambda url: with_price)
        self.assertEqual(no_shipping["price"], 800)
        self.assertIsNone(no_shipping["shipping"])
        self.assertEqual(no_shipping["previous_shipping"], 25)
        self.assertFalse(
            worker["qualifies"](no_shipping, "Budget: 900 including shipping — firm maximum")
        )
