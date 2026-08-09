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


# ───────────────────────────── Release Quality (3a) ────────────────────────────
def release_quality(events, followed_uris, *, current_week, half_life=26.0,
                    secondary_weight=0.3, shadow_factor=0.05,
                    smoothing_k=4.0, smoothing_prior=0.3) -> dict:
    """PURE. Fold release_events → per-artist forward **efficiency** (how much of what
    an artist releases actually enters). This is the second axis that catches the
    "flooder" (releases constantly, rarely enters) — invisible to the snapshot RANK.

    Per non-album event: decay = 0.5^(age/half_life); role weight = 1.0 primary /
    `secondary_weight` secondary (a feature counts less — "helps more than it hurts",
    §7). Each release adds `w=decay*role` to the DENOMINATOR (an opportunity); to the
    NUMERATOR it adds w on a hit, w*shadow_factor on a shadow, 0 on a miss.
    efficiency = (num + prior*k) / (den + k) — Bayesian shrink toward `smoothing_prior`
    so a 1-of-1 artist doesn't read as 100% (§9). `primary_releases` (own primary,
    non-album) drives the min-sample gate (§22.3). All params ⚙️ tune live (§16).

    soft-prune: only FOLLOWED artists (`followed_uris`) are aggregated (never mutate the
    log). Returns {artist_uri: {efficiency, num, den, hits, releases, primary_releases}}."""
    half_life = half_life if half_life and half_life > 0 else 26.0
    agg = {}
    for e in events:
        uri = e.get("artist_uri")
        if followed_uris is not None and uri not in followed_uris:
            continue
        outcome = e.get("outcome")
        if outcome == "album":
            continue                      # recorded, not scored (§8)
        role = e.get("role")
        rw = 1.0 if role == "primary" else secondary_weight
        age = current_week - (e.get("release_week") or current_week)
        if age < 0:
            age = 0
        w = rw * (0.5 ** (age / half_life))
        a = agg.get(uri)
        if a is None:
            a = agg[uri] = {"num": 0.0, "den": 0.0, "hits": 0, "releases": 0,
                            "primary_releases": 0}
        a["den"] += w
        if outcome == "hit":
            a["num"] += w; a["hits"] += 1
        elif outcome == "shadow":
            a["num"] += w * shadow_factor
        a["releases"] += 1
        if role == "primary":
            a["primary_releases"] += 1
    for uri, a in agg.items():
        denom = a["den"] + smoothing_k
        a["efficiency"] = round((a["num"] + smoothing_prior * smoothing_k) / denom, 4) if denom > 0 else 0.0
    return agg


# ─────────────────────────── I/O + orchestration (2b) ──────────────────────────
HISTORY_DIR = "cache/scan_history"                 # mirrors scanner.py
RECON_SNAPSHOT_FILE = "cache/recon_playlists.json"


def _uri_to_id(uri: str) -> str:
    return uri.rsplit(":", 1)[-1] if uri else uri


def _load_scan_releases(scan_id: str) -> list:
    """The releases for a week = the tracks of that week's completed weekly scan, from
    its IMMUTABLE history snapshot (NOT the transient scan_results.json which any later
    scan overwrites — §21.1). Each track carries external_ids.isrc + album.album_type
    (persisted in phase 1)."""
    return storage.load_json(f"{HISTORY_DIR}/{scan_id}.json", default=[]) or []


def _playlist_isrcs(sp, playlist_uri: str) -> set:
    """The ISRCs currently in a playlist (the 'entered' side). Paginated; a deleted/
    private playlist (403/404) yields what we have rather than failing the whole run.
    Uses the session/user client, whose 429s surface with Retry-After (§20.4)."""
    if not playlist_uri:
        return set()
    pid = _uri_to_id(playlist_uri)
    isrcs, offset = set(), 0
    while True:
        try:
            page = sp.playlist_items(pid, limit=100, offset=offset, additional_types=("track",),
                                     fields="next,items(track(uri,external_ids(isrc)))")
        except Exception as e:
            if getattr(e, "http_status", None) in (403, 404):
                return isrcs
            raise
        items = page.get("items") or []
        for it in items:
            tr = (it or {}).get("track") or {}
            isrc = (tr.get("external_ids") or {}).get("isrc") or tr.get("uri")
            if isrc:
                isrcs.add(isrc)
        if len(items) < 100:
            break
        offset += 100
    return isrcs


def measure_and_write(sp, week_no, playlist_uri, scan_id, oop_playlist_uri=None,
                      source="weekly") -> dict:
    """Orchestrate one week's measurement: load the scan's releases (history snapshot),
    pull the official Week#N playlist (+ optional #N Outofplaylist = shadow), fold via
    measure_week, and persist (week file + coverage). Returns the summary."""
    releases = _load_scan_releases(scan_id)
    weekly_isrcs = _playlist_isrcs(sp, playlist_uri)
    oop_isrcs = _playlist_isrcs(sp, oop_playlist_uri) if oop_playlist_uri else set()
    events, summary = measure_week(releases, weekly_isrcs, oop_isrcs, week_no)
    write_week(week_no, events, summary, scan_id=scan_id, playlist_uri=playlist_uri,
               source=source)
    return summary


