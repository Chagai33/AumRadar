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
from ..core.bootstrap import bootstrap
from ..core.storage_manager import storage

router = APIRouter()

RECON_SNAPSHOT_FILE = "cache/recon_playlists.json"


class MeasureLink(BaseModel):
    week_number: int
    playlist_uri: str
    # Optional: the backend finds the scan that actually contains the playlist. A value
    # here is only a hint about where to start the search, never a decision.
    scan_id: Optional[str] = None
    oop_playlist_uri: Optional[str] = None


class MeasureReq(BaseModel):
    links: List[MeasureLink] = []


class QualityReq(BaseModel):
    """Live-tunable Release Quality knobs (§16 — 'decide when you see it live')."""
    half_life: float = 26.0
    secondary_weight: float = 0.3
    shadow_factor: float = 0.05
    smoothing_k: float = 4.0
    smoothing_prior: float = 0.3
    min_primary: int = 4          # min own (primary) matured singles before flooder-eligible (§22.3)
    threshold: float = 0.15       # flooder ⟺ efficiency < threshold


class ToCleanupReq(BaseModel):
    artist_uris: List[str] = []


CANDIDATES_FILE = "cache/cleanup_candidates.json"


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
    # The scan index lets measure_and_write verify the proposed link against the
    # playlist's actual contents and fall back to a neighbouring scan if it is wrong.
    history = scanner.get_history_index()
    scan_cache = {}          # one snapshot fetch per scan for the whole batch
    out, remaining, start = [], [], time.time()
    for i, lk in enumerate(body.links):
        if time.time() - start > 20.0:
            remaining = [l.dict() for l in body.links[i:]]
            break
        try:
            summary = release.measure_and_write(sp, lk.week_number, lk.playlist_uri,
                                                lk.scan_id, lk.oop_playlist_uri,
                                                source="weekly", history=history,
                                                scan_cache=scan_cache)
            out.append({"week_number": lk.week_number, "ok": True, **summary})
        except (release.NoMatchingScan, release.PlaylistUnreadable) as e:
            # NOT an error to bury: the week is deliberately left unmeasured rather
            # than recorded as an all-missed week built on the wrong denominator.
            out.append({"week_number": lk.week_number, "ok": False, "skipped": True,
                        "error": str(e)})
        except Exception as e:
            out.append({"week_number": lk.week_number, "ok": False, "error": str(e)})
    return {"measured": out, "remaining": remaining, "done": not remaining}


@router.post("/release/quality")
def quality(request: Request, body: QualityReq):
    """The forward efficiency axis: per-artist Release Quality → the 'flooder' review
    list (own-primary-releases ≥ min AND efficiency < threshold), sorted worst-first.
    SEPARATE from the RANK candidates — a 'for review' list, not auto-fed to /cleanup."""
    get_spotify_client(request)  # session-gate
    followed = bootstrap._load_followed()          # {uri: artist obj}
    events, weeks = release.aggregate_events()
    if not events:
        return {"flooders": [], "flooder_count": 0, "measured_weeks": len(weeks),
                "scored_artists": 0, "current_week": None,
                "note": "No measured weeks yet — measure a week on the 📆 Weekly page first."}
    current_week = max((e.get("release_week") or 0) for e in events)
    q = release.release_quality(
        events, set(followed.keys()), current_week=current_week,
        half_life=body.half_life, secondary_weight=body.secondary_weight,
        shadow_factor=body.shadow_factor, smoothing_k=body.smoothing_k,
        smoothing_prior=body.smoothing_prior)
    flooders = []
    for uri, a in q.items():
        if a["primary_releases"] >= body.min_primary and a["efficiency"] < body.threshold:
            row = bootstrap._candidate_row(uri, followed.get(uri) or {})
            row.update({"efficiency": a["efficiency"], "releases": a["releases"],
                        "hits": a["hits"], "primary_releases": a["primary_releases"]})
            flooders.append(row)
    flooders.sort(key=lambda r: (r["efficiency"], -r["primary_releases"]))
    return {"flooders": flooders, "flooder_count": len(flooders),
            "measured_weeks": len(weeks), "scored_artists": len(q),
            "current_week": current_week}


@router.post("/release/to-cleanup")
def to_cleanup(request: Request, body: ToCleanupReq):
    """Phase 4: send REVIEWED flooders to /cleanup — APPEND them as candidate rows to
    cleanup_candidates.json (dedup by uri, auto-backup first). The flooder axis stays
    a deliberate 'for review → send' action, never auto-merged (§10). /cleanup then
    handles them exactly like any candidate (live-filtered vs follows + the manual
    layer). Note: a later Health 'Apply' overwrites this file — send flooders when
    you're about to act on them."""
    get_spotify_client(request)  # session-gate
    uris = [u for u in dict.fromkeys(body.artist_uris) if u]
    if not uris:
        raise HTTPException(400, "No artists to send.")
    followed = bootstrap._load_followed()
    data = storage.load_json(CANDIDATES_FILE)
    if data is None:
        data = {"generated": release._ts(), "threshold": 0,
                "window": "flooders (release quality)", "count": 0, "candidates": []}
    else:
        storage.save_json(f"cache/cleanup_candidates_backup_{release._ts()}.json", data)
    existing = {c.get("artist_uri") for c in data.get("candidates", [])}
    added = 0
    for uri in uris:
        if uri in existing:
            continue
        row = bootstrap._candidate_row(uri, followed.get(uri) or {})
        row["tier"] = "flooder"
        data["candidates"].append(row)
        existing.add(uri)
        added += 1
    data["count"] = len(data["candidates"])
    storage.save_json(CANDIDATES_FILE, data)
    return {"added": added, "total": data["count"]}
