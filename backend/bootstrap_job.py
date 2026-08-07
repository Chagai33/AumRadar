# -*- coding: utf-8 -*-
"""Cloud Run Job entrypoint for Artist Health Bootstrap (Stage 2).

Pulls tracks for every included playlist and computes per-artist RANK, running to
completion independent of any browser (the pull is hundreds of API calls / many
minutes). Mirrors backend/scan_job.py.

Reads BOOTSTRAP_MODE from the environment:
    run    : fresh bootstrap (default)
    resume : continue from the checkpoint

Run:  python -m backend.bootstrap_job
The Cloud Run Job's container command is set to exactly that; the image is the SAME
one the Service uses, so no separate build is needed — BUT the Job pins whatever
image it was created with, so after each build you must repoint it:
    gcloud run jobs update aumradar-bootstrap --image=<new-image> --region=europe-west1
(see artist-health/BOOTSTRAP.md §4 — this is the one deploy gotcha; automatable via
a Cloud Build step).
"""
import os
import sys
import time

from .core.storage_manager import storage
from .core.bootstrap import bootstrap, STATE_FILE
from .core.automation import automation_manager

HEARTBEAT_ALIVE_SEC = 120   # a run whose heartbeat is younger than this is "alive"


def _log(msg):
    print(f"[bootstrap_job] {msg}", flush=True)


def _another_instance_alive() -> bool:
    """Second (authoritative) layer of the concurrency guard: is another instance
    actively running right now? True when bootstrap_state says is_running with a
    FRESH heartbeat. status='starting' is the Service's pre-launch marker for THIS
    job, not a live run. (The API 409 is the first, best-effort layer.)"""
    st = storage.load_json(STATE_FILE)
    if not st or not st.get("is_running"):
        return False
    if st.get("status") == "starting":
        return False
    hb = st.get("heartbeat", 0)
    return bool(hb) and (time.time() - hb < HEARTBEAT_ALIVE_SEC)


def main():
    mode = (os.getenv("BOOTSTRAP_MODE") or "run").strip().lower()
    _log(f"starting, BOOTSTRAP_MODE={mode}")

    if _another_instance_alive():
        _log("another bootstrap is already running (fresh heartbeat) — exiting cleanly.")
        sys.exit(0)

    # A user client (auto-refreshing) is required: the user's own playlists may be
    # private/collaborative, which the app (client-credentials) token cannot read.
    # see_rate_limits=True so a 429 raises WITH Retry-After for our sleep-with-heartbeat.
    try:
        sp = automation_manager.get_headless_client(see_rate_limits=True)
    except Exception as e:
        msg = ("Cannot authorize with Spotify (no saved token?). Run a manual scan "
               "once to authorize, then start Bootstrap again.")
        _log(f"{msg} ({e})")
        # Leave a clear error in the shared state so the UI shows WHY instead of
        # sitting on 'starting' until the 120s staleness timer downgrades it.
        storage.save_json(STATE_FILE, {"is_running": False, "status": "error",
                                       "error": msg, "heartbeat": time.time()})
        sys.exit(1)

    try:
        bootstrap.run_bootstrap(sp, resume=(mode == "resume"))
        rc = 0
    except Exception as e:
        _log(f"FATAL: {e}")
        import traceback
        traceback.print_exc()
        rc = 1

    _log(f"done, exit={rc}")
    sys.exit(rc)


if __name__ == "__main__":
    main()
