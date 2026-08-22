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


def _track_keys(track: dict) -> set:
    """BOTH identities of a recording: its ISRC *and* its track URI.

    Matching on one alone is provably lossy in this dataset (2026-08-22 audit):
    - scans that predate ISRC enrichment (< 2026-08-09) carry NO ISRC at all, so an
      ISRC-only compare against a playlist can never intersect → every week reads
      0 entered / all missed.
    - the same ISRC can appear under two different track URIs in one scan (a focus
      track shipped on two singles). The ISRC dedup keeps one of them; the playlist
      may hold the other, so a URI-only compare misses it.
    Carrying both and intersecting is correct under either gap."""
    keys = set()
    isrc = (track.get("external_ids") or {}).get("isrc")
    if isrc:
        keys.add(isrc)
    if track.get("uri"):
        keys.add(track["uri"])
    return keys


def _artist_uris(track: dict):
    """All artist URIs on the track, in order (primary first), features included —
    IDs come from the scan (verified). Returns [] if none."""
    out = []
    for a in (track.get("artists") or []):
        uri = a.get("uri") or (f"spotify:artist:{a['id']}" if a.get("id") else None)
        if uri:
            out.append(uri)
    return out


def measure_week(release_tracks, weekly_keys, oop_keys, week_no) -> tuple:
    """PURE. Fold one week's scanned releases against that week's playlists → per-artist
    release_events + a summary. `weekly_keys`/`oop_keys` are sets holding BOTH the ISRCs
    and the track URIs that entered the official weekly / outofplaylist (see
    `_track_keys` for why both). Returns (events, summary)."""
    weekly_keys = weekly_keys or set()
    oop_keys = oop_keys or set()

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
            tkeys = _track_keys(t)
            if tkeys & weekly_keys:
                outcome = "hit"; summary["hits"] += 1
            elif tkeys & oop_keys:
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


class PlaylistUnreadable(Exception):
    """A playlist the measurement needs could not be read (deleted / private / no
    scope). Raised instead of silently returning an empty set — an empty 'entered'
    side is indistinguishable from 'nothing entered', which is exactly how six weeks
    of all-missed data got written on 2026-08-22."""


def _playlist_keys(sp, playlist_uri: str, *, required=True) -> tuple:
    """The recordings currently in a playlist (the 'entered' side), as (keys, items).
    `keys` is the flat union of every item's keys; `items` is one key-set PER recording,
    which is what a match RATIO must be computed over (a track contributes two keys).
    Paginated. Uses the session/user client, whose 429s surface with Retry-After (§20.4).

    A 403/404 raises PlaylistUnreadable when `required` (the official weekly playlist —
    without it there is no measurement); for an optional playlist (outofplaylist) it
    returns empty so a missing shadow list does not sink the whole week."""
    if not playlist_uri:
        return set(), []
    pid = _uri_to_id(playlist_uri)
    keys, items, offset = set(), [], 0
    while True:
        try:
            page = sp.playlist_items(pid, limit=100, offset=offset, additional_types=("track",),
                                     fields="next,items(track(uri,external_ids(isrc)))")
        except Exception as e:
            if getattr(e, "http_status", None) in (403, 404):
                if required:
                    raise PlaylistUnreadable(
                        f"Playlist {pid} could not be read (deleted, private, or missing "
                        f"scope) — the week cannot be measured.")
                return set(), []
            raise
        page_items = page.get("items") or []
        for it in page_items:
            tr = (it or {}).get("track") or {}
            tkeys = _track_keys(tr)
            if tkeys:
                keys |= tkeys
                items.append(tkeys)
        if len(page_items) < 100:
            break
        offset += 100
    return keys, items


IN_PROGRESS_MARK = "❤"   # ❤ — the user marks a weekly playlist that is still being
                              # built as e.g. "Week#325❤️❤️❤️", and only renames it to the
                              # closed form ("彡 <title> 彡 Week#325") when he finalises it
                              # on the weekend. Measuring it before then would score a
                              # half-filled playlist as if it were the finished week.


def is_closed_week(playlist: dict) -> bool:
    """A weekly playlist is measurable only once it is CLOSED. An unfinished one still
    carries the heart marker in its name."""
    return IN_PROGRESS_MARK not in (playlist.get("name") or "")


MIN_MATCH = 0.6            # a scan is the playlist's source only if it CONTAINS at least
                           # this share of it. Real pairings measure ~1.0 (the playlist is
                           # built out of the scan); a wrong pairing measures ~0.0. The gate
                           # sits far from both so small gaps — a track pulled from Spotify,
                           # an ISRC that moved — never reject a genuine week.
MAX_CANDIDATES = 10        # how many scans to try before giving up on a week. History
                           # holds a handful of legitimate weekly scans, so this covers
                           # all of them — the loop stops at the first exact match.


class NoMatchingScan(Exception):
    """No scan in history actually contains this week's playlist, so there is no valid
    denominator and the week MUST NOT be written. Recording it anyway is what produced
    six weeks of fabricated 'all missed' data (weeks 314/320, 2026-08-22) — every artist
    in them reads as a 0%-efficiency flooder."""


def _scan_keys(tracks) -> set:
    """Flat key set of a whole scan snapshot — the haystack a playlist is matched against."""
    keys = set()
    for t in tracks:
        keys |= _track_keys(t)
    return keys


def _match_ratio(playlist_items, scan_keys) -> float:
    """Share of the playlist's RECORDINGS that exist in the scan. 1.0 = the scan is
    provably the playlist's source; ~0.0 = unrelated week."""
    if not playlist_items:
        return 0.0
    hit = sum(1 for tkeys in playlist_items if tkeys & scan_keys)
    return hit / len(playlist_items)


