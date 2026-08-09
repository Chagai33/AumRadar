import time
import datetime
import logging
import random
import requests
from spotipy.exceptions import SpotifyException
import threading


class ScanInterruptedException(Exception):
    """Raised when a Spotify call keeps failing on a TRANSIENT error (a 5xx or a
    network drop) after all retries are exhausted. It signals the scanner to stop
    cleanly and save a resumable checkpoint — NOT to silently skip the artist,
    which would drop that artist's tracks (bug #2). A hard rate-limit uses the
    separate CRITICAL_RATE_LIMIT path; a real 4xx (auth / 404) is not transient
    and propagates normally."""
    pass


# Transient-error retry policy (5xx / network). Backoff runs OUTSIDE the pacing/
# state locks (see _sleep_backoff) so one retrying call never stalls the others.
TRANSIENT_BACKOFF = (2, 4, 8)   # seconds per retry attempt (+ jitter)


# ── Global adaptive rate limiter (AIMD) ─────────────────────────────────────
# Spotify rate-limits the app-wide token on a rolling window. We pace the
# COMBINED request start rate of all threads to a deliberately conservative rate:
#   - START_RPS == CEIL_RPS → the rate never climbs above the safe cruise speed.
#   - any new 429 episode → halve the rate (multiplicative decrease); it may then
#     recover back UP toward the ceiling over INCREASE_AFTER successes, never above.
#   - every BREATHE_EVERY_N requests we insert a ~BREATHE_SECONDS "breather" so a
#     long scan never sustains a rate that trips Spotify's silent long-window quota.
# A shared _blocked_until timestamp holds ALL threads through a 429 window / breather.
START_RPS = 2.0          # conservative cruise (was 4.0); ≈2 rps → ~46 min for ~5k requests
MIN_RPS = 1.0
CEIL_RPS = 2.0           # == START_RPS → no auto step-up above the safe rate
INCREASE_STEP = 0.25
INCREASE_AFTER = 200     # consecutive successes before recovering the rate

BREATHE_EVERY_N = 500    # insert a breather every N successful requests
BREATHE_SECONDS = 25     # length of each breather

_pace_lock = threading.Lock()    # serializes request dispatch (FIFO pacing)
_state_lock = threading.Lock()   # guards the small mutable state below
_current_rps = START_RPS
_last_call_ts = 0.0
_blocked_until = 0.0             # monotonic: no request may start before this
_success_streak = 0
_request_count = 0               # successful calls, for the periodic breather
# Hard-abort latch: once a long (>60s) block is seen, every in-flight thread
# short-circuits at its next safe_api_call instead of spamming more requests.
# MUST be cleared by reset_pacing() at the start of each scan (globals persist
# across scans in the BackgroundTask process model).
_CRITICAL_ABORT = False
_CRITICAL_SECONDS = 0


def reset_pacing():
    """Reset ALL cross-scan pacing globals. Call at the very start of every scan
    (scan_process AND resume_scan): in the BackgroundTask model these globals
    survive in the process between scans, so a _CRITICAL_ABORT latched by a
    blocked scan would otherwise instantly kill the next one."""
    global _current_rps, _blocked_until, _success_streak, _last_call_ts
    global _request_count, _CRITICAL_ABORT, _CRITICAL_SECONDS
    with _state_lock:
        _current_rps = START_RPS
        _blocked_until = 0.0
        _success_streak = 0
        _last_call_ts = 0.0
        _request_count = 0
        _CRITICAL_ABORT = False
        _CRITICAL_SECONDS = 0

def _pace():
    """Block until (a) the shared 429 window has passed and (b) at least
    1/_current_rps seconds since the last request start. One thread paces at a
    time (FIFO); state is sampled in short slices so a 429 reported while we
    sleep can extend the block without waiting on us."""
    global _last_call_ts
    with _pace_lock:
        while True:
            with _state_lock:
                now = time.monotonic()
                wait = max(_blocked_until - now,
                           _last_call_ts + (1.0 / _current_rps) - now)
                if wait <= 0:
                    _last_call_ts = now
                    return
            # Small jitter (≤0.1s) so the serialized threads don't fire at a
            # perfectly regular cadence. Kept small ON PURPOSE: it is added to
            # EVERY pace sleep, so a wider range would drag the effective rate well
            # below the 2 rps target (uniform(0.1,0.4) → ~1.3 rps / ~65 min).
            time.sleep(min(wait + random.uniform(0.0, 0.1), 1.0))

