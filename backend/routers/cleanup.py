"""
Artist cleanup (Stage 1) — direct, reversible bulk-unfollow.

Robust by design:
- The candidate list is filtered against the user's LIVE follows, so an unfollowed
  artist drops off automatically and nothing is hidden wrongly (no bookkeeping file
  that can drift out of sync).
- unfollow / undo hit the raw /me/following endpoint — some deployed spotipy helper
  versions call the wrong URL (/me/library/contains) and 400.
- ONLY artists that were actually removed are recorded to the manifest (the undo
  source). Failed batches (e.g. Spotify rate-limit) are reported, not recorded.
- Protected artists are never removed.
"""
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import time
import datetime

from .auth import get_spotify_client
from ..core.storage_manager import storage

router = APIRouter()

CANDIDATES_FILE = "cache/cleanup_candidates.json"
CLEANUP_DIR = "cache/cleanup"
LATEST_MANIFEST = f"{CLEANUP_DIR}/latest_manifest.json"
PROTECTED_FILE = f"{CLEANUP_DIR}/protected.json"


class UriList(BaseModel):
    uris: List[str] = []


class UnfollowReq(BaseModel):
    uris: List[str] = []
    manifest_id: Optional[str] = None


class UndoReq(BaseModel):
    manifest_id: Optional[str] = None
    uris: Optional[List[str]] = None


class ProtectReq(BaseModel):
    uri: str
    protected: bool = True


def _uri_to_id(uri: str) -> str:
    return uri.rsplit(":", 1)[-1] if uri else uri


def _ts() -> str:
    return datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")


def _retry_after_seconds(e) -> Optional[int]:
    """If e is a Spotify 429, the Retry-After in seconds (client keeps it because 429
    is excluded from the client's status_forcelist); otherwise None."""
    if getattr(e, "http_status", None) != 429:
        return None
    hdrs = getattr(e, "headers", None) or {}
    try:
        return max(1, int(hdrs.get("Retry-After", 1)))
    except Exception:
        return 1


def _following_flags(sp, ids: List[str]) -> List[bool]:
    """For each artist id (in order), does the user currently follow? Raw endpoint,
    batches of 50. On a check error, default to True (so nothing is dropped wrongly)."""
    flags: List[bool] = []
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        try:
            flags.extend(sp._get("me/following/contains", type="artist", ids=",".join(chunk)))
        except Exception:
            flags.extend([True] * len(chunk))
    return flags


def _load_protected() -> set:
    return set(storage.load_json(PROTECTED_FILE, default=[]) or [])


def _save_protected(uris: set):
    storage.save_json(PROTECTED_FILE, sorted(uris))


@router.get("/cleanup/candidates")
def get_candidates(request: Request):
    """Serve candidates filtered to those the user STILL follows (self-healing) —
    already-unfollowed artists drop off; protected ones stay (flagged in the UI)."""
    data = storage.load_json(CANDIDATES_FILE, default=None)
    if data is None:
        raise HTTPException(404, "No candidates file found.")
    cands = data.get("candidates", [])
    sp = get_spotify_client(request)
    flags = _following_flags(sp, [c["artist_id"] for c in cands])
    live = [c for c, f in zip(cands, flags) if f]
    return {**data, "candidates": live, "count": len(live),
            "protected": sorted(_load_protected())}


@router.post("/cleanup/dry-run")
def dry_run(request: Request, body: UriList):
    sp = get_spotify_client(request)
    uniq = list(dict.fromkeys(body.uris))
    if not uniq:
        return {"requested": 0, "currently_followed": 0, "not_followed": 0}
    flags = _following_flags(sp, [_uri_to_id(u) for u in uniq])
    followed = sum(1 for f in flags if f)
    return {"requested": len(uniq), "currently_followed": followed,
            "not_followed": len(uniq) - followed}


