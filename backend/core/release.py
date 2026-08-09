# -*- coding: utf-8 -*-
"""Artist Health Engine — Stage 4: Release Quality — the measurement core (phase 2a).

The forward/accumulating half of the engine. Every week, once the official weekly
playlist is published (Thursday, STAGE4.md §19.1), we cross-reference:

    what the artist RELEASED that week (from the weekly scan's history snapshot)
        ×  what ENTERED the official 彡…Week#N playlist (+ #N Outofplaylist = shadow)
    →  a per-artist hit / shadow / miss log  (cache/release/week_<NNN>.json)

Aggregated over weeks (phase 3) this gives Release Quality = entered/released
efficiency — the only thing that catches the "flooder" (releases constantly, rarely
enters), which the snapshot RANK (stages 1-3) misses.

This module is the PURE measurement + persistence. The I/O around it — reading the
scan-history snapshot, pulling the Week#N playlist via the API, the propose-and-confirm
linking, and the coverage/backlog UI — is the next sub-step. v1 is JSON + Python
(no SQLite), consistent with Bootstrap.

Locked rules honored here (STAGE4.md):
- dedup by ISRC; on a shared ISRC a **single WINS over an album** (§22.1) so a
  focus-track single released the same week as its album isn't discarded and skipped.
- albums (album_type=album) are RECORDED but NOT scored (§8) — outcome "album".
- credit EVERY artist on the track (role primary/secondary; §8) — one event per artist.
- outcome per ISRC: weekly hit > outofplaylist shadow > miss (§8/§310).
- coverage.json is written INCREMENTALLY after each week file (crash-safe, §21.5);
  it is the index for aggregation, so no directory listing is needed (§20.5).
"""
import datetime

from .storage_manager import storage

RELEASE_DIR = "cache/release"
COVERAGE_FILE = f"{RELEASE_DIR}/coverage.json"


def _ts() -> str:
    return datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")


def week_file(week_no) -> str:
    return f"{RELEASE_DIR}/week_{week_no}.json"


def _isrc_of(track: dict) -> str:
    """Stable per-recording key; fall back to the track URI so an ISRC-less track
    still counts once instead of vanishing (mirrors Bootstrap)."""
    return (track.get("external_ids") or {}).get("isrc") or track.get("uri")


def _artist_uris(track: dict):
    """All artist URIs on the track, in order (primary first), features included —
    IDs come from the scan (verified). Returns [] if none."""
    out = []
    for a in (track.get("artists") or []):
        uri = a.get("uri") or (f"spotify:artist:{a['id']}" if a.get("id") else None)
        if uri:
            out.append(uri)
    return out


def measure_week(release_tracks, weekly_isrcs, oop_isrcs, week_no) -> tuple:
    """PURE. Fold one week's scanned releases against that week's playlists → per-artist
    release_events + a summary. `weekly_isrcs`/`oop_isrcs` are sets of the ISRCs that
    entered the official weekly / outofplaylist. Returns (events, summary)."""
    weekly_isrcs = weekly_isrcs or set()
    oop_isrcs = oop_isrcs or set()

    # 1) dedup by ISRC — a single beats an album on the same ISRC (§22.1).
    by_isrc = {}
    for t in release_tracks:
        isrc = _isrc_of(t)
        if not isrc:
            continue
        cur = by_isrc.get(isrc)
        if cur is None:
            by_isrc[isrc] = t
            continue
        cur_album = (cur.get("album") or {}).get("album_type") == "album"
        new_not_album = (t.get("album") or {}).get("album_type") != "album"
        if cur_album and new_not_album:
            by_isrc[isrc] = t            # replace the album record with the single

    # 2) outcome per ISRC → one event per artist (role primary/secondary).
    events = []
    summary = {"releases": 0, "hits": 0, "shadow": 0, "misses": 0, "albums": 0,
               "artists": 0}
    artist_seen = set()
    for isrc, t in by_isrc.items():
        album_type = (t.get("album") or {}).get("album_type")
        if album_type == "album":
            outcome = "album"            # recorded, NOT scored (§8)
            summary["albums"] += 1
        else:
            if isrc in weekly_isrcs:
                outcome = "hit"; summary["hits"] += 1
            elif isrc in oop_isrcs:
                outcome = "shadow"; summary["shadow"] += 1
            else:
                outcome = "miss"; summary["misses"] += 1
            summary["releases"] += 1

        for idx, auri in enumerate(_artist_uris(t)):
            events.append({
                "artist_uri": auri,
                "isrc": isrc,
                "track_uri": t.get("uri"),
                "role": "primary" if idx == 0 else "secondary",
                "release_week": week_no,
                "album_type": album_type,
                "outcome": outcome,
            })
            artist_seen.add(auri)
    summary["artists"] = len(artist_seen)
    return events, summary


