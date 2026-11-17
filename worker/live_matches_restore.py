"""Run the historical Live Matches consumer path without replacing Seeker."""
import hashlib
import sys
from pathlib import Path

BASELINE = "f972a58e01aeef7291e269eb50e283e8d646e12d"
SHA256 = "81bf6455dab7eef094d443c6dd75bdedc559f731f0df80342282ba2a9a1be75c"

def protect_log_encoding():
    """A character in a listing must not abort the run on Windows cp1252.

    Preserve the configured log encoding; escape unsupported log characters.
    Source text, model input, and saved assessment strings are not modified.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, 'reconfigure', None)
        if callable(reconfigure):
            reconfigure(errors='backslashreplace')

def load_baseline():
    path = Path(__file__).with_name("live_matches_f972a58.bat")
    raw = path.read_bytes()
    # Git on Windows may check out text with CRLF.
    normalized = raw.replace(b"\r\n", b"\n")
    if hashlib.sha256(normalized).hexdigest() != SHA256:
        raise RuntimeError("Live Matches baseline integrity check failed")
    scope = {"__name__": "live_matches_baseline", "__file__": str(path)}
    exec(compile(normalized.decode("utf-8").split("\n", 1)[1], str(path), "exec"), scope)
    return scope

def run_claimed(sb, client, seen, run):
    """The current worker owns claiming, heartbeat, scheduling and blank Seeker runs."""
    protect_log_encoding()
    if not (run.get("search_query") or "").strip() or run.get("job_kind") in ("market_discovery", "recheck"):
        raise ValueError("Historical Live Matches accepts typed consumer searches only")
    baseline = load_baseline()
    if baseline["is_market_discovery_run"](run):
        raise ValueError("Market discovery must stay on its existing path")
    # Supply the already-claimed run; never claim or requeue another job here.
    baseline["claim_next_run"] = lambda _sb: run
    from live_matches_discovery import install as install_discovery
    install_discovery(baseline)
    from live_matches_v2.adapter import install
    install(baseline)
    print("    Live Matches search baseline: f972a58; assessment: V2 (gpt-5.4-2026-03-05)")
    if not baseline["check_manual_requests"](sb, client, seen):
        raise RuntimeError("Restored Live Matches run did not complete; inspect worker log")
    return True
