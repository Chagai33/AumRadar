"""
Artist Health Engine — Stage 1: Recon (read-only).

Pulls ALL the user's OWNED playlists via the Spotify API, classifies each
(weekly / outofplaylist / other), extracts week number + season, tags legacy
old-format playlists, and guesses a family for `other`. Read-only: nothing is
written to Spotify. Output is a JSON snapshot in GCS.

Classification is two-pass so old-format season-1 playlists (`#NN - <theme>`,
`#NN - Out Of Playlist`, and typo'd outofplaylists) are recognised as weekly /
outofplaylist — guarded so a bare `#NN` only becomes weekly when it's not a
range and no real `Week#NN` already exists (those high-numbers are the House
series / Purim events).

Inclusion feeds the engine: weekly + outofplaylist are included by default,
every other playlist only if the user opts it in. An explicit override can
include OR exclude ANY playlist (so a mis-classified weekly can be dropped).
"""
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from typing import Optional, List
import re
import time
import datetime

from .auth import get_spotify_client
from ..core.storage_manager import storage

router = APIRouter()

SNAPSHOT_FILE = "cache/recon_playlists.json"
OVERRIDES_FILE = "cache/recon_inclusions.json"
AUTO_INCLUDED_TYPES = ("weekly", "outofplaylist")

WEEKLY_RE = re.compile(r"(?:week|aum)\s*#\s*(\d+)", re.IGNORECASE)
NUM_RE = re.compile(r"#\s*(\d+)")
LEAD_NUM_RE = re.compile(r"^\s*#\s*(\d+)")
RANGE_RE = re.compile(r"^\s*#\s*\d+-\d+")          # "#1-25" (tight) = a range, not a week
SX_RE = re.compile(r"(?:^|\s)s\s*([1-9])(?:\s|$|[-_])", re.IGNORECASE)
OOP_TYPOS = ("outofplaylist", "outofolaylist", "outpfplaylist", "ootofplaylist")

GENRE_WORDS = ("rap", "hip hop", "hip-hop", "reggae", "reggaeton", "dancehall", "house",
               "techno", "trance", "indie", "rock", "pop", "mizrahi", "מזרחי", "afro",
               "electro", "electronic", "drum", "funk", "jazz", "latin", "trap", "soul",
               "ethnic", "disco", "ישראלי", "israeli", "arab", "folk", "acoustic")
MOOD_WORDS = ("meditation", "spa", "yoga", "gym", "run", "sleep", "relax", "morning",
              "chill", "study", "sad", "romantic", "happy", "late night", "vibe",
              "workout", "party", "summer", "beach", "fitness")


class IncludeReq(BaseModel):
    playlist_uri: str
    included: bool = True


class BatchReq(BaseModel):
    uris: List[str] = []
    included: bool = True


def _ts() -> str:
    return datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")


def _oop_match(name: str) -> bool:
    """Old / typo outofplaylist ('#28 - Out Of Playlist', '#179 Outofolaylist')."""
    s = re.sub(r"\s+", "", name or "").lower()
    return any(v in s for v in OOP_TYPOS)


def _sx_season(name: str) -> Optional[str]:
    m = SX_RE.search(name or "")
    return f"S{m.group(1)}" if m else None


def _season_of(wk: Optional[int], name: str) -> Optional[str]:
    if wk:
        return f"S{(wk - 1) // 100 + 1}"
    return _sx_season(name)


def _family(name: str) -> str:
    """Best-guess family for an `other` playlist — a display/grouping aid only."""
    n = name or ""
    low = n.lower()
    if "aio" in low or low.startswith("all ") or "all playlist" in low:
        return "aggregator"
    if "release" in low or re.search(r"multi\s*\d", low):
        return "aggregator"
    if "##" in n or "למיין" in n or "working" in low or low.startswith("check"):
        return "working"
    if re.search(r"\bdj\b", low) or "לתקלוט" in n or re.search(r"\bset\b|set\d", low):
        return "dj"
    if "favorit" in low or "מועדף" in n:
        return "favorite"
    if "best of" in low:
        return "bestof"
    if "להקשיב" in n or "shazam" in low or "like" in low:
        return "listen"
    if ("חתונה" in n or "birthday" in low or "יומולדת" in n or "רווקות" in n
            or "אינסטגרם" in n or "wedding" in low):
        return "personal"
    if "upim" in low or "siargao" in low or "sri lanka" in low or "rooftop" in low:
        return "trip"
    if ("彡" in n or "purim" in low or "פורים" in n or "עצמאות" in n or "ראש השנה" in n
            or "valentine" in low or "independence" in low):
        return "event"
    if any(g in low for g in GENRE_WORDS):
        return "genre"
    if any(m in low for m in MOOD_WORDS):
        return "mood"
    return "uncategorized"


def _classify_modern(name: str):
    """Current naming: '彡…Week#NNN' / 'Aum#NNN' and exact '#NNN Outofplaylist'."""
    n = name or ""
    if "outofplaylist" in n.lower():
        m = NUM_RE.search(n)
        return "outofplaylist", (int(m.group(1)) if m else None)
    m = WEEKLY_RE.search(n)
    if m:
        return "weekly", int(m.group(1))
    return "other", None


def _load_overrides() -> dict:
    return storage.load_json(OVERRIDES_FILE, default={}) or {}


