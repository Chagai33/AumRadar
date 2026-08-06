# -*- coding: utf-8 -*-
"""Cloud Run Job entrypoint for AumRadar scans.

Every scan — manual and scheduled — runs here as a Job that executes to
completion, independent of any browser (fixes the "scheduled scan needs a tab
open" problem and makes manual scans survive a closed tab too).

Reads SCAN_MODE from the environment:
    scheduled : fresh scan from automation_config (weekly Cloud Scheduler)
    manual    : fresh scan from cache/scan_job_request.json (written by /api/start)
    resume    : continue a blocked/interrupted scan from its checkpoint

Run:  python -m backend.scan_job
The Cloud Run Job's container command is set to exactly that; the image is the
same one the Service uses, so no separate build is needed.
"""
import os
import sys
import time
import asyncio

from .core.storage_manager import storage
from .core.scanner import scanner, SCAN_STATE_FILE, CHECKPOINT_FILE
from .core.automation import automation_manager
from .routers.auth import get_app_client
from .routers.scan import resolve_dynamic_dates

JOB_REQUEST_FILE = "cache/scan_job_request.json"
HEARTBEAT_ALIVE_SEC = 120   # a scan whose heartbeat is younger than this is "alive"


def _log(msg):
    print(f"[scan_job] {msg}", flush=True)


def _another_instance_alive():
    """Second (authoritative) layer of the concurrency guard: is another instance
    actively scanning right now? True when scan_state.json says is_running with a
    FRESH heartbeat. (The API 409 is the first, best-effort layer.) A tiny residual
    race remains — acceptable at single-user scale.)"""
    st = storage.load_json(SCAN_STATE_FILE)
    if not st or not st.get("is_running"):
        return False
    if st.get("status") == "starting":
        return False   # the Service's pre-launch marker for THIS job, not a live scan
    hb = st.get("heartbeat", 0)
    return bool(hb) and (time.time() - hb < HEARTBEAT_ALIVE_SEC)


async def _run():
    mode = (os.getenv("SCAN_MODE") or "manual").strip().lower()
    _log(f"starting, SCAN_MODE={mode}")

    if _another_instance_alive():
        _log("another scan is already running (fresh heartbeat) — exiting cleanly.")
        return 0

    # Auto-resume poller: a periodic scheduler fires SCAN_MODE=resume_due. Skip
    # cheaply (BEFORE building clients) unless there's a checkpoint whose rate-limit
    # block has already cleared — otherwise we'd just re-hit the block and waste
    # requests that could extend it.
    if mode == "resume_due":
        if not scanner.get_auto_resume().get("enabled", True):
            _log("auto-resume is disabled by the user — skipping.")
            return 0
        cp = storage.load_json(CHECKPOINT_FILE)
        if not cp:
            _log("auto-resume: no checkpoint — nothing to do.")
            return 0
        remaining = (cp.get("blocked_until") or 0) - time.time()
        if remaining > 0:
            _log(f"auto-resume: rate-limit block clears in ~{int(remaining / 60)} min — skipping this poll.")
            return 0
        _log("auto-resume: block cleared — resuming.")

    # Both clients are headless: the app client (auto-refreshing client-credentials)
    # for the heavy album/track calls, and the user client (auto-refreshing via the
    # storage-backed cache handler) for followed-artists + playlist export.
    try:
        app_sp = get_app_client()
        sp = automation_manager.get_headless_client()
    except Exception as e:
        _log(f"cannot build Spotify clients (no saved token? run a manual scan to authorize): {e}")
        return 1

    if mode in ("resume", "resume_due"):
        _log("resuming from checkpoint.")
        await scanner.resume_scan(sp, app_sp)   # no-ops safely if nothing to resume
        return 0

    if mode == "refresh_artists":
        _log("refresh-artists-only: updating the followed-artists cache (no scan).")
        await scanner.refresh_followed_artists(sp)
        return 0

    if mode == "scheduled":
        config = automation_manager.load_config()
        if not config.get("enabled"):
            _log("scheduled run but automation is disabled — skipping.")
            return 0
        settings_dict = resolve_dynamic_dates(dict(config.get("settings", {})))
        start = settings_dict.get("start_date", "")
        end = settings_dict.get("end_date", "")
        playlist_name = f"Weekly Radar {start} – {end}" if start and end else "Weekly Radar"
        _log(f"scheduled fresh scan {start}..{end} -> auto-export '{playlist_name}'")
        await scanner.scan_process(sp, settings_dict, app_sp, auto_export_name=playlist_name)
        return 0

    # default: manual
    req = storage.load_json(JOB_REQUEST_FILE)
    if not req or not req.get("settings"):
        _log("manual run but no scan_job_request.json found — nothing to do.")
        return 0
    settings_dict = resolve_dynamic_dates(dict(req["settings"]))
    _log(f"manual fresh scan {settings_dict.get('start_date')}..{settings_dict.get('end_date')}")
    await scanner.scan_process(sp, settings_dict, app_sp,
                               auto_export_name=req.get("auto_export_name"))
    return 0


def main():
    try:
        rc = asyncio.run(_run())
    except Exception as e:
        _log(f"FATAL: {e}")
        import traceback
        traceback.print_exc()
        rc = 1
    _log(f"done, exit={rc}")
    sys.exit(rc or 0)


if __name__ == "__main__":
    main()
