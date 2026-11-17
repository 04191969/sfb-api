# Independent workers
Baseline: `bea39775a007c5153dcedd5a241f1470a119c078` (working consumer search).

Live Matches search, Facebook/Craigslist readers, currency handoff, V2 template, validator and qualification rules are copied byte-for-byte from that baseline. Its new process host only owns queue polling, separate state, heartbeats and usage attribution. Seeker has a separate physical V2 copy and separate process host. Neither imports the other.

Existing Django endpoints and both Supabase project addresses remain unchanged. The existing `seeker_runs` storage is split into mutually exclusive logical queues by an atomic database claim function: typed consumer searches with no market belong to Live Matches; blank discovery, market discovery and rechecks belong to Seeker. A wrong queue assignment fails closed. The old claim function is never used by the new workers. Original request and result records stay compatible with the site.

Each process has a separate worker lease, execution/version record, state directory, seen file, logs and launch task. The existing usage ledger remains the billing aggregate; `sfb_component_ai` attributes each reservation to its component and run. No usage reset. The existing Seeker dashboard pause applies only to scheduled Seeker work. Component enable controls stop new claims while an active job finishes. Shared catalog/result storage is retained; this is code/process isolation, not a migration to separate databases.

## Install once
Apply the additive `migrations/20261117230000_component_isolation.sql` to the worker database only. It creates disabled component controls and does not stop the legacy worker.

Run `install-isolated.ps1` only while no searches are pending/running. It verifies both releases and the pinned external package, copies independent immutable releases to `%USERPROFILE%\SFB`, copies the existing seen history separately, backs up and disables the old `SFB Seeker` task, stops the idle combined worker, activates the two queues, and starts `SFB live_matches` and `SFB seeker` tasks. Existing site routes stay unchanged. Do not use `worker/restart-five-step.ps1` after cutover.

The source installer is one-time and fails if release folders already exist. A partially completed install must be inspected rather than overwritten. The original repository and old task XML remain available for recovery. Never start the legacy combined worker alongside these processes.

## Independent operation
- Live Matches log: `%USERPROFILE%\SFB\live_matches\worker.log`
- Seeker log: `%USERPROFILE%\SFB\seeker\worker.log`
- Each `worker.err` sits beside its log.
- Stop gracefully: run that component's `stop.ps1 -Component live_matches` or `-Component seeker`; current job finishes first.
- Start: run that component's `start.ps1` with its component argument. The other process is untouched.
- Each worker has its own local single-instance socket guard.
- A machine reboot requires user login (logon tasks). No always-on hosting is implied.
- A dead in-progress job is held failed with an incomplete reason, not blindly replayed. Uncertain paid calls are not retried or marked free.

## Releases and changes
`release.json` lists hashes of every component source file. Editing one component does not edit the other. V2 source files are identical at separation but independent thereafter. A runtime refuses to start if its release hashes differ. Future changes need a new release id and a refreshed manifest for that component only, followed by its own tests and `activate-component.ps1 -Component seeker` (or `live_matches`). That script waits for only the selected worker to finish and changes only its active release pointer. The root shared old worker files are recovery/history only after cutover.

## Acceptance
Offline tests prove immutable live source copies, independent V2 payloads, explicit queue classification, boundary failure behavior and no cross-component imports. Database checks prove claims remain disabled until cutover, and grants restrict claims to service_role. Live PC acceptance still requires two fresh heartbeats, a consumer search, a Seeker run, and stopping/restarting each while the other remains online. Do not call PC isolation live before those checks.
