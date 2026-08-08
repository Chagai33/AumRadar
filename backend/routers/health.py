# -*- coding: utf-8 -*-
"""Artist Health Engine — Stage 3: Health API (STAGE3.md §4א).

Thin control surface over the HealthEngine. Every endpoint is a fast web request
(compute is in-memory, < 1s — no Cloud Run Job, unlike Bootstrap's pull) and is
session-gated like the rest of the app. Read-only w.r.t. Spotify: these endpoints only
READ cache files and WRITE cache files (cleanup_candidates / health_weights /
artist_manual). They never unfollow — /cleanup stays the only place removal happens.

Endpoints (STAGE3.md §4א.3-8):
  POST /health/preview        live band counts for a set of knobs
  POST /health/ranked         the sorted, paginated list (name search filters this client-side)
  POST /health/artist/{uri}   the per-artist "why" breakdown (accepts bare id / uri / link)
  POST /health/apply          write cleanup_candidates.json + persist the knobs
  POST /health/manual         write one artist's manual override
  GET  /health/weights        the active knobs, to re-open the sliders where you left them
"""
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from typing import List, Optional

from .auth import get_spotify_client
from ..core.bootstrap import HALF_LIFE_WEEKS, TYPE_WEIGHT, LEGACY_MULT
from ..core.health import health, ColdStartError, DEFAULT_WEIGHTS, DEFAULT_BANDS, WEIGHTS_FILE
from ..core import manual_store
from ..core.storage_manager import storage

router = APIRouter()


class Weights(BaseModel):
    """The tuning knobs that ride with every weights-dependent call (STAGE3.md §11.1) so
    a drill-down / artist panel is always consistent with the live counts, not a stale
    default. Defaults = the values locked in Bootstrap."""
    half_life: float = HALF_LIFE_WEEKS
    weekly: float = TYPE_WEIGHT["weekly"]
    outof: float = TYPE_WEIGHT["outofplaylist"]
    other: float = TYPE_WEIGHT["other"]
    legacy: float = LEGACY_MULT
    bands: List[float] = list(DEFAULT_BANDS)
    candidate_bands: List[str] = ["red"]

    def type_weight(self) -> dict:
        return {"weekly": self.weekly, "outofplaylist": self.outof, "other": self.other}

    def safe_bands(self) -> List[float]:
        b = self.bands
        return list(b) if isinstance(b, list) and len(b) == 3 else list(DEFAULT_BANDS)


class RankedReq(Weights):
    """Weights + paging for the list endpoint (POST so bands[] + knobs travel in a clean
    JSON body, not a messy query string — STAGE3.md §12.6)."""
    offset: int = 0
    limit: int = 100
    band: Optional[str] = None


class ManualReq(BaseModel):
    artist_uri: str
    disposition: Optional[str] = None          # protect | remove | none
    score_floor: Optional[float] = None
    pinned_until: Optional[str] = None
    notes: Optional[str] = None
    tags: Optional[List[str]] = None


_COLD_START = ("No song data yet — run 'Refresh data' (Bootstrap) once to enable live "
               "tuning.")


@router.post("/health/preview")
def preview(request: Request, w: Weights):
    """Live 🔴🟠🟡🟢 counts + candidate total for a set of knobs. Sub-second (§4א.3)."""
    get_spotify_client(request)  # session-gate
    try:
        return health.preview(w)
    except ColdStartError:
        raise HTTPException(409, _COLD_START)


@router.post("/health/ranked")
def ranked(request: Request, body: RankedReq):
    """The sorted, paginated artist list consistent with the live counts (§4א.5)."""
    get_spotify_client(request)  # session-gate
    try:
        return health.ranked(body, offset=body.offset, limit=body.limit, band=body.band)
    except ColdStartError:
        raise HTTPException(409, _COLD_START)


@router.post("/health/artist/{uri:path}")
def artist(uri: str, request: Request, w: Weights):
    """The 'why' breakdown for one artist (§4א.4). `uri:path` so a bare id, a
    spotify:artist:… uri, or a pasted open.spotify.com link all route here."""
    get_spotify_client(request)  # session-gate
    try:
        return health.artist(uri, w)
    except ColdStartError:
        raise HTTPException(409, _COLD_START)


@router.post("/health/apply")
def apply(request: Request, w: Weights):
    """Write the current banding to cleanup_candidates.json (auto-backed-up) and save the
    knobs (§4א.6). Feeds /cleanup; never removes anyone."""
    get_spotify_client(request)  # session-gate
    try:
        return health.apply(w)
    except ColdStartError:
        raise HTTPException(409, _COLD_START)


@router.post("/health/manual")
def manual(request: Request, body: ManualReq):
    """Write ONE artist's manual override via the shared safe read-merge-write (§4א.7)."""
    get_spotify_client(request)  # session-gate
    if body.disposition is not None and body.disposition not in manual_store.VALID_DISPOSITIONS:
        raise HTTPException(400, f"disposition must be one of {manual_store.VALID_DISPOSITIONS}")
    try:
        rec = manual_store.update(body.artist_uri, {
            "disposition": body.disposition, "score_floor": body.score_floor,
            "pinned_until": body.pinned_until, "notes": body.notes, "tags": body.tags})
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"artist_uri": body.artist_uri, "manual": rec}


@router.get("/health/weights")
def weights(request: Request):
    """The active knobs so the dashboard re-opens the sliders where you left them; the
    locked defaults if apply hasn't run yet (§4א.8)."""
    get_spotify_client(request)  # session-gate
    return storage.load_json(WEIGHTS_FILE, default=DEFAULT_WEIGHTS)