def _is_included(pl: dict, overrides: dict) -> bool:
    uri = pl.get("playlist_uri")
    if uri in overrides:
        return bool(overrides[uri])
    return pl.get("type") in AUTO_INCLUDED_TYPES


def _retry_after_seconds(e) -> Optional[int]:
    if getattr(e, "http_status", None) != 429:
        return None
    hdrs = getattr(e, "headers", None) or {}
    try:
        return max(1, int(hdrs.get("Retry-After", 1)))
    except Exception:
        return 1


def _as_http_error(e) -> HTTPException:
    ra = _retry_after_seconds(e)
    if ra is not None:
        return HTTPException(429, f"Spotify rate limit — retry in ~{ra}s.")
    return HTTPException(502, f"Spotify error while reading your library: {e}")


@router.post("/recon/scan")
def scan(request: Request):
    """Page owned playlists, classify (two-pass, legacy-aware), save the snapshot."""
    sp = get_spotify_client(request)
    granted = set(((request.session.get("token_info") or {}).get("scope") or "").split())
    if "playlist-read-private" not in granted:
        raise HTTPException(403, "Missing playlist read permission — log out and log back "
                                 "in to grant it, then scan again.")
    try:
        me_id = sp.current_user().get("id")
    except Exception as e:
        raise _as_http_error(e)

    raw = []
    followed_count = 0
    offset = 0
    start = time.time()
    while True:
        if time.time() - start > 22.0:
            raise HTTPException(504, "Playlist scan took too long — please try again.")
        try:
            page = sp.current_user_playlists(limit=50, offset=offset)
        except Exception as e:
            ra = _retry_after_seconds(e)
            if ra is not None and ra <= 3:
                time.sleep(ra + 0.5)
                continue
            raise _as_http_error(e)
        for pl in ((page or {}).get("items") or []):
            if not pl:
                continue
            if (pl.get("owner") or {}).get("id") != me_id:
                followed_count += 1
                continue
            raw.append(pl)
        if page and page.get("next"):
            offset += 50
        else:
            break

    # pass 1 — modern classification
    entries = []
    for pl in raw:
        name = pl.get("name", "")
        t, wk = _classify_modern(name)
        entries.append({"pl": pl, "name": name, "type": t, "wk": wk, "legacy": False})
    modern_weekly_nums = {e["wk"] for e in entries if e["type"] == "weekly" and e["wk"]}

    # pass 2 — legacy old-format for anything still 'other'
    for e in entries:
        if e["type"] != "other":
            continue
        name = e["name"]
        if _oop_match(name):
            m = NUM_RE.search(name)
            e["type"] = "outofplaylist"
            e["wk"] = int(m.group(1)) if m else None
            e["legacy"] = True
        else:
            lm = LEAD_NUM_RE.match(name)
            if lm and not RANGE_RE.match(name) and int(lm.group(1)) not in modern_weekly_nums:
                e["type"] = "weekly"
                e["wk"] = int(lm.group(1))
                e["legacy"] = True

    playlists = []
    counts = {"weekly": 0, "outofplaylist": 0, "other": 0}
    for e in entries:
        pl = e["pl"]
        imgs = pl.get("images") or []
        playlists.append({
            "playlist_uri": pl.get("uri"),
            "playlist_id": pl.get("id"),
            "name": e["name"],
            "type": e["type"],
            "week_number": e["wk"],
            "season": _season_of(e["wk"], e["name"]),
            "legacy": e["legacy"],
            "family": _family(e["name"]) if e["type"] == "other" else None,
            "track_count": (pl.get("tracks") or {}).get("total", 0),
            "image": imgs[0]["url"] if imgs else None,
            "spotify_url": (pl.get("external_urls") or {}).get("spotify"),
        })
        counts[e["type"]] = counts.get(e["type"], 0) + 1

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
    get_spotify_client(request)  # session-gate
    snap = storage.load_json(SNAPSHOT_FILE, default=None)
    if snap is None:
        raise HTTPException(404, "No Recon snapshot yet — run a scan first.")
    overrides = _load_overrides()
    rows = []
    included = 0
    for pl in snap.get("playlists", []):
        inc = _is_included(pl, overrides)
        included += 1 if inc else 0
        rows.append({**pl, "included": inc,
                     "auto_included": pl.get("type") in AUTO_INCLUDED_TYPES})
    return {**snap, "playlists": rows, "included_count": included}


@router.post("/recon/include")
def set_include(request: Request, body: IncludeReq):
    """Explicitly include or exclude a single playlist (works on ANY type)."""
    get_spotify_client(request)  # session-gate
    overrides = _load_overrides()
    overrides[body.playlist_uri] = body.included
    if not storage.save_json(OVERRIDES_FILE, overrides):
        raise HTTPException(500, "Could not save the change — please try again.")
    return {"playlist_uri": body.playlist_uri, "included": body.included}


@router.post("/recon/include-batch")
def include_batch(request: Request, body: BatchReq):
    """Bulk include/exclude — one write for a whole group."""
    get_spotify_client(request)  # session-gate
    overrides = _load_overrides()
    for u in body.uris:
        if u:
            overrides[u] = body.included
    if not storage.save_json(OVERRIDES_FILE, overrides):
        raise HTTPException(500, "Could not save the changes — please try again.")
    return {"updated": len(body.uris), "included": body.included}
