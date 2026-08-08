# -*- coding: utf-8 -*-
"""Artist Health Engine — Stage 3: the live-tuning engine (STAGE3.md §4א).

Stage 2 (Bootstrap) already computed a decay-weighted RANK per artist and threw the
raw material away. Stage 3 keeps that raw material (`cache/bootstrap_songs.json`) and
turns RANK into something you can *see* and *tune*: drag half-life / weights / band
boundaries and watch the candidate counts move — without ever re-pulling Spotify.

How it stays fast (STAGE3.md §10.3 / §12.1): the raw per-ISRC fold is loaded ONCE into
process memory and a per-artist index is built in the same pass; every preview/ranked/
artist call then just re-runs the pure `score_songs` over that in-memory dict (< 1s for
~8k songs). The only GCS touch on the hot path is a cheap metadata HEAD to notice when
Bootstrap (a separate Job) rewrote the file — then, and only then, we re-download.

Stage 3א is BACKEND ONLY and scores RAW (the manual-override layer is applied at the
single seam `_effective`, a no-op until Stage 3ג wires it in — STAGE3.md §10.1). The
dashboard is 3ב; the rich manual layer is 3ג.
"""
import threading
import datetime
from typing import Optional

from . import manual_store
from .storage_manager import storage
from .bootstrap import (score_songs, bootstrap, SONGS_FILE, ARTISTS_CACHE_FILE,
                        RECON_SNAPSHOT_FILE, HALF_LIFE_WEEKS, TYPE_WEIGHT, LEGACY_MULT)

CACHE_DIR = "cache"
WEIGHTS_FILE = f"{CACHE_DIR}/health_weights.json"
CANDIDATES_FILE = f"{CACHE_DIR}/cleanup_candidates.json"     # the /cleanup contract
BACKUP_PREFIX = f"{CACHE_DIR}/cleanup_candidates_backup_"

BAND_NAMES = ("red", "orange", "yellow", "green")
# 🔴 rank≤b1(=0, "never entered") · 🟠 ≤b2(0.05) · 🟡 ≤b3(0.5) · 🟢 above. Start point
# only — every value is a slider (STAGE3.md §8.1).
DEFAULT_BANDS = [0.0, 0.05, 0.5]
DEFAULT_WEIGHTS = {
    "half_life": HALF_LIFE_WEEKS,
    "weekly": TYPE_WEIGHT["weekly"],
    "outof": TYPE_WEIGHT["outofplaylist"],
    "other": TYPE_WEIGHT["other"],
    "legacy": LEGACY_MULT,
    "bands": DEFAULT_BANDS,
    "candidate_bands": ["red"],
}


def _ts() -> str:
    return datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")


def _uri_to_id(uri: str) -> str:
    return uri.rsplit(":", 1)[-1] if uri else uri


def normalize_uri(artist: str) -> str:
    """Canonical `spotify:artist:ID` from a bare id, a full uri, or an open.spotify.com
    link — so the 'why' endpoint answers a search by ID or a pasted URL, not only a uri
    (the artist-search request)."""
    if not artist:
        return artist
    a = artist.strip()
    if "open.spotify.com/artist/" in a:
        a = a.split("artist/")[-1].split("?")[0].split("/")[0]
    a = a.rsplit(":", 1)[-1]              # bare id, whether it came in as uri or already bare
    return f"spotify:artist:{a}"


def band_of(rank: float, bands) -> str:
    """Graduated band for a RANK given the 3 boundaries (STAGE3.md §7.1)."""
    b1, b2, b3 = bands
    if rank <= b1:
        return "red"
    if rank <= b2:
        return "orange"
    if rank <= b3:
        return "yellow"
    return "green"


class ColdStartError(RuntimeError):
    """bootstrap_songs.json doesn't exist yet — before the first Bootstrap run on the
    new code. The router maps this to a 409 with an actionable message (STAGE3.md §10.4)."""