def _candidate_scan_ids(proposed, history, week_no=None):
    """Every legitimate weekly scan, best guess first — the search space for "which scan
    is this playlist built from?".

    There is deliberately NO date arithmetic deciding anything here. The week number is
    a running counter the user assigns, the scan runs on Friday, and the playlist is
    closed the following weekend — an offset that no anchor can infer reliably (trying
    to cost week 321 its measurement entirely). Since a playlist is built out of exactly
    one scan, the content match is both cheaper to trust and exact. `proposed` is only a
    hint about where to start looking; recency is the fallback order because unmeasured
    weeks are recent ones."""
    valid = _valid_weekly_scans(history, MIN_WEEK_TRACKS)   # already newest-first
    out = [proposed] if proposed else []
    for v in valid:
        if v["id"] not in out:
            out.append(v["id"])
    return out[:MAX_CANDIDATES]


def measure_and_write(sp, week_no, playlist_uri, scan_id, oop_playlist_uri=None,
                      source="weekly", history=None, scan_cache=None) -> dict:
    """Orchestrate one week's measurement: pull the official Week#N playlist, find the
    scan that ACTUALLY contains it, fold via measure_week, and persist.

    The pairing is verified by CONTENT, not asserted by date. The playlist is built out
    of one weekly scan, so the true source contains ~100% of it and every other scan
    contains ~0% — a separation wide enough that the proposal only needs to be close,
    not exact. If no candidate clears MIN_MATCH the week is NOT written; it stays an
    honest gap. Returns the summary plus the scan actually used and its match ratio."""
    weekly_keys, weekly_items = _playlist_keys(sp, playlist_uri, required=True)
    if not weekly_items:
        raise NoMatchingScan(f"Week {week_no}: the weekly playlist is empty — nothing to measure.")

    # A multi-week batch searches the same handful of scans over and over; the caller
    # passes a dict so each snapshot is fetched from storage at most once per request.
    cache = scan_cache if scan_cache is not None else {}
    best_id, best_ratio, best_tracks = None, 0.0, None
    for cand in _candidate_scan_ids(scan_id, history or [], week_no):
        if cand not in cache:
            t = _load_scan_releases(cand)
            cache[cand] = (t, _scan_keys(t))
        tracks, keys = cache[cand]
        if not tracks:
            continue
        ratio = _match_ratio(weekly_items, keys)
        if ratio > best_ratio:
            best_id, best_ratio, best_tracks = cand, ratio, tracks
        if ratio >= 0.999:
            break                      # exact source found; no reason to keep looking
    if best_ratio < MIN_MATCH:
        raise NoMatchingScan(
            f"Week {week_no}: no scan in history contains this playlist "
            f"(best match {best_ratio:.0%} of {len(weekly_items)} tracks, need "
            f"{MIN_MATCH:.0%}). The week needs a real weekly scan — leaving it unmeasured.")

    oop_keys = _playlist_keys(sp, oop_playlist_uri, required=False)[0] if oop_playlist_uri else set()
    events, summary = measure_week(best_tracks, weekly_keys, oop_keys, week_no)
    summary = {**summary, "playlist_tracks": len(weekly_items),
               "match_ratio": round(best_ratio, 4), "matched_scan": best_id}
    write_week(week_no, events, summary, scan_id=best_id, playlist_uri=playlist_uri,
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
    """Coverage view + the backlog of weeks still to measure. Pure.

    No scan is PROPOSED here and none has to be picked in the UI. A weekly playlist is
    built out of exactly one scan, so the scan that contains it is a fact to be looked
    up, not a choice to be made — measure_and_write finds it by content. Every date
    heuristic tried before this got weeks wrong in a way the user then had to correct
    by hand (2026-08-22).

    Two rules decide what is measurable:
    - only LEGITIMATE weekly scans count as sources (completed + non-partial + ~one-week
      span + >= min_tracks), so a 1-day, multi-week or tiny run never becomes a partial
      denominator;
    - only CLOSED weeks are offered — one still carrying the ❤ marker is mid-build and
      would score as a half-filled week.

    The backlog is capped at a little more than the number of scans in history: a week
    older than every scan can never be matched, and listing hundreds of dead weeks helps
    nobody."""
    weeklies = [p for p in (playlists or []) if p.get("type") == "weekly" and p.get("week_number")]
    weeklies.sort(key=lambda p: p["week_number"], reverse=True)
    closed = [p for p in weeklies if is_closed_week(p)]
    in_progress = [p for p in weeklies if not is_closed_week(p)]
    oop_by_week = {p["week_number"]: p["playlist_uri"] for p in (playlists or [])
                   if p.get("type") == "outofplaylist" and p.get("week_number")}
    measured = set((cov.get("weeks") or {}).keys())
    valid = _valid_weekly_scans(history, min_tracks)

    backlog = []
    if valid:
        for p in closed:
            if str(p["week_number"]) in measured:
                continue
            backlog.append({
                "week_number": p["week_number"],
                "playlist_uri": p["playlist_uri"],
                "playlist_name": p.get("name"),
                "playlist_tracks": p.get("track_count"),
                "oop_playlist_uri": oop_by_week.get(p["week_number"]),
                # Compatibility shim for a frontend build that predates auto-matching:
                # it treats a row without `proposed_scan_id` as unmeasurable and greys it
                # out. The value is a placeholder only — measure_and_write re-derives the
                # real scan from the playlist's contents and ignores whatever is sent.
                "proposed_scan_id": valid[0]["id"],
                "proposed_scan_dates": f"{valid[0].get('start_date')} .. {valid[0].get('end_date')}",
                "proposed_scan_tracks": valid[0].get("track_count"),
            })
            if len(backlog) >= len(valid) + 2:
                break
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
        "in_progress": [{"week_number": p["week_number"], "name": p.get("name")}
                        for p in in_progress],
    }
