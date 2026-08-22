# -*- coding: utf-8 -*-
"""Artist Health Engine — Library Index (display-only, NEVER touches the score).

Answers the one question RANK cannot: *before I unfollow this artist, what of
theirs do I actually have?* — how many of their songs I marked with a LIKE, and
which of my playlists their songs sit in.

WHY A GLOBAL INDEX AND NOT A PER-ARTIST FETCH
Spotify has no artist-scoped view of your library. `GET /me/tracks` returns ALL
saved tracks (50/page, no filter) and there is no "which playlists contain artist
X" endpoint at all. Fetching for ONE artist therefore costs exactly the same as
fetching for all 3,800 — so one pull builds the index for everybody, after which
every per-artist lookup is an in-memory dict hit instead of ~20 API calls.
(The alternative — walk the artist's discography, then /me/tracks/contains — is
slower per artist, silently MISSES tracks where they are only a featured artist,
and cannot answer the playlist half at all.)

SCOPE OF THE PULL: Liked Songs + every playlist EXCEPT the `outofplaylist` ones.
Deliberately independent of Recon's include/exclude flags: those drive the SCORE,
and this layer must never move a band, a RANK, or a cleanup candidate. It is a
read-only observation layer bolted alongside the engine, not into it.

Only songs crediting at least one FOLLOWED artist are kept — the whole feature
exists to inform an unfollow decision, so the rest is dead weight (and it keeps
the index a few MB instead of a few tens of MB).

Runs as a Cloud Run Job (backend.library_job) with the same checkpoint / stop /
heartbeat contract as Bootstrap, because ~1,000 paged reads take minutes.
"""
import time
import datetime
import threading
from typing import Optional

from .storage_manager import storage

CACHE_DIR = "cache"
INDEX_FILE = f"{CACHE_DIR}/library_index.json"          # full per-artist detail (MBs)
COUNTS_FILE = f"{CACHE_DIR}/library_counts.json"        # {artist_uri: counts} — the list column
STATE_FILE = f"{CACHE_DIR}/library_state.json"
CHECKPOINT_FILE = f"{CACHE_DIR}/library_checkpoint.json"
STOP_REQUEST_FILE = f"{CACHE_DIR}/library_stop_request.json"

RECON_SNAPSHOT_FILE = f"{CACHE_DIR}/recon_playlists.json"
ARTISTS_CACHE_FILE = f"{CACHE_DIR}/artists_cache.json"

# The user's rule: everything EXCEPT Out Of Playlist. Weekly + other are both in.
EXCLUDED_PLAYLIST_TYPES = ("outofplaylist",)

HEARTBEAT_ALIVE_SEC = 120
STARTING_GRACE_SEC = 300   # 'starting' is written the instant the Job is triggered, before
                           # the container exists, so it needs a longer window than a live
                           # run's heartbeat — a Cloud Run cold start can take a couple of
                           # minutes. It must still EXPIRE though: if the trigger itself
                           # fails (e.g. the 403 on 2026-08-22 before the Job had
                           # run.developer), the state is left claiming a run that will
                           # never start, and _reject_if_running then refuses every future
                           # start with 409 — permanently wedged, since nothing else clears
                           # it.
PAGE_LIMIT = 100                # Spotify max page size for playlist items
LIKED_PAGE_LIMIT = 50           # Spotify max page size for /me/tracks
CHECKPOINT_EVERY = 40           # persist the accumulator every N fully-pulled playlists
MAX_LIKED = 50000               # sanity ceiling so a pathological library can't run forever
# Same pacing rationale as Bootstrap: a sustained burst trips Spotify's SILENT
# long-window quota (the 2026-07-17 hard block), which does NOT arrive as a 429.
REQUEST_PACE_SEC = 0.25

# Only ask Spotify for the fields we actually fold — keeps each page small/cheap.
# `name` and `id` are here (unlike Bootstrap's fold) because this layer must SHOW
# the songs, not just count them.
_ITEM_FIELDS = "next,items(track(id,uri,name,type,external_ids(isrc),artists(id,uri)))"


def _ts() -> str:
    return datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")


def _uri_to_id(uri: str) -> str:
    return uri.rsplit(":", 1)[-1] if uri else uri