class HealthEngine:
    """Service-side, read-only-w.r.t.-Spotify. Holds the raw fold in process memory and
    recomputes on demand. One instance per worker process; each invalidates its own
    cache independently by file mtime, so a Bootstrap rewrite is picked up everywhere."""

    def __init__(self):
        self._lock = threading.Lock()
        self._songs = None                 # {isrc: {album_type, artist_uris:[...], placements:[...]}}
        self._by_artist = {}               # {artist_uri: [isrc, ...]}  (built in memory, never a file)
        self._current_week = 0
        self._songs_generated = None
        self._songs_stamp = None
        self._followed = None              # {artist_uri: raw Spotify artist obj}
        self._followed_stamp = None
        self._pl_meta = None               # {playlist_uri: {name,type,week_number,spotify_url}}
        self._pl_stamp = None

    # ─────────────────────────── cache management ──────────────────────────
    @staticmethod
    def _stamp(filename: str) -> Optional[str]:
        """A cheap change-token for a GCS/local file (its update time, or size as a
        fallback). None ⟺ the file doesn't exist."""
        meta = storage.get_metadata(filename)
        if not meta:
            return None
        return meta.get("last_updated") or f"size:{meta.get('size')}"

    def _ensure_songs(self):
        """(Re)load bootstrap_songs.json + rebuild the per-artist index when it changed
        (§10.3/§12.1); raise ColdStartError if it doesn't exist yet (§10.4). Hot path =
        one metadata HEAD; the 3-5MB download happens only when the mtime moved."""
        stamp = self._stamp(SONGS_FILE)
        if stamp is None:
            raise ColdStartError("no song data")
        if self._songs is not None and stamp == self._songs_stamp:
            return
        with self._lock:
            if self._songs is not None and stamp == self._songs_stamp:
                return                      # another thread just loaded it
            payload = storage.load_json(SONGS_FILE)
            if not payload or not payload.get("songs"):
                raise ColdStartError("no song data")   # vanished/truncated between HEAD and GET
            songs, by_artist = {}, {}
            for isrc, s in payload["songs"].items():
                uris = s.get("artist_uris") or []
                songs[isrc] = {"album_type": s.get("album_type"),
                               "artist_uris": uris,
                               "placements": s.get("placements") or []}
                for u in uris:
                    by_artist.setdefault(u, []).append(isrc)
            self._songs = songs
            self._by_artist = by_artist
            self._current_week = payload.get("current_week") or 0
            self._songs_generated = payload.get("generated")
            self._songs_stamp = stamp

    def _followed_map(self) -> dict:
        """The candidate universe (only followed artists can be culled), from the scan's
        cache. mtime-cached so a slider drag never re-downloads it (§10.3)."""
        stamp = self._stamp(ARTISTS_CACHE_FILE)
        if self._followed is not None and stamp == self._followed_stamp:
            return self._followed
        arr = storage.load_json(ARTISTS_CACHE_FILE, default=[]) or []
        self._followed = {a.get("uri"): a for a in arr if a.get("uri")}
        self._followed_stamp = stamp
        return self._followed

    def _playlist_meta(self) -> dict:
        """playlist_uri → {name,type,week_number,spotify_url} from the Recon snapshot,
        to label + link the 'why' panel's placements (§8.7). mtime-cached."""
        stamp = self._stamp(RECON_SNAPSHOT_FILE)
        if self._pl_meta is not None and stamp == self._pl_stamp:
            return self._pl_meta
        snap = storage.load_json(RECON_SNAPSHOT_FILE) or {}
        self._pl_meta = {pl.get("playlist_uri"): {
            "name": pl.get("name"), "type": pl.get("type"),
            "week_number": pl.get("week_number"), "spotify_url": pl.get("spotify_url"),
        } for pl in snap.get("playlists", [])}
        self._pl_stamp = stamp
        return self._pl_meta

    # ─────────────────────────── manual-layer seam ─────────────────────────
    def _effective(self, uri: str, raw: float) -> float:
        """The RANK used for banding/counting/placing. Stage 3א = raw (STAGE3.md §10.1:
        '3א gives the raw _score; extended when 3ג is built'). Stage 3ג wires the manual
        overrides in HERE — score_floor lifts it (max(raw, floor); §10.5),
        disposition:remove forces it to 🔴 (§13.3), protect keeps it out of candidates —
        so preview/ranked/apply all become effective-aware at this one seam."""
        return raw

    @staticmethod
    def _half_life(w) -> float:
        """Guard the decay denominator — a stray 0 (e.g. a hand-rolled curl) would
        divide by zero. Falls back to the locked default."""
        return w.half_life if getattr(w, "half_life", 0) and w.half_life > 0 else HALF_LIFE_WEEKS

    def _score(self, w) -> dict:
        return score_songs(self._songs, self._current_week,
                           half_life=self._half_life(w), type_weight=w.type_weight(),
                           legacy_mult=w.legacy, default_type_weight=w.other)

    @staticmethod
    def _weights_dict(w) -> dict:
        """The active knobs, in the exact shape GET /weights returns and the sliders
        read back (independent of the request model, which may carry paging extras)."""
        return {"half_life": w.half_life, "weekly": w.weekly, "outof": w.outof,
                "other": w.other, "legacy": w.legacy, "bands": w.safe_bands(),
                "candidate_bands": list(w.candidate_bands or ["red"])}

    # ─────────────────────────────── endpoints ─────────────────────────────
    def preview(self, w) -> dict:
        """Live band counts (< 1s). Counts are over FOLLOWED artists and use the
        EFFECTIVE rank so what you see == what apply would write (§10.1)."""
        self._ensure_songs()
        scores = self._score(w)
        followed = self._followed_map()
        bands = w.safe_bands()
        cand_bands = set(w.candidate_bands or [])
        counts = {b: 0 for b in BAND_NAMES}
        candidates = 0
        for uri in followed:
            eff = self._effective(uri, scores.get(uri, {}).get("rank", 0.0))
            b = band_of(eff, bands)
            counts[b] += 1
            if b in cand_bands:
                candidates += 1
        return {"counts": counts, "candidates": candidates, "followed": len(followed),
                "current_week": self._current_week, "generated": self._songs_generated,
                "bands": bands}

    def ranked(self, w, offset: int = 0, limit: int = 100,
               band: Optional[str] = None) -> dict:
        """The sorted, paginated list — consistent with the live counts. Each row
        carries the same visual fields as a /cleanup candidate plus rank/raw_rank/
        entered/band, so the dashboard (and its client-side name search) renders it
        directly."""
        self._ensure_songs()
        scores = self._score(w)
        followed = self._followed_map()
        bands = w.safe_bands()
        rows = []
        for uri, art in followed.items():
            sc = scores.get(uri)
            raw = sc["rank"] if sc else 0.0
            entered = sc["entered"] if sc else 0
            eff = self._effective(uri, raw)
            b = band_of(eff, bands)
            if band and b != band:
                continue
            rows.append((uri, art, raw, eff, entered, b))
        rows.sort(key=lambda r: r[3], reverse=True)         # by effective rank
        total = len(rows)
        offset = max(0, offset)
        page = rows[offset:offset + max(1, limit)]
        out = []
        for uri, art, raw, eff, entered, b in page:
            row = bootstrap._candidate_row(uri, art)
            row.update({"rank": round(eff, 6), "raw_rank": round(raw, 6),
                        "entered": entered, "band": b})
            out.append(row)
        return {"total": total, "offset": offset, "limit": limit,
                "count": len(out), "rows": out}

    def artist(self, uri: str, w) -> dict:
        """The 'why' breakdown for ONE artist: raw + effective RANK, per-song strongest
        placement (→ contribution), and the distinct playlists it appears in. Accepts a
        bare id / uri / open.spotify.com link (artist search by ID)."""
        self._ensure_songs()
        uri = normalize_uri(uri)
        followed = self._followed_map()
        pl_meta = self._playlist_meta()
        art = followed.get(uri) or {}
        tw = w.type_weight()
        bands = w.safe_bands()
        hl = self._half_life(w)

        songs_out, playlists, raw, entered = [], {}, 0.0, 0
        for isrc in self._by_artist.get(uri, []):
            s = self._songs.get(isrc)
            if not s:
                continue
            best, best_contrib = None, 0.0
            for p in s["placements"]:
                pu = p.get("playlist_uri")
                if pu and pu not in playlists:               # collect the playlist overview
                    meta = pl_meta.get(pu) or {}
                    playlists[pu] = {"playlist_uri": pu, "name": meta.get("name"),
                                     "type": meta.get("type") or p.get("type"),
                                     "week_number": meta.get("week_number") or p.get("week_number"),
                                     "spotify_url": meta.get("spotify_url")}
                wt = tw.get(p.get("type"), w.other)
                if wt <= 0:
                    continue
                mult = w.legacy if p.get("legacy") else 1.0
                age = self._current_week - (p.get("week_number") or self._current_week)
                if age < 0:
                    age = 0
                contrib = wt * mult * (0.5 ** (age / hl))
                if contrib > best_contrib:
                    best_contrib, best = contrib, {
                        "week_number": p.get("week_number"), "type": p.get("type"),
                        "legacy": bool(p.get("legacy")), "playlist_uri": pu, "age": age}
            if best_contrib > 0 and best:
                meta = pl_meta.get(best["playlist_uri"]) or {}
                songs_out.append({
                    "isrc": isrc, "album_type": s.get("album_type"),
                    "week_number": best["week_number"], "type": best["type"],
                    "legacy": best["legacy"], "age": best["age"],
                    "playlist_uri": best["playlist_uri"], "playlist_name": meta.get("name"),
                    "contribution": round(best_contrib, 6), "placements": len(s["placements"]),
                })
                raw += best_contrib
                entered += 1
        songs_out.sort(key=lambda r: r["contribution"], reverse=True)
        eff = self._effective(uri, raw)
        base = bootstrap._candidate_row(uri, art)                # safe even when art == {}
        return {
            **{k: base[k] for k in ("artist_uri", "artist_id", "artist", "image",
                                    "genres", "followers", "spotify_url")},
            "followed": uri in followed,
            "rank": round(raw, 6), "effective_rank": round(eff, 6),
            "band": band_of(eff, bands), "entered": entered,
            "songs": songs_out,
            "playlists": sorted(playlists.values(),
                                key=lambda p: (p.get("week_number") or 0), reverse=True),
            "manual": manual_store.get(uri),
        }

    def apply(self, w) -> dict:
        """Write cleanup_candidates.json (the /cleanup contract) for every followed
        artist whose EFFECTIVE band is a candidate band; auto-back-up the current file
        first (§8.6); persist the knobs to health_weights.json (§12.3). Never unfollows
        — /cleanup stays the only place removal happens."""
        self._ensure_songs()
        scores = self._score(w)
        followed = self._followed_map()
        bands = w.safe_bands()
        cand_bands = set(w.candidate_bands or ["red"])
        candidates = [bootstrap._candidate_row(uri, art)
                      for uri, art in followed.items()
                      if band_of(self._effective(uri, scores.get(uri, {}).get("rank", 0.0)),
                                 bands) in cand_bands]

        existing = storage.load_json(CANDIDATES_FILE)
        backup = None
        if existing:
            backup = f"{BACKUP_PREFIX}{_ts()}.json"
            storage.save_json(backup, existing)             # GCS write is atomic (§13.2)

        gen = _ts()
        storage.save_json(CANDIDATES_FILE, {
            "generated": gen, "threshold": 0,
            "window": (f"Health tuning · half-life {w.half_life}w · bands {bands} · "
                       f"candidate {sorted(cand_bands)}"),
            "count": len(candidates), "candidates": candidates,
        })
        storage.save_json(WEIGHTS_FILE, self._weights_dict(w))
        return {"candidates": len(candidates), "followed": len(followed),
                "generated": gen, "backup": backup}


# Global instance (mirrors bootstrap / scanner / automation_manager).
health = HealthEngine()