@router.post("/cleanup/unfollow")
def unfollow(request: Request, body: UnfollowReq):
    """Resumable bulk-unfollow. Processes a time-boxed slice of `uris` per call so the
    request stays under the proxy limit; records ONLY real successes to a manifest that
    accumulates across resumes; and returns what's left + how long Spotify asked us to
    wait (retry_after) so the client resumes automatically with a live progress bar."""
    sp = get_spotify_client(request)
    queue = [u for u in dict.fromkeys(body.uris) if u]
    protected = _load_protected()
    queue = [u for u in queue if u not in protected]
    if not queue:
        raise HTTPException(400, "No removable artists (empty or all protected).")

    mid = body.manifest_id or _ts()
    mpath = f"{CLEANUP_DIR}/manifest_{mid}.json"
    manifest = storage.load_json(mpath) or {
        "created": mid, "user": sp.current_user().get("id"), "unfollowed_uris": []}

    removed_now, errors, retry_after = [], [], 0
    start = time.time()
    i = 0
    while i < len(queue):
        if time.time() - start > 15.0:      # stay well under the ~26s proxy limit
            break
        chunk = queue[i:i + 50]
        cids = ",".join(_uri_to_id(u) for u in chunk)
        try:
            sp._delete("me/following?type=artist&ids=" + cids)
            removed_now.extend(chunk)
            i += 50
            time.sleep(0.1)
        except Exception as e:
            ra = _retry_after_seconds(e)
            if ra is None:                  # not a rate-limit → real error, stop
                errors.append(str(e))
                break
            if ra <= 3:                     # short wait — absorb it and retry the chunk
                time.sleep(ra + 0.5)
                continue
            retry_after = ra                # long wait — hand back to the client
            break

    remaining = queue[i:]
    manifest["unfollowed_uris"] = list(dict.fromkeys(manifest["unfollowed_uris"] + removed_now))
    manifest["count"] = len(manifest["unfollowed_uris"])
    storage.save_json(mpath, manifest)
    storage.save_json(LATEST_MANIFEST, manifest)

    return {"manifest_id": mid, "unfollowed": len(removed_now),
            "total_unfollowed": manifest["count"], "remaining_uris": remaining,
            "retry_after": retry_after, "done": not remaining, "errors": errors[:2]}


@router.post("/cleanup/undo")
def undo(request: Request, body: UndoReq):
    """Re-follow via raw PUT /me/following. Pass uris, a manifest_id, or neither (latest)."""
    sp = get_spotify_client(request)
    uris = body.uris
    if not uris:
        m = (storage.load_json(f"{CLEANUP_DIR}/manifest_{body.manifest_id}.json")
             if body.manifest_id else storage.load_json(LATEST_MANIFEST))
        if not m:
            raise HTTPException(404, "No manifest found to undo.")
        uris = m.get("unfollowed_uris", [])
    uniq = list(dict.fromkeys(uris))
    done, errors = 0, []
    for i in range(0, len(uniq), 50):
        cids = ",".join(_uri_to_id(u) for u in uniq[i:i + 50])
        try:
            sp._put("me/following?type=artist&ids=" + cids)
            done += len(uniq[i:i + 50])
        except Exception as e:
            errors.append(str(e))
        time.sleep(0.2)
    return {"refollowed": done, "errors": errors[:3]}


@router.post("/cleanup/protect")
def protect(request: Request, body: ProtectReq):
    """Mark an artist protected (never removable), or unprotect it."""
    get_spotify_client(request)  # session-gate
    p = _load_protected()
    p.add(body.uri) if body.protected else p.discard(body.uri)
    _save_protected(p)
    return {"uri": body.uri, "protected": body.protected, "protected_count": len(p)}


@router.get("/cleanup/latest-manifest")
def latest_manifest():
    m = storage.load_json(LATEST_MANIFEST)
    if not m:
        return {"exists": False}
    return {"exists": True, "manifest_id": m.get("created"), "count": m.get("count")}


@router.get("/cleanup/follow-count")
def follow_count(request: Request):
    """Live count of artists the user currently follows (one cheap paging call)."""
    sp = get_spotify_client(request)
    total = (sp.current_user_followed_artists(limit=1).get("artists") or {}).get("total")
    return {"count": total, "fetched_at": _ts()}
