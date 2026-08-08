# -*- coding: utf-8 -*-
"""Artist Health Engine — Stage 2: Bootstrap (read-only compute).

Turns the Recon playlist map into real per-artist scores. For every playlist the
user marked *included* in Recon it pulls the tracks — ISRC + album_type + ALL the
artist-IDs (including featured artists) arrive in that single paginated read, no
enrichment calls — then folds every song into a decay-weighted RANK per artist,
entirely in Python memory.

RANK = 0  (a followed artist NONE of whose songs ever entered an included
playlist) => a cleanup candidate.  RANK > 0 => keep. Decay never reaches zero, so
RANK == 0  ⟺  the artist never appeared in ANY included playlist (BOOTSTRAP.md §7).

The output is cache/cleanup_candidates.json in the EXACT shape the existing
/cleanup page already reads (Bootstrap feeds that UI, it never rebuilds it) plus a
cache/bootstrap_scores.json with the full ranked list for spot-checking / Stage 3.
Bootstrap NEVER unfollows anyone — /cleanup stays the only place removal happens.

Runs as a Cloud Run Job (backend.bootstrap_job) so hundreds of track reads finish
regardless of the browser; a light per-playlist checkpoint lets a recycled
container resume without re-pulling. v1 keeps everything in memory — NO SQLite
(that arrives in Stage 4 with release_events). See artist-health/BOOTSTRAP.md.
"""
import time
import datetime
from typing import Optional

from .storage_manager import storage

CACHE_DIR = "cache"
STATE_FILE = f"{CACHE_DIR}/bootstrap_state.json"
CHECKPOINT_FILE = f"{CACHE_DIR}/bootstrap_checkpoint.json"
STOP_REQUEST_FILE = f"{CACHE_DIR}/bootstrap_stop_request.json"
CANDIDATES_FILE = f"{CACHE_DIR}/cleanup_candidates.json"   # consumed by /cleanup
SCORES_FILE = f"{CACHE_DIR}/bootstrap_scores.json"         # full ranked list (observability)
SONGS_FILE = f"{CACHE_DIR}/bootstrap_songs.json"           # raw per-ISRC fold — feeds Stage 3 live tuning

# Recon (Stage 1) outputs that Bootstrap consumes — mirror routers/recon.py.
RECON_SNAPSHOT_FILE = "cache/recon_playlists.json"
RECON_OVERRIDES_FILE = "cache/recon_inclusions.json"
ARTISTS_CACHE_FILE = f"{CACHE_DIR}/artists_cache.json"
AUTO_INCLUDED_TYPES = ("weekly", "outofplaylist")

# ── RANK formula — locked defaults (BOOTSTRAP.md §7 / §11). ⚙️ = tune later in preview. ──
HALF_LIFE_WEEKS = 26            # ⚙️ ~6 months: weight halves every 26 weeks
LEGACY_MULT = 0.5              # ⚙️ season-1 legacy playlists count half
# Base weight per playlist type, applied BEFORE decay. Every INCLUDED type must be
# > 0: an artist who appears only in a hand-picked 'other' playlist has still
# "entered", so flagging them a candidate would violate §1 ("never entered ANY
# included playlist"). 'other' therefore mirrors the shadow-layer weight.
TYPE_WEIGHT = {"weekly": 1.0, "outofplaylist": 0.05, "other": 0.05}  # ⚙️
DEFAULT_TYPE_WEIGHT = 0.05     # any unforeseen-but-included type still credits

HEARTBEAT_ALIVE_SEC = 120      # a run whose heartbeat is younger than this is "alive"
CHECKPOINT_EVERY = 20          # persist the accumulator every N fully-pulled playlists
PAGE_LIMIT = 100               # Spotify max page size for playlist items
# Proactive pacing between page reads. The Job has no waiting client, so a fast
# container could otherwise burst read calls and trip Spotify's SILENT long-window
# quota — the failure mode from the 2026-07-17 hard-block (a sustained rate raises a
# multi-hour block that does NOT arrive as a clean 429). ~0.25s keeps us near ~2 rps,
# well under the ~5 rps danger line, at a cost of ~a couple minutes over the whole run.
REQUEST_PACE_SEC = 0.25

# Only ask Spotify for the fields we actually fold — keeps each page small/cheap.
_ITEM_FIELDS = ("next,items(track(id,uri,type,external_ids(isrc),"
                "album(album_type),artists(id,uri)))")


def _ts() -> str:
    return datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")


