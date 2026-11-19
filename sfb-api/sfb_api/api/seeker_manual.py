"""Blank manual Seeker jobs.

The verified route is a consumer_search with trigger user, no market, and no
search text. That row is claimed by the Seeker worker. A typed consumer query
stays on Live Matches and is rejected here.
"""

from datetime import datetime, timezone

from .seeker_markets import (
    assigned_component,
    classify_heartbeat,
    is_scheduled_run,
    present_run,
)

SEEKER_OFFLINE = "Seeker worker is offline. The search was not sent to Live Matches."
SEEKER_UNKNOWN = "Seeker worker status is unknown. The search was not sent to Live Matches."
SEEKER_QUERY_REJECTED = "Seeker discovery cannot accept a consumer query."


def seeker_worker_report(rows, now=None):
    report = classify_heartbeat(rows, now=now)
    report["component"] = "seeker"
    if report["status"] == "online":
        report["message"] = "Seeker worker is online"
    elif report["status"] == "offline":
        report["message"] = "Seeker worker is offline"
    else:
        report["message"] = "Seeker worker status is unknown"
    return report


def blank_manual_row(now=None):
    """The row shape used by manual job 82a3b7c1."""
    now = now or datetime.now(timezone.utc)
    return {
        "search_query": None,
        "status": "pending",
        "job_kind": "consumer_search",
        "trigger": "user",
        "market_id": None,
        "requested_at": now.isoformat(),
    }


def active_seeker_job(runs, paused):
    """Oldest Seeker job the worker can claim. A held scheduled job does not count."""
    chosen = None
    for run in runs or []:
        status_name = str((run or {}).get("status") or "")
        if status_name not in ("pending", "running"):
            continue
        if assigned_component(run) != "seeker":
            continue
        if status_name == "pending" and paused and is_scheduled_run(run):
            continue
        if chosen is None or str(run.get("requested_at") or "") < str(chosen.get("requested_at") or ""):
            chosen = run
    return chosen


def decide_manual_seeker(query, runs, worker_rows, paused, now=None):
    if isinstance(query, str) and query.strip():
        return {"action": "reject_query", "error": SEEKER_QUERY_REJECTED, "component": "seeker"}
    now = now or datetime.now(timezone.utc)
    worker = seeker_worker_report(worker_rows, now=now)
    if worker["status"] != "online":
        error = SEEKER_UNKNOWN if worker["status"] == "unknown" else SEEKER_OFFLINE
        return {"action": "offline", "error": error, "component": "seeker", "worker": worker}
    existing = active_seeker_job(runs, paused)
    if existing is not None:
        return {"action": "existing", "run": existing, "worker": worker}
    return {"action": "create", "row": blank_manual_row(now), "worker": worker}


def review_result(run, match=None):
    """Show a saved assessment. Do not approve it or send an introduction."""
    match = match or {}
    match_id = (run or {}).get("match_id") or match.get("id")
    summary = (run or {}).get("result_summary") or ""
    if not match_id and not summary:
        return None
    status_name = match.get("status")
    return {
        "match_id": match_id,
        "status": status_name,
        "match_decision": match.get("match_decision"),
        "result_summary": summary or None,
        "introduction_sent": status_name == "introduction_sent",
    }


def present_seeker_job(run, worker, review=None, duplicate=False):
    body = present_run(run)
    body["component"] = "seeker"
    body["worker"] = worker
    body["job_id"] = body.get("id")
    body["duplicate"] = duplicate
    body["review"] = review
    return body