def _sleep_backoff(attempt, label):
    """Lock-free backoff for transient retries. MUST be called while holding
    NEITHER _pace_lock NOR _state_lock, so a call waiting out a 5xx/network blip
    never freezes the other worker threads (they keep pacing and dispatching)."""
    base = TRANSIENT_BACKOFF[min(attempt, len(TRANSIENT_BACKOFF)) - 1]
    delay = base + random.uniform(0.0, 0.5)   # jitter so parallel retries don't align
    log_message(f"⚠️ {label} — transient, retry {attempt}/{len(TRANSIENT_BACKOFF)} in {delay:.1f}s")
    time.sleep(delay)

def safe_api_call(func, *args, **kwargs):
    """
    Thread-safe wrapper for Spotify API calls.
    - Paces every call to stay under Spotify's app-wide rate budget (adaptive).
    - Short 429 (Retry-After ≤ 60s): blocks all threads for the window, halves
      the global rate once per episode, then resumes automatically.
    - Long 429 (Retry-After > 60s): raises CRITICAL_RATE_LIMIT so the scan stops
      and shows the user exactly how long Spotify is blocking the app.
    - Transient 5xx / network error: retries the SAME call up to
      len(TRANSIENT_BACKOFF) times with lock-free backoff; if it still fails,
      raises ScanInterruptedException so the scan stops cleanly and saves a
      resumable checkpoint — never a silent skip that would drop tracks (bug #2).
    """
    global _current_rps, _blocked_until, _success_streak
    global _request_count, _CRITICAL_ABORT, _CRITICAL_SECONDS
    transient_attempts = 0
    while True:
        # A long block latched by any thread short-circuits the rest — no point
        # firing more requests into a hard rate-limit (checked BEFORE the network).
        if _CRITICAL_ABORT:
            raise Exception(f"CRITICAL_RATE_LIMIT:{_CRITICAL_SECONDS or 0}")
        _pace()

        try:
            result = func(*args, **kwargs)
        except SpotifyException as e:
            if e.http_status == 429:
                # With 429 excluded from status_forcelist, the real Retry-After
                # header arrives intact (unlike the old MaxRetryError path).
                hdr = e.headers.get('Retry-After') if e.headers else None
                retry_after = (int(hdr) + 1) if hdr else 6

                # Long block = Spotify has hard rate-limited the app → latch the
                # abort so every other in-flight thread stops too, then raise.
                if retry_after > 60:
                    with _state_lock:
                        _CRITICAL_ABORT = True
                        _CRITICAL_SECONDS = retry_after
                    raise Exception(f"CRITICAL_RATE_LIMIT:{retry_after}")

                target = time.monotonic() + retry_after
                msg = None
                with _state_lock:
                    _success_streak = 0
                    # New episode only if we're not already inside a block window —
                    # in-flight stragglers extend the window but don't re-halve.
                    new_episode = time.monotonic() >= _blocked_until
                    _blocked_until = max(_blocked_until, target)
                    if new_episode:
                        old = _current_rps
                        _current_rps = max(MIN_RPS, _current_rps / 2)
                        msg = (f"⛔ 429 from Spotify — pausing all requests {retry_after}s, "
                               f"rate {old:.2f}→{_current_rps:.2f} rps")
                if msg:
                    log_message(msg)
                continue  # loop → _pace() waits out the shared block, then retries

            # Transient server error (5xx) or a spotipy-wrapped network error
            # (http_status == -1): back off (lock-free) and retry the SAME call.
            if e.http_status in (500, 502, 503, 504) or e.http_status == -1:
                transient_attempts += 1
                if transient_attempts > len(TRANSIENT_BACKOFF):
                    raise ScanInterruptedException(
                        f"Spotify API kept failing ({e.http_status}) after "
                        f"{len(TRANSIENT_BACKOFF)} retries: {e}")
                _sleep_backoff(transient_attempts, f"Spotify {e.http_status}")
                continue

            # Real 4xx (auth / 404 / …) — not transient. Surface it; the caller's
            # per-artist handler logs and skips just that artist.
            raise

        except requests.exceptions.RequestException as e:
            # Raw network error not wrapped by spotipy (connection reset / read
            # timeout) — transient. Back off (lock-free) and retry the same call.
            transient_attempts += 1
            if transient_attempts > len(TRANSIENT_BACKOFF):
                raise ScanInterruptedException(
                    f"Network kept failing after {len(TRANSIENT_BACKOFF)} retries: {e}")
            _sleep_backoff(transient_attempts, "network error")
            continue

        else:
            msg = None
            breathe = False
            with _state_lock:
                _success_streak += 1
                _request_count += 1
                # Periodic breather: extend the shared block so ALL threads pause,
                # keeping a long scan off Spotify's silent long-window quota.
                if BREATHE_EVERY_N and _request_count % BREATHE_EVERY_N == 0:
                    breathe = True
                    _blocked_until = max(_blocked_until, time.monotonic() + BREATHE_SECONDS)
                if _success_streak >= INCREASE_AFTER and _current_rps < CEIL_RPS:
                    _success_streak = 0
                    old = _current_rps
                    _current_rps = min(CEIL_RPS, _current_rps + INCREASE_STEP)
                    msg = f"📈 Rate limit healthy — stepping up {old:.2f}→{_current_rps:.2f} rps"
            if breathe:
                log_message(f"😮‍💨 Breather — pausing ~{BREATHE_SECONDS}s after {_request_count} requests")
            if msg:
                log_message(msg)
            return result


# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def log_message(message):
    print(message)
    logger.info(message)

# --- Core Logic ---

def get_normalized_key(track):
    normalized_name = track['name'].lower().strip()
    artists = [artist['name'].lower().strip() for artist in track.get('artists', [])][:2]
    return (normalized_name, tuple(artists))

def filter_tracks(tracks, no_filter_artists, filter_options={}):
    filtered_tracks = []
    excluded_tracks = []
    basic_tracks = []
    
    # Defaults
    min_ms = filter_options.get('min_duration_ms', 90000)
    max_ms = filter_options.get('max_duration_ms', 270000)
    
    default_forbidden = [" live ", "session", "לייב", "קאבר", "a capella", "acapella", "FSOE",
                       "techno", "extended", "sped up", "speed up", "intro", "slow", "remaster", "instrumental"]
    forbidden_words = filter_options.get('forbidden_keywords', default_forbidden)
    if not forbidden_words: forbidden_words = default_forbidden
    
    if 'forbidden_keywords' in filter_options:
         forbidden_words = filter_options['forbidden_keywords']

    for track in tracks:
        name = track['name'].lower()
        duration_ms = track['duration_ms']
        if not track.get('artists'): continue
        artist_id = track['artists'][0]['id']
        
        if artist_id in no_filter_artists:
            filtered_tracks.append(track)
            continue
        
        if any(forbidden in name for forbidden in forbidden_words):
            log_message(f"DEBUG: Skipping '{track['name']}' - Keyword match")
            excluded_tracks.append(track)
            continue
        
        if duration_ms < min_ms or duration_ms > max_ms:
            log_message(f"DEBUG: Skipping '{track['name']}' (Time: {duration_ms/1000}s) - Outside {min_ms/1000}s-{max_ms/1000}s range")
            excluded_tracks.append(track)
            continue
            
        basic_tracks.append(track)

    groups = {}
    for track in basic_tracks:
        key = get_normalized_key(track)
        groups.setdefault(key, []).append(track)

    for key, group in groups.items():
        explicit_tracks = [t for t in group if t.get('explicit', False)]
        non_explicit_tracks = [t for t in group if not t.get('explicit', False)]
        
        if explicit_tracks:
            filtered_tracks.extend(explicit_tracks)
            excluded_tracks.extend(non_explicit_tracks)
        else:
            filtered_tracks.extend(group)

    return filtered_tracks, excluded_tracks

# --- Spotify Interactions (Synchronous & Robust) ---

