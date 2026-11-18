# Broad public search engine test — November 18, 2026

**205 search attempts across 41 public routes.** Includes alternative interfaces and three instances of the same SearXNG software; these are not 41 independent search indexes. This is a broad documented survey, not a claim that every public engine worldwide has been exhaustively enumerated.

## Scope

Five identical, product-free queries per route:

- `"want to buy" classifieds`
- `"WTB" forum`
- `"looking to buy" forum`
- `"wanted" marketplace`
- `"in search of" classifieds`

Direct anonymous public HTML GETs from this Linux test environment. No paid search APIs, Tavily, Exa, model calls, database writes, worker changes, credential changes, or Live Matches changes. A returned HTML page is not automatically a successful search. Generic outgoing-link counts were reviewed and were not used as result counts.

No JavaScript rendering, login, CAPTCHA bypass, or proxy rotation. The environment already uses a network proxy. Network failures cannot automatically be attributed to the search provider. This is not a Windows connectivity proof. No US locale was forced; some result sets defaulted to the UK.

## Findings

| Public route | Responses across 5 attempts | Finding |
|---|---|---|
| Google | 429 × 5 | HTTP 429; unusual-traffic challenge. No usable result list. |
| Bing | 200 × 5 | Real result lists in all five saved HTML pages. Result URLs use Bing redirect encoding; a generic external-link parser misses them. |
| Yahoo | 500 × 5 | Direct route returned HTTP 500 with empty bodies in all five attempts. Earlier DDGS route had partial success; this does not prove Yahoo universally unavailable. |
| DuckDuckGo HTML | 202 × 5 | HTTP 202 human-verification challenge, all five. |
| DuckDuckGo Lite | 202 × 5 | HTTP 202 human-verification challenge, all five. |
| Brave | 200 × 5 | Real result lists in all five saved HTML pages. Some use /go/v1/ redirect links, others direct URLs. Earlier DDGS 429 result did not recur with this direct route. |
| Mojeek | 200 × 5 | HTTP 200 CAPTCHA page, not results. |
| Startpage | 200 × 5 | HTTP 200 Anubis verification page, not results. |
| Qwant | 200 × 5 | Query/title present, but no readable result list in returned HTML; client-rendered route unproven. |
| Ecosia | 403 × 5 | HTTP 403 firewall challenge. |
| Yep | 200 × 5 | HTTP 200 shell without readable results; browser-rendered retrieval unproven. |
| Swisscows | 200 × 5 | Query/title present but no readable results in HTML; browser-rendered retrieval unproven. |
| MetaGer | 200 × 5 | Redirected to access-key/payment setup. Not available as anonymous free search in this test. |
| Marginalia | 200 × 5 | Wait/bot-interstitial page; no result list evaluated. Did not bypass it. |
| Mwmbl | 200 × 5 | Real results on two queries, explicit no-results on three; observed results mostly unrelated to buying ads. Seed Search requires login; not used. |
| Stract | 404 × 5 | Tested /search route returned 404; no usable search demonstrated. Do not infer full service status from this route alone. |
| Yandex | 200 × 5 | HTTP 200 robot-verification page. |
| Baidu | 200 × 2, 503 × 3 | Empty/unusable responses: two HTTP 200, three 503. |
| Naver | 200 × 5 | Real search results, including wanted-classified sources on first query; other queries include unrelated material. Korean interface, English queries. |
| Seznam | 200 × 3, 429 × 2 | Real result pages on three queries, HTTP 429 on two. Classified sources present, relevance mixed. |
| AOL | 404 × 5 | Tested public route returned 404 in all five; search not demonstrated. |
| Ask | 404 × 5 | Tested public route returned 404 in all five; search not demonstrated. |
| Lycos | 503 × 5 | HTTP 503 in all five. |
| Dogpile | request_error × 1, 200 × 4 | One request error; four HTML shells saying loading search results. Browser-rendered results not tested. |
| Gibiru | 200 × 5 | HTTP 200 Site Unavailable response, no results. |
| SearXNG sapti | 429 × 5 | HTTP 429 in all five. |
| SearXNG tiekoetter | 429 × 5 | HTTP 429 in all five. |
| SearXNG paulgo | 429 × 5 | HTTP 429 in all five. |
| Wiby | 200 × 5 | Explicit no-results pages on all five broad queries. |
| SearchMySite | 200 × 5 | Real results on four queries, no results on one. Sampled results personal blogs rather than buyer ads. |
| Lilo | 200 × 1, request_error × 4 | One empty HTML shell and four request errors. No usable list. |
| Good | 200 × 5 | Explicit no-results pages on all five queries. |
| OceanHero | 200 × 5 | HTML shell/navigation only; no readable result list. |
| You | 200 × 5 | HTML shell/sign-in interface; no readable result list. |
| Presearch | 200 × 5 | Site Unavailable response, no results. |
| Boardreader | 200 × 5 | Tested route returned page-not-found text with HTTP 200. Route unresolved, not a valid no-results finding. |
| 360 Search | 302 × 5 | HTTP 302 redirect-loop errors; no result page. |
| Sogou | 200 × 5 | Search content returned, but observed English-query interpretation/relevance poor (translation/definitions and unrelated content); not proven for buyer discovery. |
| Daum | 200 × 5 | Real search results, but largely unrelated; WTB query includes virtual-game trading. Not demonstrated useful for physical-product buyer discovery. |
| Petal | 503 × 5 | HTTP 503 in all five. |
| Luxxle | 200 × 5 | HTTP 200 with 404/error-page text; no usable list. |

## What this proves

- Bing and Brave returned real broad search result lists on every query in this sweep. Both returned classified/forum sources. They are candidates for isolated step-2 testing, not yet approved production replacements.
- Naver and Seznam broadened source coverage. Their result relevance and location need validation before use.
- An engine can be reachable yet return irrelevant results, a challenge, or a JavaScript shell. These are separate outcomes.
- Public retrieval is variable: earlier DDGS Brave calls returned 429; this direct route returned results. Earlier DDGS Yahoo had partial success; this direct route returned 500. These observations do not establish the cause of the difference.
- Broad queries returned both directories and individual discussion links. Search snippets do not prove a genuine current buyer. The original post still needs opening and reading.
- This test does not establish current availability, buyer authenticity, match quality, or Windows reliability. Step 2 remains the next separate proof.

## Test integrity

The diagnostic runner has five passing offline checks for query scope, explicit Google/Bing routes, redirect extraction, and not treating navigation links as verified results. All 205 attempts are retained locally with requested/final URLs, status, timing and response hashes. Only aggregate COUNTS.json is published; raw responses and detailed request metadata remain local. No production branch was updated.
