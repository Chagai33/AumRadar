# -*- coding: utf-8 -*-
"""Artist Health Engine — Stage 2: Bootstrap API.

Thin control surface over the Bootstrap Job: start / resume / stop / status. Every
endpoint is a fast web request (the heavy pull runs in the Cloud Run Job, polled
via /status) so nothing here approaches the ~26s proxy limit. Read-only w.r.t.
Spotify — Bootstrap only READS playlist tracks and WRITES cache files; it never
unfollows (that stays in /cleanup).
"""
from fastapi import APIRouter, Request, HTTPException, BackgroundTasks

from .auth import get_spotify_client
from ..core.bootstrap import bootstrap
from ..core.bootstrap_trigger import trigger_bootstrap_job
from ..core.storage_manager import storage

router = APIRouter()

RECON_SNAPSHOT_FILE = "cache/recon_playlists.json"
RECON_OVERRIDES_FILE = "cache/recon_inclusions.json"
AUTO_INCLUDED_TYPES = ("weekly", "outofplaylist")


def _reject_if_running():
    # get_status() already downgrades a stale (>120s heartbeat) run to not-running,
    # so a True here means a genuinely live run → refuse a second.
    if bootstrap.get_status().get("is_running"):
        raise HTTPException(status_code=409, detail="A bootstrap is already running")


def _included_count() -> int:
    """How many playlists currently feed the engine (weekly+outof auto, plus any
    manual override) — mirrors recon._is_included so the button can show the count."""
    snap = storage.load_json(RECON_SNAPSHOT_FILE)
    if not snap:
        return 0
    overrides = storage.load_json(RECON_OVERRIDES_FILE, default={}) or {}
    n = 0
    for pl in snap.get("playlists", []):
        uri = pl.get("playlist_uri")
        inc = overrides[uri] if uri in overrides else (pl.get("type") in AUTO_INCLUDED_TYPES)
        n += 1 if inc else 0
    return n


@router.post("/bootstrap/start")
async def start(request: Request, background_tasks: BackgroundTasks):
    """Kick off a fresh bootstrap. Requires a Recon snapshot to exist first."""
    sp = get_spotify_client(request)
    _reject_if_running()
    if not storage.exists(RECON_SNAPSHOT_FILE):
        raise HTTPException(400, "No Recon snapshot yet — run Playlist Recon (Stage 1) first.")
    if _included_count() == 0:
        raise HTTPException(400, "No playlists are marked to include — pick some in Recon first.")
    result = trigger_bootstrap_job("run", sp=sp, background_tasks=background_tasks)
    if result.get("status") == "error":
        raise HTTPException(503, result.get("message", "could not start bootstrap"))
    return {"status": "started", "trigger": result}


@router.post("/bootstrap/resume")
async def resume(request: Request, background_tasks: BackgroundTasks):
    """Continue an interrupted bootstrap from its checkpoint."""
    sp = get_spotify_client(request)
    _reject_if_running()
    result = trigger_bootstrap_job("resume", sp=sp, background_tasks=background_tasks)
    if result.get("status") == "error":
        raise HTTPException(503, result.get("message", "could not resume bootstrap"))
    return {"status": "resumed", "trigger": result}


@router.post("/bootstrap/stop")
def stop(request: Request):
    """Request a stop; the Job checkpoints and exits at the next safe point."""
    get_spotify_client(request)  # session-gate
    bootstrap.request_stop()
    return {"status": "stopping"}


@router.get("/bootstrap/status")
def status(request: Request):
    """Full status for the UI: run state + phase/progress + included-playlist count
    + whether a candidates file is ready to hand to /cleanup."""
    get_spotify_client(request)  # session-gate
    st = bootstrap.get_status()
    st["included_count"] = _included_count()
    st["recon_ready"] = storage.exists(RECON_SNAPSHOT_FILE)
    return st