# ─────────────────────────── coverage / persistence ────────────────────────────
def load_coverage() -> dict:
    """The per-week index (empty-safe on first run; §13.6). `weeks` maps a week number
    (str) → its measurement record; `start_week` bounds what counts as a gap."""
    cov = storage.load_json(COVERAGE_FILE, default=None)
    if not cov:
        return {"generated": _ts(), "start_week": None, "weeks": {}}
    cov.setdefault("weeks", {})
    return cov


def week_status(cov: dict, week_no) -> str:
    """measured | (absent) — a week the coverage index has a 'measured' record for."""
    rec = (cov.get("weeks") or {}).get(str(week_no))
    return rec.get("status") if rec else "unmeasured"


def write_week(week_no, events, summary, *, scan_id=None, playlist_uri=None,
               source="weekly", filter_fingerprint=None) -> dict:
    """Persist one week's measurement: the week file FIRST, then update coverage
    (incremental → a crash never leaves coverage claiming an unwritten week; §21.5).
    Re-measuring a week REPLACES its file (idempotent; §20.3). Returns the coverage rec."""
    storage.save_json(week_file(week_no), {
        "week": week_no, "generated": _ts(), "source": source,
        "scan_id": scan_id, "playlist_uri": playlist_uri,
        "filter_fingerprint": filter_fingerprint,
        "summary": summary, "events": events,
    })
    cov = load_coverage()
    if cov.get("start_week") is None:
        cov["start_week"] = week_no
    else:
        try:
            cov["start_week"] = min(cov["start_week"], week_no)
        except TypeError:
            pass
    cov["weeks"][str(week_no)] = {
        "status": "measured", "measured_at": _ts(), "source": source,
        "scan_id": scan_id, "playlist_uri": playlist_uri,
        "filter_fingerprint": filter_fingerprint, **summary,
    }
    cov["generated"] = _ts()
    storage.save_json(COVERAGE_FILE, cov)
    return cov["weeks"][str(week_no)]


def backlog_weeks(cov: dict, candidate_weeks) -> list:
    """PURE. Given the coverage index and the weeks that COULD be measured (a completed
    weekly scan exists AND the official playlist is published), return the unmeasured
    ones, ascending — the multi-week catch-up list (user requirement 2026-08-09): if
    you didn't confirm for several weeks, they all surface together for batch linking."""
    measured = set((cov.get("weeks") or {}).keys())
    start = cov.get("start_week")
    out = []
    for wk in candidate_weeks:
        if start is not None:
            try:
                if wk < start:
                    continue
            except TypeError:
                pass
        if str(wk) not in measured:
            out.append(wk)
    return sorted(out)


def aggregate_events():
    """Load every measured week's events from the coverage index (NOT a dir scan; §20.5).
    Returns (all_events, weeks_measured, gaps_note). Phase 3 turns this into Release
    Quality. Placeholder-light here; the scoring/smoothing lives in phase 3."""
    cov = load_coverage()
    weeks = sorted(cov.get("weeks", {}).keys(), key=lambda w: int(w) if str(w).isdigit() else w)
    all_events = []
    for w in weeks:
        wf = storage.load_json(week_file(w))
        if wf and wf.get("events"):
            all_events.extend(wf["events"])
    return all_events, weeks
