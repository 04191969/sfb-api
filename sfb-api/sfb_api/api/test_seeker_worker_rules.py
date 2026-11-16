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
        self.args = args
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

    def test_daily_total_does_not_stop_a_reservation(self):
        worker = self.worker
        self.assertFalse(worker["usage_blocked"](None))
        gate = Gate(data="550e8400-e29b-41d4-a716-446655440000")
        kind, token = worker["_reserve_ai"](gate, True)
        self.assertEqual((kind, token), ("db", "550e8400-e29b-41d4-a716-446655440000"))
        self.assertEqual(gate.args["cap"], worker["RESERVE_CAP"])
        self.assertGreater(gate.args["cap"], 40)
        self.assertNotIn("daily OpenAI cap", worker["reserve_closed_reason"]("missing"))

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

    def test_daily_total_does_not_stop_discovery(self):
        saved, pairs, _searches, summary, _database, _evaluations = self._run(blocked=True)
        self.assertTrue(saved)
        self.assertIn(
            ("https://sfb.example/request", "https://reverb.com/item/97393148-epiphone", "market-1"),
            pairs,
        )
        self.assertNotIn("daily OpenAI cap", summary)
        self.assertIn("qualifying match", summary)

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


FACEBOOK_SALE = "https://www.facebook.com/marketplace/item/1411175104520622/"
SELLING_EXCERPT = "Selling a rare piece of guitar history."


class BuyerIntentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.worker = load_worker()

    def test_facebook_selling_excerpt_is_not_a_buyer(self):
        intent = self.worker["author_intent"](SELLING_EXCERPT, FACEBOOK_SALE)
        self.assertEqual(intent["role"], "sell")
        buyer, offer = self.worker["public_post_roles"]({
            "title": SELLING_EXCERPT,
            "text": SELLING_EXCERPT,
            "url": FACEBOOK_SALE,
            "platform": "facebook",
        })
        self.assertIsNone(buyer)
        self.assertEqual(offer["text"], SELLING_EXCERPT)

    def test_genuine_buy_request_is_accepted(self):
        intent = self.worker["author_intent"]("Looking to buy an Epiphone USA Map")
        self.assertEqual(intent["role"], "buy")
        self.assertIn("Epiphone USA Map", intent["wanted"])

    def test_quoted_navigation_and_related_text_are_not_the_author(self):
        worker = self.worker
        quoted = worker["author_intent"](
            'The index lists "WTB Epiphone USA Map". Selling a rare piece of guitar history.'
        )
        self.assertEqual(quoted["role"], "sell")
        self.assertNotIn("Epiphone", quoted["wanted"])
        navigation = worker["author_intent"]("Marketplace · Buy · Sell · WTB\nEpiphone USA Map")
        self.assertEqual(navigation["role"], "unresolved")
        related = worker["author_intent"](
            "Looking to buy an Epiphone USA Map.\nRelated listings\nWTB Fender Stratocaster"
        )
        self.assertIn("Looking to buy an Epiphone USA Map", related["wanted"])
        self.assertNotIn("Fender", related["wanted"])

    def test_mixed_post_matches_only_the_wanted_item(self):
        intent = self.worker["author_intent"]("WTB Epiphone USA Map. WTS Gibson Les Paul for sale.")
        self.assertEqual(intent["role"], "mixed")
        self.assertIn("Epiphone USA Map", intent["wanted"])
        self.assertNotIn("Gibson", intent["wanted"])
        self.assertIn("Gibson", intent["offered"])
        buyer = self.worker["as_matching_buyer"]({
            "text": "WTB Epiphone USA Map. WTS Gibson Les Paul for sale.",
            "url": "https://forum.example/mixed",
            "platform": "web",
            "title": "mixed",
        })
        self.assertNotIn("Gibson", buyer["text"])

    def test_stored_facebook_sale_cannot_recreate_seller_matches(self):
        record = {
            "title": SELLING_EXCERPT,
            "text": SELLING_EXCERPT,
            "url": FACEBOOK_SALE,
            "platform": "facebook",
        }
        sellers = [
            {"url": f"https://reverb.com/item/{number}", "text": "Epiphone USA Map for sale", "availability": "available"}
            for number in range(4)
        ]
        self.assertEqual(self.worker["market_comparisons"]([record], sellers), [])

    def test_reproduced_buy_request_keeps_requirements_and_source(self):
        source = "\n".join((
            "Looking to buy an Epiphone USA Map.",
            "Budget: 850 plus shipping — firm maximum.",
            "Requirements: Ohio",
            "Condition: excellent",
            "No relic.",
        ))
        intent = self.worker["author_intent"](source)
        self.assertEqual(intent["role"], "buy")
        self.assertEqual(intent["source"], source)
        wanted = intent["wanted"]
        self.assertIn("Budget: 850 plus shipping — firm maximum.", wanted)
        self.assertIn("Requirements: Ohio", wanted)
        self.assertIn("Condition: excellent", wanted)
        self.assertIn("No relic.", wanted)
        cap = self.worker["buyer_price_cap"](wanted)
        self.assertEqual(cap["limit"], 850)
        self.assertTrue(cap["plus_shipping"])
        self.assertEqual(self.worker["required_location"](wanted), "Ohio")
        self.assertEqual(self.worker["stated_condition"](wanted), "excellent")
        listing = {
            "availability": "available",
            "price": 900,
            "shipping": 25,
            "title": "Epiphone USA Map",
            "text": "excellent",
            "location": "Ohio",
        }
        self.assertFalse(self.worker["qualifies"](listing, wanted))
        listing["price"] = 800
        self.assertTrue(self.worker["qualifies"](listing, wanted))
        buyer, _offer = self.worker["public_post_roles"]({
            "title": "WTB",
            "text": source,
            "url": "https://forum.example/requirements",
            "platform": "web",
        })
        self.assertEqual(buyer["source_text"], source)
        saved = {}

        class Result:
            def __init__(self, data):
                self.data = data

        class Query:
            def __init__(self, parent):
                self.parent = parent

            def select(self, *_args, **_kwargs):
                return self

            def eq(self, *_args, **_kwargs):
                return self

            def insert(self, row):
                self.parent.row = row
                return self

            def execute(self):
                if getattr(self.parent, "row", None):
                    return Result([{"id": "saved"}])
                return Result([])

        class Database:
            def table(self, _name):
                return Query(self)

        database = Database()
        self.worker["_remember_buyer"](database, "market-1", buyer)
        self.assertEqual(database.row["post_text"], source)

    def test_reproduced_mixed_post_keeps_each_items_requirements(self):
        source = "\n".join((
            "WTB Epiphone USA Map.",
            "Budget: 850 plus shipping — firm maximum.",
            "Requirements: Ohio",
            "No relic.",
            "WTS Gibson Les Paul for sale.",
            "Asking $2000 including shipping.",
            "Requirements: Texas.",
        ))
        intent = self.worker["author_intent"](source)
        self.assertEqual(intent["role"], "mixed")
        self.assertEqual(intent["source"], source)
        self.assertIn("Epiphone USA Map", intent["wanted"])
        self.assertIn("Budget: 850 plus shipping — firm maximum.", intent["wanted"])
        self.assertIn("Requirements: Ohio", intent["wanted"])
        self.assertIn("No relic.", intent["wanted"])
        self.assertNotIn("Gibson", intent["wanted"])
        self.assertNotIn("Texas", intent["wanted"])
        self.assertNotIn("2000", intent["wanted"])
        self.assertIn("Gibson Les Paul", intent["offered"])
        self.assertIn("Asking $2000 including shipping.", intent["offered"])
        self.assertIn("Requirements: Texas.", intent["offered"])
        self.assertNotIn("850", intent["offered"])
        self.assertNotIn("Ohio", intent["offered"])
        self.assertNotIn("Epiphone", intent["offered"])
        buyer = self.worker["as_matching_buyer"]({
            "text": source,
            "url": "https://forum.example/mixed-requirements",
            "platform": "web",
            "title": "mixed",
        })
        self.assertEqual(buyer["source_text"], source)
        self.assertIn("plus shipping", buyer["text"])
        self.assertNotIn("Gibson", buyer["text"])

    def test_must_include_the_original_case_stays_with_the_wanted_item(self):
        source = "\n".join((
            "Looking to buy an Epiphone USA Map.",
            "Must include the original case.",
            "WTS Gibson Les Paul for sale.",
            "Case sold separately.",
        ))
        intent = self.worker["author_intent"](source)
        self.assertIn("Must include the original case.", intent["wanted"])
        self.assertNotIn("Must include the original case.", intent["offered"])
        self.assertNotIn("separately", intent["wanted"])
        self.assertEqual(intent["source"], source)

    def test_negated_buying_statement_does_not_establish_intent(self):
        worker = self.worker
        for statement in (
            "Not looking to buy an Epiphone USA Map.",
            "I am not WTB an Epiphone USA Map.",
            "No longer looking for an Epiphone USA Map.",
            "I don't want to buy an Epiphone USA Map.",
        ):
            intent = worker["author_intent"](statement)
            self.assertEqual(intent["role"], "unresolved", statement)
            self.assertEqual(intent["wanted"], "")
            self.assertIsNone(worker["as_matching_buyer"]({
                "text": statement,
                "url": "https://forum.example/negated",
                "platform": "web",
            }))
        denied = "Not looking to buy an Epiphone USA Map. Selling a rare piece of guitar history."
        sale = worker["author_intent"](denied)
        self.assertEqual(sale["role"], "sell")
        self.assertEqual(sale["wanted"], "")

    def test_mixed_posts_attach_following_requirements_in_both_orders(self):
        sell_then_buy = (
            "WTS Gibson Les Paul, but WTB Epiphone USA Map. "
            "Budget: $850 plus shipping, firm maximum. "
            "Must include the original case."
        )
        reversed_mixed = self.worker["author_intent"](sell_then_buy)
        self.assertEqual(reversed_mixed["role"], "mixed")
        self.assertIn("Epiphone USA Map", reversed_mixed["wanted"])
        self.assertIn("Budget: $850 plus shipping, firm maximum.", reversed_mixed["wanted"])
        self.assertIn("Must include the original case.", reversed_mixed["wanted"])
        self.assertNotIn("850", reversed_mixed["offered"])
        self.assertNotIn("original case", reversed_mixed["offered"])
        self.assertIn("Gibson", reversed_mixed["offered"])
        cap = self.worker["buyer_price_cap"](reversed_mixed["wanted"])
        self.assertEqual(cap["limit"], 850)
        self.assertTrue(cap["plus_shipping"])
        self.assertFalse(cap["negotiable"])
        listing = {
            "availability": "available",
            "price": 2000,
            "shipping": 20,
            "title": "Epiphone USA Map",
            "text": "for sale",
            "location": "",
        }
        self.assertFalse(self.worker["qualifies"](listing, reversed_mixed["wanted"]))
        buyer = self.worker["as_matching_buyer"]({
            "text": sell_then_buy,
            "url": "https://forum.example/sell-then-buy",
            "platform": "web",
            "title": "mixed",
        })
        self.assertIn("Must include the original case.", buyer["text"])
        self.assertNotIn("Gibson", buyer["text"])

        buy_then_sell = (
            "WTB Epiphone USA Map, but WTS Gibson Les Paul for sale. "
            "Asking $2000 including shipping."
        )
        forward_mixed = self.worker["author_intent"](buy_then_sell)
        self.assertEqual(forward_mixed["role"], "mixed")
        self.assertIn("Epiphone USA Map", forward_mixed["wanted"])
        self.assertNotIn("2000", forward_mixed["wanted"])
        self.assertNotIn("including shipping", forward_mixed["wanted"])
        self.assertIn("Gibson Les Paul", forward_mixed["offered"])
        self.assertIn("Asking $2000 including shipping.", forward_mixed["offered"])
        self.assertNotIn("Epiphone", forward_mixed["offered"])

    def test_curly_apostrophe_does_not_create_buying_intent(self):
        statement = "I don\u2019t want to buy an Epiphone USA Map. Selling mine."
        intent = self.worker["author_intent"](statement)
        self.assertEqual(intent["role"], "sell")
        self.assertEqual(intent["wanted"], "")
        self.assertIn("Selling mine", intent["offered"])
        self.assertEqual(intent["source"], statement)
        self.assertIsNone(self.worker["as_matching_buyer"]({
            "text": statement,
            "url": "https://forum.example/curly",
            "platform": "web",
            "title": "sale",
        }))