def _retry_after_seconds(e) -> Optional[int]:
    """If e is a Spotify 429, its Retry-After in seconds; else None. Works because
    the clients Bootstrap uses exclude 429 from status_forcelist, so the header
    survives (spotipy would otherwise swallow it). Mirrors recon/cleanup."""
    if getattr(e, "http_status", None) != 429:
        return None
    hdrs = getattr(e, "headers", None) or {}
    try:
        return max(1, int(hdrs.get("Retry-After", 1)))
    except Exception:
        return 1


def _uri_to_id(uri: str) -> str:
    return uri.rsplit(":", 1)[-1] if uri else uri


def score_songs(songs: dict, current_week: int,
                half_life: float = HALF_LIFE_WEEKS,
                type_weight: Optional[dict] = None,
                legacy_mult: float = LEGACY_MULT,
                default_type_weight: float = DEFAULT_TYPE_WEIGHT) -> dict:
    """THE single RANK scorer (BOOTSTRAP.md §7) — shared by Bootstrap (locked
    defaults) and the Stage-3 Health engine (live slider params; STAGE3.md §2/§4א.2).
    Pure: no I/O, no instance state, so both callers score identically.

    For each song take its STRONGEST placement (max contribution, NOT a sum of
    duplicate placements — and WHICH placement wins can flip with the weights, e.g. a
    fresh outof vs a decayed legacy weekly, which is exactly why Stage 3 must keep
    every placement, not the once-chosen best), credit EVERY artist on the track
    (features included), and sum those strongest contributions per artist.

    Returns scores[artist_uri] = {"rank", "entered"}; `entered` = the number of
    distinct ISRCs that credited the artist, and entered == 0 ⟺ rank 0 ⟺ the artist
    never appeared under these weights (a Stage-1 cull candidate)."""
    tw = TYPE_WEIGHT if type_weight is None else type_weight
    scores: dict = {}
    for s in songs.values():
        best = 0.0
        for p in s["placements"]:
            w = tw.get(p.get("type"), default_type_weight)
            if w <= 0:
                continue
            mult = legacy_mult if p.get("legacy") else 1.0
            age = current_week - (p.get("week_number") or current_week)
            if age < 0:
                age = 0                          # future-dated week → treat as now
            contrib = w * mult * (0.5 ** (age / half_life))
            if contrib > best:
                best = contrib
        if best <= 0:
            continue
        for uri in s["artist_uris"]:
            sc = scores.get(uri)
            if sc is None:
                scores[uri] = {"rank": best, "entered": 1}
            else:
                sc["rank"] += best
                sc["entered"] += 1
    return scores


