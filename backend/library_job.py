# -*- coding: utf-8 -*-
"""Cloud Run Job entrypoint for the Library Index (display-only layer).

Pulls Liked Songs + every playlist except Out Of Playlist and builds the per-artist
"what do I actually have of theirs" index, running to completion independent of any
browser (~1,000 paged reads / several minutes). Mirrors backend/bootstrap_job.py.

Reads LIBRARY_MODE from the environment:
    run    : fresh index (default)
    resume : continue from the checkpoint

Run:  python -m backend.library_job
The Cloud Run Job's container command is set to exactly that; the image is the SAME
one the Service uses, so no separate build is needed — BUT the Job pins whatever
image it was created with, so after each build you must repoint it:
    gcloud run jobs update aumradar-library --image=<new-image> --region=europe-west1
"""
import os
import sys
import time

from .core.storage_manager import storage
from .core.library import library, STATE_FILE
from .core.automation import automation_manager

HEARTBEAT_ALIVE_SEC = 120   # a run whose heartbeat is younger than this is "alive"


def _log(msg):
    print(f"[library_job] {msg}", flush=True)


def _another_instance_alive() -> bool:
    """Second (authoritative) layer of the concurrency guard: is another instance
    actively running right now? True when library_state says is_running with a FRESH
    heartbeat. status='starting' is the Service's pre-launch marker for THIS job, not
    a live run. (The API 409 is the first, best-effort layer.)"""
    st = storage.load_json(STATE_FILE)
    if not st or not st.get("is_running"):
        return False
    if st.get("status") == "starting":
        return False
    hb = st.get("heartbeat", 0)
    return bool(hb) and (time.time() - hb < HEARTBEAT_ALIVE_SEC)


def main():
    mode = (os.getenv("LIBRARY_MODE") or "run").strip().lower()
    _log(f"starting, LIBRARY_MODE={mode}")

    if _another_instance_alive():
        _log("another library index is already running (fresh heartbeat) — exiting cleanly.")
        sys.exit(0)

    # A user client (auto-refreshing) is required: Liked Songs and the user's own
    # private playlists are unreadable with the app (client-credentials) token.
    # see_rate_limits=True so a 429 raises WITH Retry-After for our sleep-with-heartbeat.
    try:
        sp = automation_manager.get_headless_client(see_rate_limits=True)
    except Exception as e:
        msg = ("Cannot authorize with Spotify (no saved token?). Run a manual scan "
               "once to authorize, then start the library index again.")
        _log(f"{msg} ({e})")
        # Leave a clear error in the shared state so the UI shows WHY instead of
        # sitting on 'starting' until the 120s staleness timer downgrades it.
        storage.save_json(STATE_FILE, {"is_running": False, "status": "error",
                                       "error": msg, "heartbeat": time.time()})
        sys.exit(1)

    try:
        library.run_index(sp, resume=(mode == "resume"))
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
