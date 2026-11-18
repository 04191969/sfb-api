# Steps 1 and 2 proof — 2026-11-18

Isolated diagnostic only. No worker release, production configuration, Live Matches, discovery prompt or V2 rule changed. Zero model calls and database writes. No seller searches or introductions.

Step 1: a fresh Yahoo/DDGS pass of the same five product-free WTB queries returned rows for two queries. The WTB-forum query failed on this pass. Thus fresh discovery reliability remains unproven. The prior 50-search matrix had returned five WTB directories through that same broad query; those recorded results supplied the traversal seeds. No product list or destination site list is hardcoded in the new traversal.

Step 2: 20 live HTTP page reads yielded six original-post candidates, using the original h1 and first supported post region. GolfWRX: 2023 TaylorMade P790. Miata Turbo: caged roller, NB1 MS3. PerformanceTrucks: DSOM RCSB. WatchForum: Ball watch back cover, Seiko 7015-6010. WatchUSeek returned HTTP 202 and was not treated as readable. Further pages were unreadable, unsupported, or did not establish buying wording. No claim that all buyers are still active; source age and later replies need subsequent review. The NB1 post also mentions a separate NB2 offer; its original text is preserved for the reader, not collapsed into a single direction here.

Traversal takes primary topic-title anchors from observed forum layouts (IPS, vBulletin, XenForo), excludes profile/offsite/sidebar links, reads up to two posts immediately per source, and retains remaining links plus same-directory next-page links in STATE.json. The walk function supports continuation with that state; no worker scheduling/continuation was activated. Nine boundary tests passed, including original-post isolation, blocked-site continuation, pagination, preserving pending links and skipping visited URLs.

Final parser additionally preserves wanted-directory context separately from author text. Replay against the captured live pages accepts the Ping hybrid and Xlink posts as role_needs_review, not verified buyers: neither matched the conservative buying-word regex. This correction was verified on saved live HTML, not through a second HTTP run. It does not alter V2 or claim that section membership proves intent.

Evidence boundaries: six original posts observed live; eight posts captured by final parser replay (six with explicit buying wording and two requiring intent review). Full operational steps 1/2 on the user's Windows worker have not been demonstrated or installed. Do not present this as a complete working Seeker.
