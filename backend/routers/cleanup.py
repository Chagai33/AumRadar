"""
Artist cleanup (Stage 1) — safe, reversible bulk-unfollow of removal candidates.

Flow: backup -> dry-run -> unfollow (batched, idempotent, always backs up first,
saves a manifest) -> verify -> undo (re-follow from the manifest).

Candidates are precomputed (releases vs playlist entries) and served from storage;
this router only handles the review-data delivery + the account mutation, all under
the user's own Spotify session (scopes user-follow-read/modify already granted).
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
CLEANUP_DIR = "cache/cleanup"                       # backups + manifests
LATEST_MANIFEST = f"{CLEANUP_DIR}/latest_manifest.json"


class UriList(BaseModel):
    uris: List[str] = []


class UndoReq(BaseModel):
    manifest_id: Optional[str] = None
    uris: Optional[List[str]] = None


def _uri_to_id(uri: str) -> str:
    """spotify:artist:XXXX -> XXXX (the API wants bare ids)."""
    return uri.rsplit(":", 1)[-1] if uri else uri


def _ts() -> str:
    return datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")


def _fetch_all_followed(sp) -> List[dict]:
    """Every artist the user currently follows: [{id, uri, name}]. Cursor-paginated."""
    artists, after = [], None
    while True:
        res = sp.current_user_followed_artists(limit=50, after=after)
        block = res.get("artists", {}) if res else {}
        items = block.get("items", []) or []
        for a in items:
            artists.append({"id": a["id"], "uri": a["uri"], "name": a.get("name")})
        after = (block.get("cursors") or {}).get("after")
        if not after or not items:
            break
    return artists


@router.get("/cleanup/candidates")
def get_candidates():
    """Serve the precomputed removal-candidate list for the review UI."""
    data = storage.load_json(CANDIDATES_FILE, default=None)
    if data is None:
        raise HTTPException(404, "No candidates file found. Generate it first.")
    return data


@router.post("/cleanup/backup")
def backup_follows(request: Request):
    """Snapshot the FULL current follow list to storage — the undo foundation."""
    sp = get_spotify_client(request)
    follows = _fetch_all_followed(sp)
    ts = _ts()
    storage.save_json(f"{CLEANUP_DIR}/backup_{ts}.json",
                      {"created": ts, "count": len(follows), "artists": follows})
    return {"backup_id": ts, "count": len(follows)}


@router.post("/cleanup/dry-run")
def dry_run(request: Request, body: UriList):
    """Report exactly what an unfollow would do — WITHOUT changing anything."""
    sp = get_spotify_client(request)
    followed = {a["uri"] for a in _fetch_all_followed(sp)}
    uniq = list(dict.fromkeys(body.uris))
    to_remove = [u for u in uniq if u in followed]
    already_gone = [u for u in uniq if u not in followed]
    return {
        "requested": len(uniq),
        "will_unfollow": len(to_remove),
        "already_not_followed": len(already_gone),
        "current_follow_count": len(followed),
        "after_count": len(followed) - len(to_remove),
        "sample": to_remove[:10],
    }


@router.post("/cleanup/unfollow")
def unfollow(request: Request, body: UriList):
    """Backup -> unfollow (only those actually followed, batches of 50, idempotent)
    -> save manifest -> verify. Re-running is safe (unfollowing a non-follow is a no-op)."""
    sp = get_spotify_client(request)
    uniq = list(dict.fromkeys(body.uris))
    if not uniq:
        raise HTTPException(400, "No uris provided.")

    # Account-safety: confirm whose account we're touching.
    me = sp.current_user()

    # 1. Always back up first, even if the UI already did.
    follows = _fetch_all_followed(sp)
    followed_uris = {a["uri"] for a in follows}
    ts = _ts()
    storage.save_json(f"{CLEANUP_DIR}/backup_{ts}.json",
                      {"created": ts, "user": me.get("id"),
                       "count": len(follows), "artists": follows})

    # 2. Only act on artists actually still followed.
    to_remove = [u for u in uniq if u in followed_uris]
    ids = [_uri_to_id(u) for u in to_remove]
    done, errors = 0, []
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        try:
            sp.user_unfollow_artists(chunk)
            done += len(chunk)
        except Exception as e:
            errors.append(str(e))
        time.sleep(0.3)  # gentle pacing — 15 calls max, well under any limit

    # 3. Manifest = the exact set we unfollowed (the undo source). Keep a "latest" pointer.
    manifest = {"created": ts, "user": me.get("id"),
                "unfollowed_uris": to_remove, "count": len(to_remove)}
    storage.save_json(f"{CLEANUP_DIR}/manifest_{ts}.json", manifest)
    storage.save_json(LATEST_MANIFEST, manifest)

    # 4. Verify against the live follow list.
    after = {a["uri"] for a in _fetch_all_followed(sp)}
    still_following = [u for u in to_remove if u in after]
    return {
        "manifest_id": ts,
        "requested": len(uniq),
        "unfollowed": done,
        "skipped_not_followed": len(uniq) - len(to_remove),
        "errors": errors,
        "verify_still_following": len(still_following),
        "before_count": len(follows),
        "after_count": len(after),
    }


@router.post("/cleanup/undo")
def undo(request: Request, body: UndoReq):
    """Re-follow. Pass explicit `uris` (a subset), a `manifest_id`, or neither
    (uses the latest manifest)."""
    sp = get_spotify_client(request)
    uris = body.uris
    if not uris:
        m = (storage.load_json(f"{CLEANUP_DIR}/manifest_{body.manifest_id}.json")
             if body.manifest_id else storage.load_json(LATEST_MANIFEST))
        if not m:
            raise HTTPException(404, "No manifest found to undo.")
        uris = m.get("unfollowed_uris", [])

    ids = [_uri_to_id(u) for u in dict.fromkeys(uris)]
    done, errors = 0, []
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        try:
            sp.user_follow_artists(chunk)
            done += len(chunk)
        except Exception as e:
            errors.append(str(e))
        time.sleep(0.3)
    return {"refollowed": done, "errors": errors}


@router.get("/cleanup/latest-manifest")
def latest_manifest():
    """Info about the most recent unfollow batch (for the 'undo last' button)."""
    m = storage.load_json(LATEST_MANIFEST)
    if not m:
        return {"exists": False}
    return {"exists": True, "manifest_id": m.get("created"), "count": m.get("count")}