def normalize_uri(artist: str) -> str:
    """Canonical `spotify:artist:ID` from a bare id, a full uri, or an open.spotify.com
    link — mirrors health.normalize_uri so both panels accept the same input."""
    if not artist:
        return artist
    a = artist.strip()
    if "open.spotify.com/artist/" in a:
        a = a.split("artist/")[-1].split("?")[0].split("/")[0]
    a = a.rsplit(":", 1)[-1]
    return f"spotify:artist:{a}"


def _retry_after_seconds(e) -> Optional[int]:
    """If e is a Spotify 429, its Retry-After in seconds; else None. Works because
    the Job's client excludes 429 from status_forcelist, so the header survives
    (spotipy would otherwise swallow it). Mirrors bootstrap/recon/cleanup."""
    if getattr(e, "http_status", None) != 429:
        return None
    hdrs = getattr(e, "headers", None) or {}
    try:
        return max(1, int(hdrs.get("Retry-After", 1)))
    except Exception:
        return 1


class LibraryEngine:
    """Writer side runs in the Job; reader side runs in the Service. Both live here
    so the file format has exactly one owner. The reader mirrors HealthEngine: lazy,
    mtime-stamped in-memory caches, so a rebuilt index is picked up automatically and
    the hot path is a cheap metadata HEAD."""

    def __init__(self):
        self._lock = threading.Lock()
        self._counts = None            # {artist_uri: {liked,playlists,songs}}
        self._counts_stamp = None
        self._index = None             # the full detail payload
        self._index_stamp = None

    # ────────────────────────── state / heartbeat ──────────────────────────
    def _save_state(self, st: dict):
        st["heartbeat"] = time.time()
        storage.save_json(STATE_FILE, st)

    def _log(self, st: dict, msg: str):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        st.setdefault("logs", []).append(f"[{ts}] {msg}")
        if len(st["logs"]) > 40:
            st["logs"].pop(0)
        print(f"[library] {msg}", flush=True)

    def get_status(self) -> dict:
        """The frontend's single source of truth. Downgrades a run whose heartbeat went
        stale to not-running so a crashed Job can't wedge the UI — the same staleness rule
        Bootstrap and the scanner use. A run still in 'starting' gets a longer window
        (the container may not exist yet) but is NOT exempt: exempting it meant a trigger
        that failed before the Job ever booted left is_running=True forever."""
        st = storage.load_json(STATE_FILE) or {"is_running": False, "status": "idle"}
        if st.get("is_running"):
            hb = st.get("heartbeat", 0)
            limit = STARTING_GRACE_SEC if st.get("status") == "starting" else HEARTBEAT_ALIVE_SEC
            if not hb or (time.time() - hb) > limit:
                st["is_running"] = False
                if st.get("status") not in ("done", "error", "stopped"):
                    st["status"] = "interrupted"
        payload = self._load_counts()
        st["index_ready"] = bool(payload)
        if payload:
            st["stats"] = payload.get("stats") or {}
            st["generated"] = payload.get("generated")
        cp = storage.load_json(CHECKPOINT_FILE)
        st["resumable"] = bool(cp) and not st.get("is_running") and st.get("status") != "done"
        if cp:
            st["checkpoint"] = {"pulled": len(cp.get("pulled", [])),
                                "total": cp.get("total", 0)}
        return st

    # ─────────────────────────── stop handling ────────────────────────────
    def _stop_requested(self) -> bool:
        return storage.exists(STOP_REQUEST_FILE)

    def _clear_stop(self):
        storage.delete_file(STOP_REQUEST_FILE)

    def request_stop(self):
        storage.save_json(STOP_REQUEST_FILE, {"at": time.time()})

    def _sleep_alive(self, seconds: float, st: dict):
        """Sleep a (possibly long) rate-limit wait WITHOUT letting the heartbeat go
        stale and WITHOUT ignoring a stop — <=15s slices, each refreshing it."""
        end = time.time() + seconds
        prev = st.get("status")
        st["status"] = "rate_limited"
        st["retry_after"] = int(seconds)
        self._save_state(st)
        while True:
            remaining = end - time.time()
            if remaining <= 0 or self._stop_requested():
                break
            st["heartbeat"] = time.time()
            storage.save_json(STATE_FILE, st)
            time.sleep(min(15.0, remaining))
        st["status"] = prev
        st.pop("retry_after", None)

    # ────────────────────────────── inputs ────────────────────────────────
    def _load_followed(self) -> dict:
        """dict[artist_uri] -> raw Spotify artist object. Only followed artists can
        be unfollowed, so they are the only ones worth indexing."""
        arr = storage.load_json(ARTISTS_CACHE_FILE, default=[]) or []
        return {a.get("uri"): a for a in arr if a.get("uri")}

    def _load_playlists(self):
        """Every OWNED playlist from the Recon snapshot except the Out Of Playlist
        ones. NOT filtered by Recon's include/exclude overrides on purpose — those
        govern the score, this index governs what you can SEE."""
        snap = storage.load_json(RECON_SNAPSHOT_FILE)
        if not snap:
            return None
        return [pl for pl in snap.get("playlists", [])
                if pl.get("type") not in EXCLUDED_PLAYLIST_TYPES]

    # ─────────────────────────── checkpointing ────────────────────────────
    @staticmethod
    def _ser(songs: dict) -> dict:
        return {k: {"id": s["id"], "name": s["name"], "artists": list(s["artists"]),
                    "liked": s["liked"], "pl": s["pl"]}
                for k, s in songs.items()}

    def _save_checkpoint(self, songs: dict, pulled: set, liked_done: bool, total: int,
                         liked_seen: int = 0):
        """Persist the accumulator + which playlists are fully folded in. Written ONLY
        between playlists (never mid-pull), so `songs` and `pulled` stay consistent: a
        crash re-pulls just the not-yet-checkpointed playlists, and re-folding one
        playlist is idempotent because it is folded atomically."""
        storage.save_json(CHECKPOINT_FILE, {
            "songs": self._ser(songs), "pulled": list(pulled),
            "liked_done": liked_done, "liked_seen": liked_seen,
            "total": total, "heartbeat": time.time(),
        })

    def _load_checkpoint(self):
        cp = storage.load_json(CHECKPOINT_FILE)
        if not cp:
            return {}, set(), False, 0
        songs = {k: {"id": s.get("id"), "name": s.get("name"),
                     "artists": set(s.get("artists") or []),
                     "liked": bool(s.get("liked")), "pl": s.get("pl") or []}
                 for k, s in (cp.get("songs") or {}).items()}
        # liked_seen travels with the checkpoint so a resumed run — which SKIPS the
        # liked pass — still reports how many saved tracks were scanned, instead of 0.
        return (songs, set(cp.get("pulled") or []), bool(cp.get("liked_done")),
                cp.get("liked_seen") or 0)

    # ─────────────────────────────── fold ─────────────────────────────────
    @staticmethod
    def _fold(songs: dict, followed: dict, tr: dict, playlist_uri: Optional[str],
              liked: bool) -> None:
        """Merge one track into the accumulator, keyed by ISRC (falling back to the
        track uri so an ISRC-less track still counts once instead of vanishing).
        Songs crediting NO followed artist are dropped here — that filter is what
        keeps the index small."""
        if not tr or tr.get("type") == "episode":
            return
        mine = [a.get("uri") for a in (tr.get("artists") or [])
                if a.get("uri") in followed]
        if not mine:
            return
        key = (tr.get("external_ids") or {}).get("isrc") or tr.get("uri")
        if not key:
            return
        s = songs.get(key)
        if s is None:
            s = songs[key] = {"id": tr.get("id"), "name": tr.get("name") or "",
                              "artists": set(), "liked": False, "pl": []}
        s["artists"].update(mine)
        if liked:
            s["liked"] = True
        if playlist_uri and playlist_uri not in s["pl"]:
            s["pl"].append(playlist_uri)
        if not s.get("id") and tr.get("id"):
            s["id"] = tr.get("id")
        if not s.get("name") and tr.get("name"):
            s["name"] = tr.get("name")

    # ─────────────────────────────── pull ─────────────────────────────────
    def _pull_liked(self, sp, songs: dict, followed: dict, st: dict) -> bool:
        """Page the whole of Liked Songs. Returns False if a stop interrupted it.
        Unlike a playlist this is not folded atomically — but it does not need to be:
        an interrupted liked pass simply re-runs from the top on resume, and
        re-folding is idempotent because `liked` is a flag, not a counter."""
        offset, seen = 0, 0
        st["phase"] = "liked"
        st["status"] = "liked"
        while True:
            if self._stop_requested():
                return False
            try:
                page = sp.current_user_saved_tracks(limit=LIKED_PAGE_LIMIT, offset=offset)
            except Exception as e:
                ra = _retry_after_seconds(e)
                if ra is None:
                    raise
                self._sleep_alive(ra, st)
                continue
            items = page.get("items") or []
            for it in items:
                self._fold(songs, followed, (it or {}).get("track") or {}, None, True)
            seen += len(items)
            offset += LIKED_PAGE_LIMIT
            st.update({"liked_seen": seen, "songs": len(songs),
                       "current": f"Liked Songs — {seen} scanned"})
            self._save_state(st)
            time.sleep(REQUEST_PACE_SEC)
            if len(items) < LIKED_PAGE_LIMIT or offset >= MAX_LIKED:
                break
        self._log(st, f"liked songs: {seen} scanned, {len(songs)} kept (followed artists only)")
        return True

    def _pull_playlist(self, sp, pl: dict, songs: dict, followed: dict, st: dict) -> bool:
        """Fully page one playlist and fold its tracks. ATOMIC: rows are collected
        locally and merged only after the whole playlist is read, so a stop/crash
        mid-pagination leaves `songs` untouched and the playlist is simply re-pulled.
        A deleted/private playlist (403/404) is skipped, not fatal."""
        pid = pl.get("playlist_id") or _uri_to_id(pl.get("playlist_uri"))
        name = pl.get("name", pid)
        rows, offset = [], 0
        while True:
            if self._stop_requested():
                return False
            try:
                page = sp.playlist_items(pid, limit=PAGE_LIMIT, offset=offset,
                                         additional_types=("track",), fields=_ITEM_FIELDS)
            except Exception as e:
                ra = _retry_after_seconds(e)
                if ra is None:
                    status = getattr(e, "http_status", None)
                    if status in (403, 404):
                        self._log(st, f"skip '{name}' ({status} — deleted/private)")
                        return True
                    raise
                self._sleep_alive(ra, st)
                st["status"] = "pulling"
                continue
            items = page.get("items") or []
            for it in items:
                rows.append((it or {}).get("track") or {})
            time.sleep(REQUEST_PACE_SEC)
            # Terminate on a short page, NOT on `next`: a `fields=` filter can omit the
            # top-level `next` key, and trusting it would silently truncate any playlist
            # past 100 tracks.
            if len(items) < PAGE_LIMIT:
                break
            offset += PAGE_LIMIT

        uri = pl.get("playlist_uri")
        for tr in rows:
            self._fold(songs, followed, tr, uri, False)
        return True

    # ────────────────────────────── emit ──────────────────────────────────
    def _build(self, songs: dict, playlists: list) -> tuple:
        """Invert the per-song accumulator into the two files the UI reads: a small
        counts map (the list column) and the full per-artist detail (the panel).
        Splitting them keeps the hot path — a 3,800-row list — on a ~300KB file
        instead of the multi-MB detail one."""
        pl_meta = {pl.get("playlist_uri"): {"n": pl.get("name"), "t": pl.get("type"),
                                            "w": pl.get("week_number"),
                                            "u": pl.get("spotify_url")}
                   for pl in playlists}
        artists: dict = {}
        for s in songs.values():
            rec = {"i": s["id"], "n": s["name"], "p": s["pl"]}
            if s["liked"]:
                rec["l"] = 1
            for uri in s["artists"]:
                artists.setdefault(uri, []).append(rec)

        detail, counts = {}, {}
        for uri, rows in artists.items():
            # liked first, then the most-placed, then alphabetical — the order the
            # "should I unfollow this?" question is actually read in.
            rows.sort(key=lambda r: (-r.get("l", 0), -len(r["p"]), (r["n"] or "").lower()))
            n_liked = sum(1 for r in rows if r.get("l"))
            distinct_pl = {p for r in rows for p in r["p"]}
            detail[uri] = {"liked": n_liked, "playlists": len(distinct_pl),
                           "songs": len(rows), "rows": rows}
            counts[uri] = {"liked": n_liked, "playlists": len(distinct_pl),
                           "songs": len(rows)}
        return pl_meta, detail, counts

    # ──────────────────────────── orchestration ───────────────────────────
    def run_index(self, sp, resume: bool = False):
        """The whole pipeline: load -> liked pass -> playlist pass (resumable) ->
        invert -> emit. Writes ONLY library_* files; never re-scores anything."""
        self._clear_stop()
        st = {"is_running": True, "status": "loading", "phase": "loading",
              "pulled": 0, "total": 0, "songs": 0, "liked_seen": 0,
              "current": "", "logs": [], "started": _ts()}
        self._save_state(st)

        try:
            playlists = self._load_playlists()
            if playlists is None:
                raise RuntimeError("No Recon snapshot — run Playlist Recon (Stage 1) first.")
            followed = self._load_followed()
            if not followed:
                raise RuntimeError("No followed-artists cache — run a scan / 'Update artist list' first.")

            if resume:
                songs, pulled, liked_done, liked_seen = self._load_checkpoint()
                st["liked_seen"] = liked_seen
            else:
                songs, pulled, liked_done = {}, set(), False
                storage.delete_file(CHECKPOINT_FILE)

            total = len(playlists)
            st.update({"total": total, "pulled": len(pulled), "followed": len(followed),
                       "songs": len(songs)})
            self._log(st, f"indexing Liked Songs + {total} playlists (all types except "
                          f"Out Of Playlist) for {len(followed)} followed artists")
            self._save_state(st)

            # ── phase 1: Liked Songs ──
            if not liked_done:
                if not self._pull_liked(sp, songs, followed, st):
                    self._save_checkpoint(songs, pulled, False, total, st.get('liked_seen', 0))
                    st.update({"is_running": False, "status": "stopped", "songs": len(songs)})
                    self._log(st, "stopped by user during Liked Songs — resumable")
                    self._save_state(st)
                    return
                liked_done = True
                self._save_checkpoint(songs, pulled, True, total, st.get('liked_seen', 0))

            # ── phase 2: playlists ──
            st.update({"status": "pulling", "phase": "pulling"})
            self._save_state(st)
            for pl in playlists:
                uri = pl.get("playlist_uri")
                if uri in pulled:
                    continue
                if self._stop_requested() or not self._pull_playlist(sp, pl, songs, followed, st):
                    self._save_checkpoint(songs, pulled, liked_done, total, st.get('liked_seen', 0))
                    st.update({"is_running": False, "status": "stopped", "songs": len(songs)})
                    self._log(st, f"stopped by user at {len(pulled)}/{total} playlists — resumable")
                    self._save_state(st)
                    return
                pulled.add(uri)
                st.update({"pulled": len(pulled), "songs": len(songs), "status": "pulling",
                           "current": pl.get("name", "")})
                st.pop("retry_after", None)
                if len(pulled) % CHECKPOINT_EVERY == 0:
                    self._save_checkpoint(songs, pulled, liked_done, total, st.get('liked_seen', 0))
                self._save_state(st)

            # ── phase 3: invert + emit ──
            st.update({"status": "building", "phase": "building", "current": "",
                       "songs": len(songs)})
            self._log(st, f"building index from {len(songs)} songs")
            self._save_state(st)

            pl_meta, detail, counts = self._build(songs, playlists)
            gen = _ts()
            stats = {"songs": len(songs), "artists": len(counts),
                     "liked_seen": st.get("liked_seen", 0),
                     "liked_songs": sum(1 for s in songs.values() if s["liked"]),
                     "playlists_scanned": len(pulled), "followed": len(followed)}
            # Detail first: if the big write fails, the counts file — which is what
            # flips the UI's "index ready" flag — is NOT advanced to a generation with
            # no detail behind it.
            if not storage.save_json(INDEX_FILE, {"generated": gen, "stats": stats,
                                                  "playlists": pl_meta, "artists": detail}):
                raise RuntimeError("could not write the library index to storage")
            if not storage.save_json(COUNTS_FILE, {"generated": gen, "stats": stats,
                                                   "counts": counts}):
                raise RuntimeError("could not write the library counts to storage")

            storage.delete_file(CHECKPOINT_FILE)
            st.update({"is_running": False, "status": "done", "phase": "done",
                       "stats": stats, "generated": gen, "finished": _ts()})
            self._log(st, f"done — {stats['liked_songs']} liked songs and "
                          f"{stats['songs']} songs across {stats['artists']} followed artists")
            self._save_state(st)

        except Exception as e:
            msg = str(e)
            # A cached token that can't be validated against the scope makes spotipy
            # fall back to an interactive prompt (input()) → EOFError when headless.
            if isinstance(e, EOFError) or "EOF when reading a line" in msg:
                msg = ("Spotify authorization is out of date for the background token. "
                       "Log out and log back in on the site, then run it again.")
            st.update({"is_running": False, "status": "error", "error": msg})
            self._log(st, f"ERROR: {e}")
            self._save_state(st)
            raise

    # ───────────────────────────── read side ──────────────────────────────
    @staticmethod
    def _stamp(filename: str) -> Optional[str]:
        meta = storage.get_metadata(filename)
        if not meta:
            return None
        return meta.get("last_updated") or f"size:{meta.get('size')}"

    def _load_counts(self) -> Optional[dict]:
        """The small file, mtime-cached — read on every Cleanup/Health list render."""
        stamp = self._stamp(COUNTS_FILE)
        if stamp is None:
            return None
        if self._counts is not None and stamp == self._counts_stamp:
            return self._counts
        with self._lock:
            payload = storage.load_json(COUNTS_FILE)
            if not payload:
                return None
            self._counts = payload
            self._counts_stamp = stamp
        return self._counts

    def _load_index(self) -> Optional[dict]:
        """The big file, mtime-cached — downloaded lazily on the FIRST panel open in
        a worker, never on the list path."""
        stamp = self._stamp(INDEX_FILE)
        if stamp is None:
            return None
        if self._index is not None and stamp == self._index_stamp:
            return self._index
        with self._lock:
            payload = storage.load_json(INDEX_FILE)
            if not payload:
                return None
            self._index = payload
            self._index_stamp = stamp
        return self._index

    def counts(self) -> dict:
        """{artist_uri: {liked, playlists, songs}} for the whole followed set, plus
        the generation stamp so the UI can say how old it is."""
        payload = self._load_counts()
        if not payload:
            return {"ready": False, "counts": {}}
        return {"ready": True, "generated": payload.get("generated"),
                "stats": payload.get("stats") or {}, "counts": payload.get("counts") or {}}

    def artist(self, uri: str) -> dict:
        """The per-artist panel: every song of theirs in the library, each with
        whether it is LIKED and which playlists it sits in. Playlist uris are
        expanded to names/links here so the client stays dumb."""
        payload = self._load_index()
        uri = normalize_uri(uri)
        if not payload:
            return {"ready": False, "artist_uri": uri, "liked": 0, "playlists": 0,
                    "songs": 0, "rows": []}
        rec = (payload.get("artists") or {}).get(uri)
        pl_meta = payload.get("playlists") or {}
        if not rec:
            return {"ready": True, "generated": payload.get("generated"),
                    "artist_uri": uri, "liked": 0, "playlists": 0, "songs": 0, "rows": []}
        rows = []
        for r in rec.get("rows") or []:
            tid = r.get("i")
            rows.append({
                "track_id": tid,
                "name": r.get("n"),
                "liked": bool(r.get("l")),
                "spotify_url": f"https://open.spotify.com/track/{tid}" if tid else None,
                "playlists": [{"playlist_uri": p,
                               "name": (pl_meta.get(p) or {}).get("n"),
                               "type": (pl_meta.get(p) or {}).get("t"),
                               "week_number": (pl_meta.get(p) or {}).get("w"),
                               "spotify_url": (pl_meta.get(p) or {}).get("u")}
                              for p in (r.get("p") or [])],
            })
        return {"ready": True, "generated": payload.get("generated"), "artist_uri": uri,
                "liked": rec.get("liked", 0), "playlists": rec.get("playlists", 0),
                "songs": rec.get("songs", 0), "rows": rows}


# Global instance (mirrors bootstrap / scanner / health).
library = LibraryEngine()