YOUTUBE_SONG = "Jody Watley — Looking For A New Love"
YOUTUBE_URL = "https://www.youtube.com/watch?v=qpMj8c_Imss"
REVERB_PRODUCT = "https://reverb.com/p/epiphone-usa-map-guitar-reissue"
REVERB_ITEM = "https://reverb.com/item/97393148-epiphone-usa-map"
REVERB_ITEM_SLUG = "https://reverb.com/item/97393148-epiphone-usa-map-guitar"
GENUINE_WTB = "\n".join((
    "WTB Epiphone USA Map.",
    "Budget: $850 plus shipping, firm maximum.",
    "Must include the original case.",
))


class ProductIntentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.worker = load_worker()

    def test_youtube_song_is_not_a_buyer(self):
        intent = self.worker["author_intent"](YOUTUBE_SONG, YOUTUBE_URL)
        self.assertEqual(intent["role"], "unresolved")
        self.assertEqual(intent["wanted"], "")
        self.assertEqual(intent["source"], YOUTUBE_SONG)
        buyer, offer = self.worker["public_post_roles"]({
            "title": YOUTUBE_SONG,
            "text": YOUTUBE_SONG,
            "url": YOUTUBE_URL,
            "platform": "youtube",
        }, "Epiphone USA Map")
        self.assertIsNone(buyer)
        self.assertIsNone(offer)
        self.assertIsNone(self.worker["as_matching_buyer"]({
            "title": YOUTUBE_SONG,
            "text": YOUTUBE_SONG,
            "url": YOUTUBE_URL,
            "platform": "youtube",
        }))

    def test_genuine_product_request_keeps_requirements(self):
        intent = self.worker["author_intent"](GENUINE_WTB)
        self.assertEqual(intent["role"], "buy")
        self.assertEqual(intent["source"], GENUINE_WTB)
        self.assertIn("Epiphone USA Map", intent["wanted"])
        self.assertIn("Budget: $850 plus shipping, firm maximum.", intent["wanted"])
        self.assertIn("Must include the original case.", intent["wanted"])
        cap = self.worker["buyer_price_cap"](intent["wanted"])
        self.assertEqual(cap["limit"], 850)
        self.assertTrue(cap["plus_shipping"])
        buyer, _offer = self.worker["public_post_roles"]({
            "title": "WTB Epiphone USA Map",
            "text": GENUINE_WTB,
            "url": "https://forum.example/epiphone-wtb",
            "platform": "web",
        }, "Epiphone USA Map")
        self.assertEqual(buyer["source_text"], GENUINE_WTB)
        self.assertIn("Must include the original case.", buyer["text"])
        unrelated, _offer = self.worker["public_post_roles"]({
            "title": "WTB Fender Stratocaster",
            "text": "WTB Fender Stratocaster.",
            "url": "https://forum.example/fender-wtb",
            "platform": "web",
        }, "Epiphone USA Map")
        self.assertIsNone(unrelated)

    def _item_html(self):
        pad = "Epiphone USA Map guitar reissue in stock. " * 8
        return (
            "<html><body><h1>Epiphone USA Map guitar</h1>"
            "<p>Buy it now. Add to cart. New Price $850. Located in Austin, TX.</p>"
            + pad + "</body></html>"
        )

    def _product_html(self, item_url):
        pad = "Epiphone USA Map guitar reissue in stock. " * 8
        return (
            "<html><body><h1>Epiphone USA Map Guitar Reissue</h1>"
            "<p>Shop new and used listings. From $800. Buy it now.</p>"
            f'<a href="{item_url}">Epiphone USA Map guitar</a>'
            + pad + "</body></html>"
        )

    def test_reverb_product_page_resolves_to_the_item(self):
        worker = self.worker
        pages = {
            REVERB_PRODUCT: self._product_html(REVERB_ITEM_SLUG),
            REVERB_ITEM_SLUG: self._item_html(),
        }
        found, incomplete = worker["expand_candidates"](
            [{"url": REVERB_PRODUCT, "title": "Epiphone USA Map Guitar Reissue", "body": "From $800"}],
            "Epiphone USA Map",
            lambda url: pages.get(url, ""),
        )
        self.assertEqual(incomplete, "")
        self.assertEqual([item["url"] for item in found], [REVERB_ITEM_SLUG])
        self.assertEqual(found[0]["price"], 850)
        self.assertEqual(found[0]["availability"], "available")
        self.assertEqual(found[0]["discovery_url"], REVERB_PRODUCT)
        self.assertNotEqual(found[0]["price"], 800)

    def test_product_page_and_direct_item_are_one_offer(self):
        pages = {
            REVERB_PRODUCT: self._product_html(REVERB_ITEM_SLUG),
            REVERB_ITEM: self._item_html(),
            REVERB_ITEM_SLUG: self._item_html(),
        }
        found, _incomplete = self.worker["expand_candidates"](
            [
                {"url": REVERB_PRODUCT, "title": "Epiphone USA Map Guitar Reissue", "body": "From $800"},
                {"url": REVERB_ITEM, "title": "Epiphone USA Map guitar", "body": "New Price $850"},
            ],
            "Epiphone USA Map",
            lambda url: pages.get(url, ""),
        )
        self.assertEqual(len(found), 1)
        self.assertEqual(self.worker["source_identity"](found[0]["url"]), "97393148")
        self.assertNotIn(REVERB_PRODUCT, [item["url"] for item in found])

    def test_product_page_without_a_verifiable_item_is_not_an_offer(self):
        pad = "Epiphone USA Map guitar reissue model page. " * 8
        empty = (
            "<html><body><h1>Epiphone USA Map Guitar Reissue</h1>"
            "<p>From $800. Buy it now. No current listings.</p>"
            + pad + "</body></html>"
        )
        found, _incomplete = self.worker["expand_candidates"](
            [{"url": REVERB_PRODUCT, "title": "Epiphone USA Map Guitar Reissue", "body": "From $800"}],
            "Epiphone USA Map",
            lambda url: empty,
        )
        self.assertEqual(found, [])

    def test_stored_song_and_product_page_stay_out_of_matching(self):
        song = {
            "title": YOUTUBE_SONG,
            "text": YOUTUBE_SONG,
            "url": YOUTUBE_URL,
            "platform": "youtube",
        }
        product = {
            "url": REVERB_PRODUCT,
            "title": "Epiphone USA Map Guitar Reissue",
            "text": "From $800 Buy it now",
            "availability": "available",
            "price": 800,
        }
        item = {
            "url": REVERB_ITEM,
            "title": "Epiphone USA Map guitar",
            "text": "Epiphone USA Map guitar for sale",
            "availability": "available",
            "price": 850,
        }
        buyer = {
            "title": "WTB Epiphone USA Map",
            "text": GENUINE_WTB,
            "url": "https://forum.example/epiphone-wtb",
            "platform": "web",
        }
        self.assertEqual(
            self.worker["market_comparisons"]([song], [item, product], "Epiphone USA Map"),
            [],
        )
        self.assertEqual(
            self.worker["market_comparisons"]([buyer], [product], "Epiphone USA Map"),
            [],
        )
        pairs = self.worker["market_comparisons"]([buyer, song], [product, item], "Epiphone USA Map")
        self.assertEqual([seller["url"] for _buyer, seller in pairs], [REVERB_ITEM])


class WantedProductTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.worker = load_worker()

    def test_advice_and_photo_requests_are_not_purchases(self):
        worker = self.worker
        for statement in (
            "Looking for advice on my Epiphone USA Map.",
            "Looking for photos of an Epiphone USA Map.",
            "WTB pictures of an Epiphone USA Map.",
        ):
            intent = worker["author_intent"](statement)
            self.assertEqual(intent["role"], "unresolved", statement)
            self.assertEqual(intent["source"], statement)
            self.assertIsNone(worker["as_matching_buyer"]({
                "text": statement,
                "url": "https://forum.example/not-a-purchase",
                "platform": "web",
                "title": statement,
            }))
            buyer, _offer = worker["public_post_roles"]({
                "text": statement,
                "url": "https://forum.example/not-a-purchase",
                "platform": "web",
                "title": statement,
            }, "Epiphone USA Map")
            self.assertIsNone(buyer)

    def test_owned_or_mentioned_product_does_not_set_the_market(self):
        owned = "\n".join((
            "I own an Epiphone USA Map.",
            "WTB a Gibson Les Paul.",
            "Budget: $1500 plus shipping, firm maximum.",
            "Must include the original case.",
        ))
        intent = self.worker["author_intent"](owned)
        self.assertEqual(intent["role"], "buy")
        self.assertEqual(intent["source"], owned)
        self.assertIn("Gibson Les Paul", intent["wanted"])
        self.assertNotIn("Epiphone", intent["wanted"])
        self.assertIn("Budget: $1500 plus shipping, firm maximum.", intent["wanted"])
        self.assertIn("Must include the original case.", intent["wanted"])
        self.assertFalse(self.worker["relevant_to_market"](intent["wanted"], "Epiphone USA Map"))
        self.assertTrue(self.worker["relevant_to_market"](intent["wanted"], "Gibson Les Paul"))
        buyer, _offer = self.worker["public_post_roles"]({
            "text": owned,
            "url": "https://forum.example/owns-map",
            "platform": "web",
            "title": "WTB a Gibson Les Paul",
        }, "Epiphone USA Map")
        self.assertIsNone(buyer)
        gibson_buyer, _offer = self.worker["public_post_roles"]({
            "text": owned,
            "url": "https://forum.example/owns-map",
            "platform": "web",
            "title": "WTB a Gibson Les Paul",
        }, "Gibson Les Paul")
        self.assertIn("Must include the original case.", gibson_buyer["text"])
        self.assertEqual(gibson_buyer["source_text"], owned)
        mentioned = "Saw an Epiphone USA Map today. WTB a Fender Stratocaster."
        mentioned_intent = self.worker["author_intent"](mentioned)
        self.assertNotIn("Epiphone", mentioned_intent["wanted"])
        self.assertIn("Fender Stratocaster", mentioned_intent["wanted"])
        self.assertFalse(self.worker["relevant_to_market"](mentioned_intent["wanted"], "Epiphone USA Map"))
        sellers = [{
            "url": "https://reverb.com/item/555-gibson-les-paul",
            "text": "Gibson Les Paul for sale",
            "availability": "available",
            "title": "Gibson Les Paul",
        }]
        self.assertEqual(
            self.worker["market_comparisons"](
                [{"text": owned, "url": "https://forum.example/owns-map", "platform": "web", "title": "WTB"}],
                sellers,
                "Epiphone USA Map",
            ),
            [],
        )
        pairs = self.worker["market_comparisons"](
            [{"text": owned, "url": "https://forum.example/owns-map", "platform": "web", "title": "WTB"}],
            sellers,
            "Gibson Les Paul",
        )
        self.assertEqual([seller["url"] for _buyer, seller in pairs], ["https://reverb.com/item/555-gibson-les-paul"])

    def test_model_punctuation_matches_without_collapsing_different_models(self):
        worker = self.worker
        hyphenated = "\n".join((
            "WTB Epiphone USA-Map.",
            "Budget: $850 plus shipping, firm maximum.",
            "Must include the original case.",
        ))
        intent = worker["author_intent"](hyphenated)
        self.assertEqual(intent["source"], hyphenated)
        self.assertIn("USA-Map", intent["wanted"])
        self.assertIn("Budget: $850 plus shipping, firm maximum.", intent["wanted"])
        self.assertIn("Must include the original case.", intent["wanted"])
        self.assertTrue(worker["relevant_to_market"](intent["wanted"], "Epiphone USA Map"))
        self.assertTrue(worker["relevant_to_market"]("WTB Epiphone USAMap.", "Epiphone USA Map"))
        self.assertTrue(worker["relevant_to_market"]("WTB ST62.", "ST-62"))
        self.assertTrue(worker["relevant_to_market"]("WTB ST 62.", "ST-62"))
        self.assertFalse(worker["relevant_to_market"]("WTB ST62.", "ST-62G"))
        self.assertFalse(worker["relevant_to_market"]("WTB ST-62G.", "ST-62"))
        self.assertFalse(worker["relevant_to_market"]("WTB Gibson Les Paul.", "Epiphone USA Map"))
