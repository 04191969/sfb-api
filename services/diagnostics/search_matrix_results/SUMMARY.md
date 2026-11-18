# Five-engine connection test — 2026-11-18

50 searches completed in the builder Linux environment, not the Windows PC. Five exact broad buyer queries, five explicit engines, two rounds. DDGS version: 9.16.1. No engine substitution, model calls, database writes, worker activation or production changes. Each search had a 25-second wall-clock process limit; no limits were hit. Four harness tests passed.

|Engine|Attempts returning links|HTTP responses|
|---|---:|---|
|Yahoo|5/10|200 on all ten attempts|
|Brave|0/10|429 on all ten attempts|
|Mojeek|0/10|200 but no extracted links|
|Startpage|0/10|200 but no extracted links; two HTTP requests per attempt|
|DuckDuckGo|0/10|202 on all ten attempts|

No connection-reset exception was observed. This does not explain Windows error 10054: different network/OS, and the PC DDGS version has not been measured. HTTP 200 does not establish that a real results page was received; no HTML was retained to diagnose challenge/content/parser causes. HTTP 429 shows rate limiting, but does not establish who imposed it. A no-results exception is not proof there are no ads.

Yahoo returned 25 rows / 20 unique URLs: 16 directories/community/home pages, 3 reference/off-target pages, 1 possible individual buying discussion (Pontiac Solstice). Classification uses only returned title/URL/snippet; no destination page opened, no genuine buyer or current offer verified. One Yahoo row had a title concatenating several unrelated results. That row is not reliable source evidence. See LEAD_REVIEW.json.

Yahoo returned links for WTB forum in round 1 but not round 2; want-to-buy classifieds and looking-to-buy forum showed the reverse. In-search-of classifieds returned links in both rounds. Wanted marketplace returned none in either. Therefore one earlier Yahoo success did not establish reliable retrieval.

REPORT.json retains every query, result, error and request host/status. The test script can run independently on Windows; it installs nothing and produces a timestamped folder under the user's home with SEND_THIS_REPORT.zip. Fetching its separate Git branch does not activate/change either worker.
