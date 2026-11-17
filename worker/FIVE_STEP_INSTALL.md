# Five-step connector

Scope: only blank public Run Seeker jobs use the uploaded locked five-step
package. Consumer requests, market discovery, rechecks, pauses, scheduling,
qualification and QQ2 source files are unchanged. No database migration or
Lovable publication is needed.

The default package directory is:
`%USERPROFILE%\Documents\test it\Seeker_v0_9_1_QQ2_handoff_fix\Seeker_locked_v0_9_1_plus_QQ2`
Set SFB_LOCKED_SEEKER_DIR to override explicitly. There is no folder scanning.
The bundled manifest pins the supplied package; startup fails on changed files.
Do not run the old SFB_Seeker_Connect BAT alongside the existing worker.

The existing worker claims jobs and enforces pauses. A child process executes
Run.execute, with database reservations around each model boundary, while the
parent refreshes the heartbeat. The locked local estimated-spending ledger is
preserved. A missing transport response leaves a database reservation for the
existing uncertain-call recovery; it is not falsely settled as a failed free call.
Run artifacts are under %USERPROFILE%\SFB_Seeker_Results\<run UUID>.
A previous output folder is never silently reused or executed again.

The locked package labels source availability unconfirmed. Its assessment is
stored intact for administrator review, with null confidence and no_send until
availability/requirements can qualify. No introduction is sent. New qualified
match counts are not inflated by imported assessments. Existing approved or
previously stored pair decisions are preserved. Observations remain in run files.

Verification: 66 existing API/worker regression tests and 15 connector/storage
unit tests passed. The actual uploaded package completed an offline test with
its real isolated portal handoff and simulated browser/model responses; both
model calls reserved/settled through simulated database callbacks. No live
Windows or production database-write acceptance test has yet occurred.

Installation requires updating the PC checkout to this revision while no job is
running, verifying the package, and restarting the existing SFB Seeker task.
Keep the previous commit for rollback. Do not turn on old category auto-search.
The remaining live acceptance is one blank Run Seeker from the website, followed
by inspection of the exact run UUID, current heartbeat, stored assessment and
usage reservations. Search engine blocking may legitimately stop discovery;
this connector does not change engines, queries, source screening or matching.
