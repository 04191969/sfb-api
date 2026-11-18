# Native Seeker process audit — 2026-11-18

Production not changed by this audit. Live Matches untouched. No paid model calls, introductions, or database writes.

## Live state at 10:31 AM Eastern

Supabase heartbeat: seeker-24692 still running split-bea3977-seeker-native-2; live_matches-29292 independently running split-bea3977-live-1. No pending/running jobs at the queue check. Latest Seeker job 57c8ffe2-797a-4629-be81-c938b3840950 failed at 10:20:56 AM with DDGS connection reset 10054. Native-3 activation is not established by these observations.

The activation script prints its first progress message before invoking Python. The supplied last console output has not shown that message. The code has bounded worker-stop/readiness loops, but verification, filesystem work and child process startup are not all bounded. There is insufficient PC-side evidence to locate the stall. No Windows shell is available in this workspace; a Python test does not verify Windows activation.

## Actual public DDGS probe

Ran two current broad queries through DDGS, no model call. `timeout=10` was not an overall query deadline: queries took 24.0 and 13.1 seconds.

`"want to buy" classifieds` returned Facebook Marketplace, Preloved, The Want Ads, ClassifiedAds and AutoTrader home/index pages.

`"WTB" forum` returned an irrelevant Wikipedia result plus:
- https://forums.golfwrx.com/forum/94-wanted-to-buy-forum/
- https://www.miataturbo.net/wtb-7/
- https://www.performancetrucks.net/forums/wtb-wanted-buy-211/
- https://www.watchuseek.com/forums/wtb-wanted-to-buy.89/

These are source leads, not verified current buyer ads. No ads were opened in this probe. Network results from this environment do not establish connectivity on the user's Windows machine.

## Confirmed process gaps

1. Native cycle skips directory-classified leads. There is no directory-to-individual-buyer traversal in that path. Broad queries naturally return these directories, including useful WTB forums.
2. Buyer extraction requires an h1 plus one of five forum/classified selectors. A plain h1/article wanted ad is rejected before discovery model reading. This is a tested coverage gap, not a reason to accept navigation/replies as an author post.
3. Seller discovery runs all counterpart queries before opening queued seller pages. DDGS per-request timeouts do not bound total query duration. The 600-second cycle check occurs between operations, not during them.
4. An unreadable seller page is recorded under skipped sources, but does not necessarily propagate an incomplete flag; a run can end as no qualifying match despite unreadable evidence. Observed in the full-path fixture development (short HTML rejected by existing minimum-length rule).
5. Generic dealer URL recognition remains restricted by the copied reader's is_item_page filter. Prior saved-page probes document real dealer pages excluded; this audit did not reopen them.
6. Model transport/validation failures remain possible and must retain exact original evidence. No reasoning/schema change made.

## Test evidence and limits

102 service tests + 7 isolation tests passed. New test_native_pipeline_audit.py exercises the real native cycle, buyer extraction, discovery adapter/validator, seller search/page reader, own V2 adapter/evidence validator/shared qualification, and local publish boundary. It uses the saved Polk discovery response with current source binding plus synthetic seller HTML and a synthetic structured matcher response. It validates integration, NOT live model accuracy, current offer availability, or a real database publication. Early fixture failures were corrected to meet the actual transport response format and reader minimum HTML length; production validators were not relaxed.

Two further tests document directory skipping and unsupported plain article extraction. Passing those tests confirms the existing limitations; it does not mean those limitations are repaired.

## Repair order

First resolve/observe activation on the PC. Next restore bounded WTB directory-to-original-post traversal in Seeker only and prove it on actual returned sources. Preserve original author boundaries, deduplication, accounting and incomplete reporting. Process recovered buyers immediately through the existing discovery reader and seller retrieval; do not modify V2 reasoning to compensate for inaccessible pages. Require live source evidence and a completed real run before declaring Seeker operational.
