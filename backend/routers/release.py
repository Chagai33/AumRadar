# -*- coding: utf-8 -*-
"""Artist Health Engine — Stage 4: Release measurement API (phase 2b).

Thin control surface over the release engine. Session-gated: uses the logged-in
user's client to read the (often private) weekly playlists. Fast web requests — the
per-week measure is a small playlist pull + in-memory fold; a big backlog is
time-boxed so the request stays under the Netlify ~26s proxy limit and the client
loops the remainder (like /cleanup).

  GET  /release/coverage   coverage state + the multi-week backlog with proposed links
  POST /release/measure    measure a batch of confirmed links (week ↔ scan ↔ playlist)
"""
import time
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from typing import List, Optional

from .auth import get_spotify_client
from ..core import release
from ..core.scanner import scanner
from ..core.storage_manager import storage

router = APIRouter()

RECON_SNAPSHOT_FILE = "cache/recon_playlists.json"


class MeasureLink(BaseModel):
    week_number: int
    playlist_uri: str
    scan_id: str
    oop_playlist_uri: Optional[str] = None


class MeasureReq(BaseModel):
    links: List[MeasureLink] = []


@router.get("/release/coverage")
def coverage(request: Request):
    """What's measured + the backlog of unmeasured weeks with a proposed scan↔playlist
    link (propose-and-confirm). The backlog can hold several weeks at once — the
    catch-up list when you didn't confirm for a while."""
    get_spotify_client(request)  # session-gate
    recon = storage.load_json(RECON_SNAPSHOT_FILE) or {}
    history = scanner.get_history_index()
    cov = release.load_coverage()
    return release.coverage_state(history, recon.get("playlists", []), cov)


@router.post("/release/measure")
def measure(request: Request, body: MeasureReq):
    """Measure a batch of CONFIRMED links (one or many weeks). Time-boxed ~20s; any
    remainder is returned so the client can loop, keeping each request under the proxy
    limit. Re-measuring a week just replaces it (idempotent)."""
    sp = get_spotify_client(request)
    if not body.links:
        raise HTTPException(400, "No weeks to measure.")
    out, remaining, start = [], [], time.time()
    for i, lk in enumerate(body.links):
        if time.time() - start > 20.0:
            remaining = [l.dict() for l in body.links[i:]]
            break
        try:
            summary = release.measure_and_write(sp, lk.week_number, lk.playlist_uri,
                                                lk.scan_id, lk.oop_playlist_uri, source="weekly")
            out.append({"week_number": lk.week_number, "ok": True, **summary})
        except Exception as e:
            out.append({"week_number": lk.week_number, "ok": False, "error": str(e)})
    return {"measured": out, "remaining": remaining, "done": not remaining}
