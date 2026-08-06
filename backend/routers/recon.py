"""
Artist Health Engine — Stage 1: Recon (read-only).

Pulls ALL the user's playlists via the Spotify API, auto-classifies each
(weekly / outofplaylist / other), extracts the week number, and builds a
registration table the user reviews to decide which `other` playlists feed the
engine. This snapshot is the input to Stage 2 (Bootstrap).

Read-only: nothing is written to Spotify. Output is a JSON snapshot in GCS
(SQLite is deferred to Bootstrap, per DESIGN.md). Weekly + outofplaylist are
always included; only `other` playlists carry a user override.
"""
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from typing import Optional
import re
import time
import datetime

from .auth import get_spotify_client
from ..core.storage_manager import storage

router = APIRouter()

SNAPSHOT_FILE = "cache/recon_playlists.json"
INCLUSIONS_FILE = "cache/recon_inclusions.json"

# Auto-classified types always feed the engine, regardless of the inclusions map.
AUTO_INCLUDED_TYPES = ("weekly", "outofplaylist")

# "Week#321" / "Aum#321" (optional spaces): the official weekly playlists.
WEEKLY_RE = re.compile(r"(?:week|aum)\s*#\s*(\d+)", re.IGNORECASE)
# "#321 Outofplaylist" — the shadow layer.
OOP_NUM_RE = re.compile(r"#\s*(\d+)")


class IncludeReq(BaseModel):
    playlist_uri: str
    included: bool = True


def _ts() -> str:
    return datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")


def _classify(name: str):
    """(type, week_number) for a playlist name. type ∈ weekly|outofplaylist|other."""
    n = name or ""
    if "outofplaylist" in n.lower():
        m = OOP_NUM_RE.search(n)
        return "outofplaylist", (int(m.group(1)) if m else None)
    m = WEEKLY_RE.search(n)
    if m:
        return "weekly", int(m.group(1))
    return "other", None


def _load_inclusions() -> dict:
    return storage.load_json(INCLUSIONS_FILE, default={}) or {}


def _is_included(pl: dict, inclusions: dict) -> bool:
    if pl.get("type") in AUTO_INCLUDED_TYPES:
        return True
    return bool(inclusions.get(pl.get("playlist_uri"), False))


def _retry_after_seconds(e) -> Optional[int]:
    """If e is a Spotify 429, its Retry-After in seconds; otherwise None.
    (The client keeps 429 out of its retry list so the header survives.)"""
    if getattr(e, "http_status", None) != 429:
        return None
    hdrs = getattr(e, "headers", None) or {}
    try:
        return max(1, int(hdrs.get("Retry-After", 1)))
    except Exception:
        return 1


def _as_http_error(e) -> HTTPException:
    """Convert a Spotify exception into a user-facing HTTPException: a rate-limit
    surfaces the wait (429); anything else becomes a 502."""
    ra = _retry_after_seconds(e)
    if ra is not None:
        return HTTPException(429, f"Spotify rate limit — retry in ~{ra}s.")
    return HTTPException(502, f"Spotify error while reading your library: {e}")


@router.post("/recon/scan")
def scan(request: Request):
    """Page through /me/playlists, keep the ones the user OWNS, classify each, and
    save a snapshot. All fields (name, owner, tracks.total, images) come back in the
    listing itself, so this is ~a dozen cheap calls — well within one web request.
    Followed-but-not-owned playlists are counted but not classified."""
    sp = get_spotify_client(request)

    # Guard: without playlist-read-private, /me/playlists silently returns only PUBLIC
    # playlists (no error) — a partial snapshot that looks complete. Fail loudly instead,
    # so the user knows to re-consent. (The session token carries the granted scope.)
    granted = set(((request.session.get("token_info") or {}).get("scope") or "").split())
    if "playlist-read-private" not in granted:
        raise HTTPException(403, "Missing playlist read permission — log out and log back "
                                 "in to grant it, then scan again.")

    try:
        me_id = sp.current_user().get("id")
    except Exception as e:
        raise _as_http_error(e)

    playlists = []
    followed_count = 0
    offset = 0
    start = time.time()
    while True:
        if time.time() - start > 22.0:      # defensive: stay under the ~26s proxy cap
            raise HTTPException(504, "Playlist scan took too long — please try again.")
        try:
            page = sp.current_user_playlists(limit=50, offset=offset)
        except Exception as e:
            ra = _retry_after_seconds(e)
            if ra is not None and ra <= 3:   # short wait — absorb it and retry the same page
                time.sleep(ra + 0.5)
                continue
            raise _as_http_error(e)          # long rate-limit → 429, anything else → 502

        items = (page or {}).get("items") or []
        for pl in items:
            if not pl:
                continue
            owner = pl.get("owner") or {}
            if owner.get("id") != me_id:
                followed_count += 1
                continue
            ptype, week = _classify(pl.get("name", ""))
            imgs = pl.get("images") or []
            playlists.append({
                "playlist_uri": pl.get("uri"),
                "playlist_id": pl.get("id"),
                "name": pl.get("name"),
                "owner_id": owner.get("id"),
                "owner_name": owner.get("display_name"),
                "type": ptype,
                "week_number": week,
                "track_count": (pl.get("tracks") or {}).get("total", 0),
                "image": imgs[0]["url"] if imgs else None,
                "spotify_url": (pl.get("external_urls") or {}).get("spotify"),
            })

        if page and page.get("next"):
            offset += 50
        else:
            break

    counts = {"weekly": 0, "outofplaylist": 0, "other": 0}
    for pl in playlists:
        counts[pl["type"]] = counts.get(pl["type"], 0) + 1

    snapshot = {
        "generated": _ts(),
        "owner_id": me_id,
        "total": len(playlists),
        "counts": counts,
        "followed_count": followed_count,
        "playlists": playlists,
    }
    if not storage.save_json(SNAPSHOT_FILE, snapshot):
        raise HTTPException(500, "Scanned your playlists but saving the snapshot failed — "
                                 "please try again.")
    return {"total": len(playlists), "counts": counts, "followed_count": followed_count,
            "generated": snapshot["generated"]}


@router.get("/recon/playlists")
def get_playlists(request: Request):
    """Return the saved snapshot with each row's `included` resolved against the
    inclusions map (weekly/outofplaylist are always included)."""
    get_spotify_client(request)  # session-gate
    snap = storage.load_json(SNAPSHOT_FILE, default=None)
    if snap is None:
        raise HTTPException(404, "No Recon snapshot yet — run a scan first.")
    inclusions = _load_inclusions()
    rows = []
    included_count = 0
    for pl in snap.get("playlists", []):
        inc = _is_included(pl, inclusions)
        included_count += 1 if inc else 0
        rows.append({**pl, "included": inc,
                     "auto_included": pl.get("type") in AUTO_INCLUDED_TYPES})
    return {**snap, "playlists": rows, "included_count": included_count}


@router.post("/recon/include")
def set_include(request: Request, body: IncludeReq):
    """Mark an `other` playlist as included (or not) in the engine. weekly/outofplaylist
    are always included and can't be toggled off here."""
    get_spotify_client(request)  # session-gate
    inclusions = _load_inclusions()
    if body.included:
        inclusions[body.playlist_uri] = True
    else:
        inclusions.pop(body.playlist_uri, None)
    if not storage.save_json(INCLUSIONS_FILE, inclusions):
        raise HTTPException(500, "Could not save the inclusion change — please try again.")
    return {"playlist_uri": body.playlist_uri, "included": body.included,
            "included_overrides": len(inclusions)}
