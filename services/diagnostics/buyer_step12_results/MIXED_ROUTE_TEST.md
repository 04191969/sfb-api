# Mixed direct-thread and directory test — 2026-11-18

Scope: isolated diagnostic; no worker activation, matcher change, model request, seller search, introduction or database write.

## Fresh public search

Five unchanged product-free buyer queries via explicit Yahoo/DDGS, no auto substitution. WTB forum, wanted marketplace, and in search of classifieds returned five rows each. Want to buy classifieds and looking to buy forum returned no extracted results. Two additional product-free queries, WTB + looking for and want to buy + thread, returned no results. No direct buyer thread was returned in this fresh sample; do not claim live fresh direct-thread discovery succeeded.

## Live traversal

The new fresh result set (not cached earlier seeds) supplied the directory URLs. Twenty live page reads captured nine original-post records from GolfWRX, Miata Turbo, PerformanceTrucks and WatchForum. Seven had explicit buying wording. Two (Ping hybrid and Xlink) were retained separately as role_needs_review because the original text did not match the conservative intent regex; directory context was not inserted into the author's text. Current buyer activity remains unconfirmed.

GolfWRX pagination was followed to page two and yielded a Prosendr Connection Sphere wanted post. Remaining URLs are retained in the diagnostic state. WatchUSeek returned HTTP 202; several other pages were unsupported or not buying posts. Full source texts and contact details are deliberately omitted from this repository summary.

## Routing defect exposed and repaired in diagnostic only

A direct thread with a related-thread list could previously be misclassified as a directory, skipping its original post. The presence of a supported original-post region now takes priority. If that original request is closed, it is rejected rather than following its related links as substitute evidence.

Twelve tests passed, including a mixed direct-thread/directory list reaching both original posts, related-thread precedence, exclusion of replies, closed-original rejection, same-host pagination, blocked-source continuation and preservation of pending work. Seven isolation checks passed. The direct-link route is controlled-test evidence, not a newly retrieved live buyer thread in this run.

## Result

Broad search and directory traversal are demonstrated together on fresh results. Direct-thread routing is verified in controlled tests. Search reliability remains intermittent, and the Windows worker has not been updated by this test.