def get_artist_albums(sp, artist_id, include_groups, start_date_obj):
    all_albums = []
    offset = 0
    limit = 50
    
    while True:
        try:
            # Use GLOBAL SAFE API CALL
            results = safe_api_call(sp.artist_albums, artist_id, include_groups=include_groups, limit=limit, offset=offset)
            items = results.get('items', [])
            
            if not items:
                break

            for item in items:
                r_date_str = item.get('release_date')
                if not r_date_str: continue

                try:
                    if len(r_date_str) == 4:
                        r_date = datetime.datetime.strptime(r_date_str, '%Y').date()
                    elif len(r_date_str) == 7:
                        r_date = datetime.datetime.strptime(r_date_str, '%Y-%m').date()
                    else:
                        r_date = datetime.datetime.strptime(r_date_str, '%Y-%m-%d').date()
                except ValueError:
                    continue
                
                # Check if item is within or after start date
                if r_date >= start_date_obj:
                    all_albums.append(item)
                else:
                    # Optimized return: Stop if we hit old albums (assuming sorted)
                    return all_albums
            
            if len(items) < limit:
                break
                
            offset += limit
            
        except Exception as e:
            # Propagate scan-stopping signals up to the scanner: a hard rate-limit
            # (CRITICAL_RATE_LIMIT) or an exhausted transient retry
            # (ScanInterruptedException). Both must stop the scan and save a
            # resumable checkpoint — NOT be swallowed here (that would lose tracks).
            if isinstance(e, ScanInterruptedException) or "CRITICAL_RATE_LIMIT" in str(e):
                raise
            # Real 4xx / parse error for this artist only — log which artist we
            # skip (transparency) and move on; there are simply no tracks here.
            log_message(f"Error fetching albums for artist {artist_id}: {e}")
            break
            
    return all_albums

def get_tracks_for_albums_in_batch(sp, album_ids):
    all_tracks = {}
    batch_size = 20
    idx = 0
    while idx < len(album_ids):
        chunk = album_ids[idx:idx + batch_size]
        try:
            albums_data = safe_api_call(sp.albums, chunk)
            for album in albums_data['albums']:
                if album and 'id' in album:
                    aid = album['id']
                    if 'tracks' in album and album['tracks']['items']:
                        # Inject metadata
                        items = album['tracks']['items']
                        for t in items:
                            t['album'] = {
                                'id': album['id'],
                                'name': album['name'],
                                'images': album['images'],
                                'release_date': album['release_date'],
                                'album_type': album.get('album_type'),  # Stage 4: single vs album (skip albums in scoring)
                            }
                        all_tracks[aid] = items
                    else:
                        all_tracks[aid] = []
        except Exception as e:
            # 429s never escape safe_api_call (it paces and retries internally).
            # A hard rate-limit (CRITICAL_RATE_LIMIT) or an exhausted transient
            # retry (ScanInterruptedException) must stop the scan → propagate.
            if isinstance(e, ScanInterruptedException) or "CRITICAL_RATE_LIMIT" in str(e):
                raise
            log_message(f"Error in batch fetch: {e}")

        idx += batch_size
    return all_tracks

def get_new_releases(sp, artist_id, start_date, end_date, filter_options={}):
    new_releases = []
    # Default to single,album if not specified
    include_groups = filter_options.get('include_groups', 'album,single')
    
    # Use Smart Pagination with Cutoff
    releases = get_artist_albums(sp, artist_id, include_groups, start_date)
    
    for album in releases:
        try:
            r_date_str = album['release_date']
            if len(r_date_str) == 4:
                r_date = datetime.datetime.strptime(r_date_str, '%Y').date()
            elif len(r_date_str) == 7: 
                 r_date = datetime.datetime.strptime(r_date_str, '%Y-%m').date()
            else:
                r_date = datetime.datetime.strptime(r_date_str, '%Y-%m-%d').date()

            # Double check range (End Date)
            if start_date <= r_date <= end_date:
                new_releases.append(album)
        except ValueError:
            continue
            
    return new_releases

def process_artist(sp, artist, exclusion_artists, no_filter_artists, start_date, end_date, filter_options={}):
    """
    Orchestrates the check for a single artist.
    """
    artist_id = artist['id']
    if artist_id in exclusion_artists:
        return ([], [])
        
    # log_message(f"Processing artist: {artist['name']}") # Too verbose for 2000 artists
    
    new_releases = get_new_releases(sp, artist_id, start_date, end_date, filter_options)
    filtered = []
    excluded = []
    
    if new_releases:
        album_ids = [release['id'] for release in new_releases]
        # Note: If an artist has multiple new releases, verify we don't spam.
        # usually 1 or 2 new releases.
        
        batched_tracks = get_tracks_for_albums_in_batch(sp, album_ids)
        
        for release in new_releases:
            aid = release['id']
            if aid in batched_tracks:
                f_tracks, e_tracks = filter_tracks(batched_tracks[aid], no_filter_artists, filter_options)
                filtered.extend(f_tracks)
                excluded.extend(e_tracks)
                
    return (filtered, excluded)
