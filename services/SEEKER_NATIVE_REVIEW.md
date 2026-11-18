# Independent Seeker replacement — development checkpoint

Seeker owns its reader files and V2 adapter. Blank discovery is routed to native_discovery rather than five_step_host. No files under services/live_matches were changed. Existing component processes, queues, state folders and activation remain separate.

Verification: 84 service tests and 7 isolation tests passed offline. No paid calls, database writes, worker activation or production deployment were performed.

Not release-ready: the new native discovery currently implements WTB-first retrieval only. The previous WTS fallback and buyer directory traversal still need migration and verification before activation. Original-post coverage and live DDGS-to-reader-to-V2 behavior also require verification. Do not activate this checkpoint as a completed replacement.
