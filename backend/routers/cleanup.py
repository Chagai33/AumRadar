"""
Artist cleanup (Stage 1) — direct, reversible bulk-unfollow.

Simple by design: we do NOT fetch the whole follow list (that timed out at the
Netlify proxy). We check/act only on the SELECTED artists. Unfollow is idempotent
(safe even if not followed), and the manifest of what we actually removed is the
undo source (re-follow exactly those).
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
REMOVED_FILE = f"{CLEANUP_DIR}/removed.json"   # every URI already unfollowed → hidden from candidates


class UriList(BaseModel):
    uris: List[str] = []


class UndoReq(BaseModel):
    manifest_id: Optional[str] = None
    uris: Optional[List[str]] = None


def _uri_to_id(uri: str) -> str:
    return uri.rsplit(":", 1)[-1] if uri else uri


def _ts() -> str:
    return datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")


def _following_flags(sp, ids: List[str]) -> List[bool]:
    """For each artist id (in order), is the user currently following? Batches of 50."""
    flags: List[bool] = []
    for i in range(0, len(ids), 50):
        flags.extend(sp.current_user_following_artists(ids[i:i + 50]))
    return flags


def _load_removed() -> set:
    return set(storage.load_json(REMOVED_FILE, default=[]) or [])


def _save_removed(uris: set):
    storage.save_json(REMOVED_FILE, sorted(uris))


@router.get("/cleanup/candidates")
def get_candidates():
    """Serve the candidate list, hiding artists already unfollowed (so they don't
    reappear after a refresh)."""
    data = storage.load_json(CANDIDATES_FILE, default=None)
    if data is None:
        raise HTTPException(404, "No candidates file found.")
    removed = _load_removed()
    cands = [c for c in data.get("candidates", []) if c.get("artist_uri") not in removed]
    return {**data, "candidates": cands, "count": len(cands), "removed_so_far": len(removed)}


@router.post("/cleanup/dry-run")
def dry_run(request: Request, body: UriList):
    """Fast: check only the SELECTED artists' follow status — no full-list fetch."""
    sp = get_spotify_client(request)
    uniq = list(dict.fromkeys(body.uris))
    if not uniq:
        return {"requested": 0, "currently_followed": 0, "not_followed": 0}
    ids = [_uri_to_id(u) for u in uniq]
    flags = _following_flags(sp, ids)
    followed = sum(1 for f in flags if f)
    return {"requested": len(uniq), "currently_followed": followed,
            "not_followed": len(uniq) - followed}


@router.post("/cleanup/unfollow")
def unfollow(request: Request, body: UriList):
    """Unfollow the selected directly (idempotent). Records ONLY the ones that were
    actually followed to the manifest, so undo re-follows exactly what we removed."""
    sp = get_spotify_client(request)
    uniq = list(dict.fromkeys(body.uris))
    if not uniq:
        raise HTTPException(400, "No uris provided.")
    me = sp.current_user()  # account-safety: confirm whose account this is
    ids = [_uri_to_id(u) for u in uniq]

    # Which were actually followed (fast, selected only) — for the manifest + feedback.
    flags = _following_flags(sp, ids)
    actually = [uniq[i] for i, f in enumerate(flags) if f]

    # Unfollow all selected (idempotent; the not-followed ones are no-ops).
    done, errors = 0, []
    for i in range(0, len(ids), 50):
        try:
            sp.user_unfollow_artists(ids[i:i + 50])
            done += len(ids[i:i + 50])
        except Exception as e:
            errors.append(str(e))
        time.sleep(0.2)

    # Manifest = exactly what we removed → the undo source.
    ts = _ts()
    manifest = {"created": ts, "user": me.get("id"),
                "unfollowed_uris": actually, "count": len(actually)}
    storage.save_json(f"{CLEANUP_DIR}/manifest_{ts}.json", manifest)
    storage.save_json(LATEST_MANIFEST, manifest)

    # Hide the removed artists from the candidates list (persists across refreshes).
    if actually:
        _save_removed(_load_removed() | set(actually))

    return {"manifest_id": ts, "requested": len(uniq),
            "were_followed": len(actually), "not_followed": len(uniq) - len(actually),
            "errors": errors}


@router.post("/cleanup/undo")
def undo(request: Request, body: UndoReq):
    """Re-follow. Pass explicit `uris`, a `manifest_id`, or neither (latest manifest)."""
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
        try:
            sp.user_follow_artists(ids[i:i + 50])
            done += len(ids[i:i + 50])
        except Exception as e:
            errors.append(str(e))
        time.sleep(0.2)
    # Re-followed artists become candidates again.
    _save_removed(_load_removed() - set(dict.fromkeys(uris)))
    return {"refollowed": done, "errors": errors}


@router.get("/cleanup/latest-manifest")
def latest_manifest():
    m = storage.load_json(LATEST_MANIFEST)
    if not m:
        return {"exists": False}
    return {"exists": True, "manifest_id": m.get("created"), "count": m.get("count")}
