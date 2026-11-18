# Isolated steps 1 and 2 proof

November 18, 2026. No production changes, worker restarts, database writes, model calls, or introductions.

## Fresh browser observations

A fresh public Qwant search for `"WTB" forum` returned ten result links. No product, category, or target forum was supplied.

- The GolfWRX wanted directory led to an individual request for a 2023 TaylorMade P790 set. Its original post timestamp was 2026-11-18T15:50:13Z. The body specifies 3 or 4 through PW, heads preferred, and condition limits. The existing diagnostic original-post selector isolated the one original body and excluded featured topics. Current buying intent has not been independently confirmed.
- The PerformanceTrucks wanted directory led to an original request for a DSOM RCSB truck. The original body asks for great condition and favors lower mileage. Four other matching DOM regions included replies and a separate related request; only the first is the original buyer text. Its title must not be resolved using a reply as though it were the author's statement. No current-intent claim is made.
- A second broad query, `"want to buy" classifieds`, returned ten largely generic marketplace/homepage links. This is retrieval success, not proof of buyer extraction.

## Runnable Windows proof

`run-browser-buyer-proof.ps1` uses the existing Python and Playwright installation plus installed Microsoft Edge in a separate temporary browser session. No personal browser profile is used. Missing dependencies or a blocked page are reported, not automatically installed or bypassed.

Nine broad searches: three phrases on Qwant, Lilo and Yep. Up to twelve source-page navigations. Original title/first-post extraction uses `buyer_discovery_steps12.py`. Candidate records stay unconfirmed; later replies/current intent and seller matching are outside acceptance.

Every operation prints progress. JSON report and pending queue are saved under a new timestamped SFB_Buyer_Browser_Proof folder in the user's home directory. A ZIP is produced even on setup failure. The five-minute allowance is checked between operations; an in-flight browser operation can add its timeout.

Validation: 12 traversal tests and 4 runner/parser tests passed. The standalone runner has not yet passed a live Windows run. Interactive cloud-browser observations are not represented as a Windows pass. Production is unchanged.
