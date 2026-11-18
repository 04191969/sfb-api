# Independent Seeker replacement — development checkpoint

Seeker owns its reader files and V2 adapter. Blank discovery is routed to native_discovery rather than five_step_host. No files under services/live_matches were changed. Existing component processes, queues, state folders and activation remain separate.

Verification: 84 service tests and 7 isolation tests passed offline. No paid calls, database writes, worker activation or production deployment were performed.

Not release-ready: the new native discovery currently implements WTB-first retrieval only. The previous WTS fallback and buyer directory traversal still need migration and verification before activation. Original-post coverage and live DDGS-to-reader-to-V2 behavior also require verification. Do not activate this checkpoint as a completed replacement.

## Discovery reader restored (November 18)

Native WTB discovery now sends the complete captured original to a separately owned discovery reader. Its instruction and input/output schema are copied unchanged from the existing discovery contract. Model gpt-5.4-2026-03-05, medium reasoning, the existing 6,000-token allowance and exact-quote instruction are retained. Validated ordered queries enter seller retrieval exactly; V2 still receives the originals. V2 instruction, reasoning, evidence contract and qualification logic were not edited. Live Matches was not edited.

The new leg passed frozen-response replay, payload equality, direction handling, exact-query handoff, invalid-evidence, incomplete-response and uncertain-call tests. No new paid model call was made. Prior retrieval failures, absent WTS fallback and directory traversal remain outstanding; this is still a development checkpoint, not an activated release.
