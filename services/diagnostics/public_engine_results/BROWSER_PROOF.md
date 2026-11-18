# Browser search fallback proof — November 18, 2026

Scope: anonymous public search and navigation to original buyer posts. No paid search APIs, model calls, production edits, database writes, worker restarts, or introductions.

## Retrieval

| Engine | Observed browser result |
|---|---|
| Qwant | Usable results verified on all five broad queries |
| Lilo | Usable results verified on all five broad queries |
| Yep | Usable results verified on all five broad queries; relevance varied substantially |
| Swisscows | Usable results verified, including ten WTB cards; not a verified five-of-five result |
| OceanHero | Usable results on the WTB query; other queries not tested |
| Dogpile | Explicit search-results-temporarily-unavailable message |
| You.com | Sign-in redirect; no login attempted |

Queries: `"WTB" forum`, `"want to buy" classifieds`, `"looking to buy" forum`, `"wanted" marketplace`, `"in search of" classifieds`.

These five readable routes had not yielded usable result lists in the preceding HTML-only test. Browser rendering and form submission recovered them. Search results still included definitions, general discussions, and virtual-game trading; retrieval success is not buyer validation.

## Following original posts

Fresh Qwant WTB results supported both paths:
1. Direct thread result -> original Decware cable buying request. Later author replies indicate at least the speaker-cable portion was fulfilled. This proves navigation, not an active buyer.
2. Wanted-directory result -> individual WatchForum request for Seiko 5626 black day/date parts, dated August 9, 2026. The original post requests shipment to Brazil. It is a genuine buying request, but current intent and geographic eligibility were not established.

No contact details or raw page captures are included in this report.

## Limits

This is an interactive browser proof in the available test environment, not an automated Windows Seeker integration test. Some initial accessibility snapshots were incremental or captured before asynchronous results arrived; only subsequent full snapshots with visible results support the success statements above. No empty early snapshot was scored as an engine failure.

Next engineering gate: isolated automated retrieval using the proven public browser routes, followed by original-post extraction, with saved engine/URL/outcome records and bounded fallback. Prove on the Windows worker environment before production activation. Keep Live Matches and V2 qualification unchanged.
