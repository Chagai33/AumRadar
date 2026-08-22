# -*- coding: utf-8 -*-
"""Library Index API — the display-only "what do I actually have of this artist?"
layer that /cleanup and /health hang their new column and detail panel off.

Thin control surface over the Library Job (start / resume / stop / status) plus two
read endpoints. Every endpoint is a fast web request — the heavy pull runs in the
Cloud Run Job — so nothing here approaches the ~26s proxy limit.

Read-only w.r.t. Spotify AND w.r.t. the scoring pipeline: this router never writes a
candidate, a weight, or a rank. Deleting cache/library_*.json returns the app to
exactly its previous behaviour.
"""
from fastapi import APIRouter, Request, HTTPException, BackgroundTasks

from .auth import get_spotify_client
from ..core.library import library, RECON_SNAPSHOT_FILE, EXCLUDED_PLAYLIST_TYPES
from ..core.library_trigger import trigger_library_job
from ..core.storage_manager import storage

router = APIRouter()


def _reject_if_running():
    # get_status() already downgrades a stale (>120s heartbeat) run to not-running,
    # so a True here means a genuinely live run → refuse a second.
    if library.get_status().get("is_running"):
        raise HTTPException(status_code=409, detail="A library index is already running")


def _scan_scope() -> int:
    """How many playlists the next run would read — everything except Out Of
    Playlist. Lets the button show the scope before you commit to it."""
    snap = storage.load_json(RECON_SNAPSHOT_FILE)
    if not snap:
        return 0
    return sum(1 for pl in snap.get("playlists", [])
               if pl.get("type") not in EXCLUDED_PLAYLIST_TYPES)


@router.post("/library/start")
async def start(request: Request, background_tasks: BackgroundTasks):
    """Kick off a fresh index build. Requires a Recon snapshot (for the playlist
    list) — Liked Songs alone would answer only half the question."""
    sp = get_spotify_client(request)
    _reject_if_running()
    if not storage.exists(RECON_SNAPSHOT_FILE):
        raise HTTPException(400, "No Recon snapshot yet — run Playlist Recon (Stage 1) first.")
    result = trigger_library_job("run", sp=sp, background_tasks=background_tasks)
    if result.get("status") == "error":
        raise HTTPException(503, result.get("message", "could not start the library index"))
    return {"status": "started", "trigger": result}


@router.post("/library/resume")
async def resume(request: Request, background_tasks: BackgroundTasks):
    """Continue an interrupted index build from its checkpoint."""
    sp = get_spotify_client(request)
    _reject_if_running()
    result = trigger_library_job("resume", sp=sp, background_tasks=background_tasks)
    if result.get("status") == "error":
        raise HTTPException(503, result.get("message", "could not resume the library index"))
    return {"status": "resumed", "trigger": result}


@router.post("/library/stop")
def stop(request: Request):
    """Request a stop; the Job checkpoints and exits at the next safe point."""
    get_spotify_client(request)  # session-gate
    library.request_stop()
    return {"status": "stopping"}


@router.get("/library/status")
def status(request: Request):
    """Run state + progress + whether an index exists and how old it is."""
    get_spotify_client(request)  # session-gate
    st = library.get_status()
    st["scan_scope"] = _scan_scope()
    st["recon_ready"] = storage.exists(RECON_SNAPSHOT_FILE)
    return st


@router.get("/library/counts")
def counts(request: Request):
    """{artist_uri: {liked, playlists, songs}} for every followed artist that has
    anything in the library. Artists with nothing are simply absent — the client
    treats a miss as zeros, which keeps this payload small."""
    get_spotify_client(request)  # session-gate
    return library.counts()


@router.get("/library/artist/{artist}")
def artist(artist: str, request: Request):
    """The detail panel for ONE artist: every song of theirs in the library, each
    flagged liked / not and listing the playlists it sits in. Accepts a bare id, a
    uri, or an open.spotify.com link."""
    get_spotify_client(request)  # session-gate
    return library.artist(artist)