MIN_WEEK_TRACKS = 300      # below this a "scan" is a 1-day / test / failed run — NOT a
                           # representative weekly measurement (user rule 2026-08-09).
SCAN_SPAN_DAYS = (5, 9)    # a single-week window (Sat–Fri / Sun–Sat = 6, last7 = 7);
                           # excludes 1-day and multi-week custom ranges (§7 "single week").


def _parse_date(s):
    try:
        return datetime.date.fromisoformat(str(s)[:10])
    except Exception:
        return None


def _valid_weekly_scans(history, min_tracks) -> list:
    """A scan counts as a legitimate weekly measurement only if it's completed, NOT a
    selected-artist run, spans ~one week, AND has enough tracks (a real week yields
    hundreds; a 1-day/test/interrupted run yields a handful). Sorted newest-first."""
    out = []
    for h in (history or []):
        if not h.get("completed") or h.get("partial_scan"):
            continue
        sd, ed = _parse_date(h.get("start_date")), _parse_date(h.get("end_date"))
        if not sd or not ed:
            continue
        if not (SCAN_SPAN_DAYS[0] <= (ed - sd).days <= SCAN_SPAN_DAYS[1]):
            continue                                   # not a single-week window
        if (h.get("track_count") or 0) < min_tracks:
            continue                                   # too few tracks → not representative
        out.append({**h, "_end": ed})
    out.sort(key=lambda h: h["_end"], reverse=True)
    return out


def coverage_state(history, playlists, cov, min_tracks=MIN_WEEK_TRACKS) -> dict:
    """Coverage view + the multi-week backlog with PROPOSED links. Pure.

    FIX (2026-08-09): only LEGITIMATE weekly scans are offered (§7: completed +
    non-partial + ~one-week span + ≥min_tracks) — a 1-day, multi-week, or tiny scan is
    never a measurement source (else the denominator is partial/wrong). And a scan is
    matched to a week by **DATE** (anchor the newest unmeasured week to the newest valid
    scan, then align each week to the scan within ±3 days of its expected weekly slot) —
    not by naive recency-order, which mis-paired weeks with unrelated scans. A week with
    no valid scan near its slot gets NO proposal (needs a real scan / stays a gap). The
    user still confirms/overrides per row in the UI (propose-and-confirm)."""
    weeklies = [p for p in (playlists or []) if p.get("type") == "weekly" and p.get("week_number")]
    weeklies.sort(key=lambda p: p["week_number"], reverse=True)
    oop_by_week = {p["week_number"]: p["playlist_uri"] for p in (playlists or [])
                   if p.get("type") == "outofplaylist" and p.get("week_number")}
    measured = set((cov.get("weeks") or {}).keys())
    valid = _valid_weekly_scans(history, min_tracks)
    unmeasured = [p for p in weeklies if str(p["week_number"]) not in measured]

    backlog = []
    if unmeasured and valid:
        anchor_week = weeklies[0]["week_number"]       # newest week OVERALL ↔ newest valid scan
        anchor_end = valid[0]["_end"]                   # (stable even if the newest week is measured)
        oldest_end = valid[-1]["_end"]
        used = set()
        for p in unmeasured:
            wk = p["week_number"]
            expected = anchor_end - datetime.timedelta(weeks=(anchor_week - wk))
            if expected < oldest_end - datetime.timedelta(days=3):
                break                                  # older than any valid scan → seed territory
            best_i, best_d = None, 4                    # accept a match within ±3 days
            for i, s in enumerate(valid):
                if i in used:
                    continue
                d = abs((s["_end"] - expected).days)
                if d < best_d:
                    best_i, best_d = i, d
            row = {"week_number": wk, "playlist_uri": p["playlist_uri"],
                   "playlist_name": p.get("name"), "oop_playlist_uri": oop_by_week.get(wk),
                   "proposed_scan_id": None, "proposed_scan_dates": None, "proposed_scan_tracks": None}
            if best_i is not None:
                s = valid[best_i]
                used.add(best_i)
                row.update({"proposed_scan_id": s["id"],
                            "proposed_scan_dates": f"{s.get('start_date')} .. {s.get('end_date')}",
                            "proposed_scan_tracks": s.get("track_count")})
            backlog.append(row)
    return {
        "measured_count": len(measured),
        "measured": sorted(measured, key=lambda w: int(w) if str(w).isdigit() else 0,
                           reverse=True)[:12],
        "backlog": backlog,
        "available_scans": len(valid),
        "min_tracks": min_tracks,
        "scans": [{"id": s["id"], "dates": f"{s.get('start_date')} .. {s.get('end_date')}",
                   "tracks": s.get("track_count")} for s in valid[:20]],
        "latest_week": weeklies[0]["week_number"] if weeklies else None,
    }