class BootstrapEngine:
    """Single-user, single-writer — same model as the scanner. No in-memory state
    survives between runs (the Job is a fresh process); everything authoritative
    lives in GCS (STATE_FILE / CHECKPOINT_FILE)."""

    # ────────────────────────── state / heartbeat ──────────────────────────
    def _save_state(self, st: dict):
        st["heartbeat"] = time.time()
        storage.save_json(STATE_FILE, st)

    def _log(self, st: dict, msg: str):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        st.setdefault("logs", []).append(f"[{ts}] {msg}")
        if len(st["logs"]) > 40:
            st["logs"].pop(0)
        print(f"[bootstrap] {msg}", flush=True)

    def get_status(self) -> dict:
        """The frontend's single source of truth. Downgrades a run whose heartbeat
        went stale (>120s) to not-running so a crashed Job can't wedge the UI or
        block a fresh start — same staleness rule the scanner uses."""
        st = storage.load_json(STATE_FILE) or {"is_running": False, "status": "idle"}
        if st.get("is_running") and st.get("status") != "starting":
            hb = st.get("heartbeat", 0)
            if not hb or (time.time() - hb) > HEARTBEAT_ALIVE_SEC:
                st["is_running"] = False
                if st.get("status") not in ("done", "error", "stopped"):
                    st["status"] = "interrupted"
        st["candidates_ready"] = storage.exists(CANDIDATES_FILE)
        cp = storage.load_json(CHECKPOINT_FILE)
        st["resumable"] = bool(cp) and not st.get("is_running") and st.get("status") != "done"
        if cp:
            st["checkpoint"] = {"pulled": len(cp.get("pulled", [])),
                                "total": cp.get("total", 0)}
        return st

    # ─────────────────────────── stop handling ────────────────────────────
    def _stop_requested(self) -> bool:
        """Read from GCS so a STOP issued by the Service reaches the Job process."""
        return storage.exists(STOP_REQUEST_FILE)

    def _clear_stop(self):
        storage.delete_file(STOP_REQUEST_FILE)

    def request_stop(self):
        storage.save_json(STOP_REQUEST_FILE, {"at": time.time()})

    def _sleep_alive(self, seconds: float, st: dict):
        """Sleep a (possibly long) rate-limit wait WITHOUT letting the heartbeat go
        stale and WITHOUT ignoring a stop. Chunks the sleep into ≤15s slices, each
        refreshing the heartbeat — so a Retry-After far past the 120s liveness
        window can't make the liveness checks declare this run dead."""
        end = time.time() + seconds
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

    # ────────────────────────────── inputs ────────────────────────────────
    def _load_included(self):
        """Return (included_playlists, current_week) from the Recon snapshot +
        overrides, or (None, 0) if Recon hasn't run. Inclusion mirrors recon._is_
        included exactly: weekly+outofplaylist auto-in, an explicit override wins.
        current_week (the decay anchor) = the highest week number in the snapshot."""
        snap = storage.load_json(RECON_SNAPSHOT_FILE)
        if not snap:
            return None, 0
        overrides = storage.load_json(RECON_OVERRIDES_FILE, default={}) or {}
        included, current_week = [], 0
        for pl in snap.get("playlists", []):
            wk = pl.get("week_number")
            if wk:
                current_week = max(current_week, wk)
            uri = pl.get("playlist_uri")
            inc = overrides[uri] if uri in overrides else (pl.get("type") in AUTO_INCLUDED_TYPES)
            if inc:
                included.append(pl)
        return included, current_week

    def _load_followed(self) -> dict:
        """dict[artist_uri] -> raw Spotify artist object (from the scan's cache).
        Only followed artists are cleanup candidates, so this is the universe."""
        arr = storage.load_json(ARTISTS_CACHE_FILE, default=[]) or []
        return {a.get("uri"): a for a in arr if a.get("uri")}

    # ─────────────────────────── checkpointing ────────────────────────────
    def _save_checkpoint(self, songs: dict, pulled: set, current_week: int, total: int):
        """Persist the accumulator + which playlists are fully folded in. Written
        ONLY between playlists (never mid-pull), so songs and `pulled` are always
        consistent: a crash re-pulls just the not-yet-checkpointed playlists, and
        re-folding is idempotent because a playlist is folded atomically (§8/§10)."""
        ser = {isrc: {"album_type": s["album_type"],
                      "artist_uris": list(s["artist_uris"]),
                      "placements": s["placements"]}
               for isrc, s in songs.items()}
        storage.save_json(CHECKPOINT_FILE, {
            "songs": ser, "pulled": list(pulled),
            "current_week": current_week, "total": total, "heartbeat": time.time(),
        })

    def _load_checkpoint(self):
        cp = storage.load_json(CHECKPOINT_FILE)
        if not cp:
            return {}, set(), 0
        songs = {isrc: {"album_type": s.get("album_type"),
                        "artist_uris": set(s.get("artist_uris") or []),
                        "placements": s.get("placements") or []}
                 for isrc, s in (cp.get("songs") or {}).items()}
        return songs, set(cp.get("pulled") or []), cp.get("current_week", 0)

    # ─────────────────────────────── pull ─────────────────────────────────
    def _pull_playlist(self, sp, pl: dict, songs: dict, st: dict) -> bool:
        """Fully page one playlist and fold its tracks into `songs`. ATOMIC: rows
        are collected locally and merged only after the whole playlist is read, so a
        stop/crash mid-pagination leaves `songs` untouched (the playlist is simply
        re-pulled on resume). Returns True if fully pulled (or safely skipped),
        False if a stop interrupted it. A deleted/private playlist (404/403) is
        skipped, not fatal (§8)."""
        pid = pl.get("playlist_id") or _uri_to_id(pl.get("playlist_uri"))
        name = pl.get("name", pid)
        rows = []                                    # (isrc, album_type, [artist_uris])
        offset = 0
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
                        return True                  # nothing to fold; treat as done
                    raise
                self._sleep_alive(ra, st)
                st["status"] = "pulling"
                st.pop("retry_after", None)
                continue
            items = page.get("items") or []
            for it in items:
                tr = (it or {}).get("track") or {}
                if not tr or tr.get("type") == "episode":
                    continue                         # podcast episode / empty slot
                # ISRC is the natural dedup key (§6); fall back to the track URI so a
                # rare ISRC-less track still counts once instead of vanishing.
                isrc = (tr.get("external_ids") or {}).get("isrc") or tr.get("uri")
                if not isrc:
                    continue
                album_type = (tr.get("album") or {}).get("album_type")
                artist_uris = [a.get("uri") for a in (tr.get("artists") or []) if a.get("uri")]
                rows.append((isrc, album_type, artist_uris))
            time.sleep(REQUEST_PACE_SEC)             # pace EVERY read → stay under the silent quota
            # Terminate on a short page, NOT on `next`: a `fields=` filter can omit the
            # top-level `next` key, and trusting it would silently truncate any playlist
            # past 100 tracks. A full page (== PAGE_LIMIT items, incl. unavailable ones)
            # means there may be more; a short page is always the last.
            if len(items) < PAGE_LIMIT:
                break
            offset += PAGE_LIMIT

        # merge — one placement record per (song, playlist) appearance
        placement = {"week_number": pl.get("week_number") or 0,
                     "type": pl.get("type"), "legacy": bool(pl.get("legacy")),
                     "playlist_uri": pl.get("playlist_uri")}   # Stage 3 "why" panel links the exact playlist
        for isrc, album_type, artist_uris in rows:
            s = songs.get(isrc)
            if s is None:
                songs[isrc] = {"album_type": album_type,
                               "artist_uris": set(artist_uris),
                               "placements": [placement]}
            else:
                s["artist_uris"].update(artist_uris)
                s["placements"].append(placement)
                if not s.get("album_type"):
                    s["album_type"] = album_type
        return True

    # ─────────────────────────────── score ────────────────────────────────
    def _score(self, songs: dict, current_week: int) -> dict:
        """Bootstrap's locked-default scoring — a thin wrapper over the shared pure
        `score_songs` (Stage 3 calls that same function with live slider params).
        Kept so existing call sites and behaviour read exactly as before."""
        return score_songs(songs, current_week)

    def _candidate_row(self, uri: str, art: dict) -> dict:
        """Build ONE cleanup candidate in the EXACT shape Cleanup.tsx's `Candidate`
        interface demands — the contract that keeps the UI from crashing (§9):
        genres=STRING, followers=FLAT int, artist_id=bare id, releases placeholder,
        tier=neutral single value."""
        aid = art.get("id") or _uri_to_id(uri)
        imgs = art.get("images") or []
        genres = art.get("genres") or []
        return {
            "artist_uri": uri,
            "artist_id": aid,                                    # /cleanup filters live on this
            "artist": art.get("name"),
            "image": imgs[0]["url"] if imgs else "",
            "genres": ", ".join(genres),                         # STRING (UI does .toLowerCase())
            "followers": (art.get("followers") or {}).get("total", 0),  # FLAT int
            "releases": 0,                                       # not known cheaply in v1 (§9)
            "entered": 0,                                        # candidates never entered → 0
            "tier": "—",                                         # flat list, no tiers (§11.5)
            "spotify_url": (art.get("external_urls") or {}).get("spotify")
                           or f"https://open.spotify.com/artist/{aid}",
        }

    def _emit(self, scores: dict, followed: dict, current_week: int) -> dict:
        """Write cleanup_candidates.json (RANK==0 followers) + bootstrap_scores.json
        (full ranked list). Returns a small summary for the status card."""
        candidates, ranked = [], []
        for uri, art in followed.items():
            sc = scores.get(uri)
            rank = sc["rank"] if sc else 0.0
            entered = sc["entered"] if sc else 0
            ranked.append({"artist_uri": uri, "artist": art.get("name"),
                           "rank": round(rank, 6), "entered": entered})
            if entered == 0:                          # rank == 0 ⟺ never appeared
                candidates.append(self._candidate_row(uri, art))
        ranked.sort(key=lambda r: r["rank"], reverse=True)

        gen = _ts()
        with_rank = sum(1 for r in ranked if r["rank"] > 0)
        storage.save_json(CANDIDATES_FILE, {
            "generated": gen,
            "threshold": 0,
            "window": f"RANK 0 — never entered an included playlist · half-life {HALF_LIFE_WEEKS}w",
            "count": len(candidates),
            "candidates": candidates,
        })
        storage.save_json(SCORES_FILE, {
            "generated": gen, "current_week": current_week,
            "followed": len(followed), "with_rank": with_rank,
            "candidates": len(candidates), "artists": ranked,
        })
        return {"followed": len(followed), "with_rank": with_rank,
                "candidates": len(candidates)}

    def _save_songs(self, songs: dict, current_week: int):
        """Stage 3A: persist the raw fold so the Health dashboard can recompute RANK
        for ANY slider values without re-pulling Spotify (STAGE3.md §3/§4א.1). Stored
        per-ISRC only — the per-artist index is rebuilt in memory on load (§10.3),
        never a second file that could drift out of sync. Sets → lists for JSON,
        exactly like the checkpoint. current_week travels WITH the songs so the Health
        engine has one file + one mtime to invalidate its in-memory cache on (§12.1)."""
        ser = {isrc: {"album_type": s["album_type"],
                      "artist_uris": list(s["artist_uris"]),
                      "placements": s["placements"]}
               for isrc, s in songs.items()}
        storage.save_json(SONGS_FILE, {
            "generated": _ts(), "current_week": current_week,
            "count": len(ser), "songs": ser,
        })

    # ──────────────────────────── orchestration ───────────────────────────
    def run_bootstrap(self, sp, resume: bool = False):
        """The whole pipeline (§8): load → pull (resumable) → score → emit. Runs
        synchronously in the Job process (or a local BackgroundTask in dev). Safe to
        call twice: the checkpoint makes a resume continue, not restart."""
        self._clear_stop()
        st = {"is_running": True, "status": "loading", "phase": "loading",
              "pulled": 0, "total": 0, "songs": 0, "current": "",
              "logs": [], "started": _ts()}
        self._save_state(st)

        try:
            included, current_week = self._load_included()
            if included is None:
                raise RuntimeError("No Recon snapshot — run Playlist Recon (Stage 1) first.")
            followed = self._load_followed()
            if not followed:
                raise RuntimeError("No followed-artists cache — run a scan / 'Update artist list' first.")

            if resume:
                songs, pulled, cp_week = self._load_checkpoint()
                if cp_week:
                    current_week = cp_week            # keep the anchor stable across the run
            else:
                songs, pulled = {}, set()
                storage.delete_file(CHECKPOINT_FILE)

            total = len(included)
            st.update({"total": total, "pulled": len(pulled), "current_week": current_week,
                       "followed": len(followed), "status": "pulling", "phase": "pulling"})
            self._log(st, f"pulling {total - len(pulled)} of {total} included playlists "
                          f"(anchor week {current_week}, {len(followed)} followed)")
            self._save_state(st)

            # ── pull phase ──
            for pl in included:
                uri = pl.get("playlist_uri")
                if uri in pulled:
                    continue
                if self._stop_requested():
                    self._save_checkpoint(songs, pulled, current_week, total)
                    st.update({"is_running": False, "status": "stopped", "songs": len(songs)})
                    self._log(st, f"stopped by user at {len(pulled)}/{total} — resumable")
                    self._save_state(st)
                    return
                st["current"] = pl.get("name", "")
                st["songs"] = len(songs)
                self._save_state(st)

                if not self._pull_playlist(sp, pl, songs, st):   # False = stop mid-pull
                    self._save_checkpoint(songs, pulled, current_week, total)
                    st.update({"is_running": False, "status": "stopped", "songs": len(songs)})
                    self._log(st, f"stopped by user at {len(pulled)}/{total} — resumable")
                    self._save_state(st)
                    return

                pulled.add(uri)
                st.update({"pulled": len(pulled), "songs": len(songs), "status": "pulling"})
                st.pop("retry_after", None)
                if len(pulled) % CHECKPOINT_EVERY == 0:
                    self._save_checkpoint(songs, pulled, current_week, total)
                self._save_state(st)

            self._save_checkpoint(songs, pulled, current_week, total)  # end-of-pull safety net

            # ── score + emit phase ──
            st.update({"status": "scoring", "phase": "scoring", "current": "",
                       "songs": len(songs)})
            self._log(st, f"scoring {len(songs)} unique songs across {len(followed)} followed artists")
            self._save_state(st)

            scores = self._score(songs, current_week)
            summary = self._emit(scores, followed, current_week)
            self._save_songs(songs, current_week)     # Stage 3A: raw material for live tuning

            storage.delete_file(CHECKPOINT_FILE)      # success → nothing to resume
            st.update({"is_running": False, "status": "done", "phase": "done",
                       "songs": len(songs), "summary": summary, "finished": _ts()})
            self._log(st, f"done — {summary['candidates']} cleanup candidates "
                          f"({summary['with_rank']}/{summary['followed']} kept)")
            self._save_state(st)

        except Exception as e:
            msg = str(e)
            # When the cached token can't be validated against the requested scope,
            # spotipy falls back to an interactive login prompt (input()) — headless
            # that raises EOFError ("EOF when reading a line"). Translate it into an
            # actionable message instead of the cryptic default.
            if isinstance(e, EOFError) or "EOF when reading a line" in msg:
                msg = ("Spotify authorization is missing the playlist-read permission "
                       "(the background token is out of date). Log out and log back in "
                       "on the site to re-authorize, then run Bootstrap again.")
            st.update({"is_running": False, "status": "error", "error": msg})
            self._log(st, f"ERROR: {e}")
            self._save_state(st)
            raise


# Global instance (mirrors scanner / automation_manager).
bootstrap = BootstrapEngine()
