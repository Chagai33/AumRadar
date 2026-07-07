import time
import datetime
import logging
from spotipy.exceptions import SpotifyException
import threading

# ── Global adaptive rate limiter (AIMD) ─────────────────────────────────────
# Spotify rate-limits the app-wide token on a rolling window. We pace the
# COMBINED request start rate of all threads and adapt it to the real budget:
#   - any new 429 episode  → halve the rate (multiplicative decrease)
#   - INCREASE_AFTER consecutive successes → +INCREASE_STEP rps (additive increase)
# A shared _blocked_until timestamp holds ALL threads through a 429's
# Retry-After window; in-flight stragglers that 429 during an active block only
# EXTEND the window — they never halve the rate again (no phantom rounds).
START_RPS = 4.0          # proven clean on the 2026-07-03 full scan
MIN_RPS = 1.0
CEIL_RPS = 5.0
INCREASE_STEP = 0.25
INCREASE_AFTER = 200     # consecutive successes before stepping the rate up

_pace_lock = threading.Lock()    # serializes request dispatch (FIFO pacing)
_state_lock = threading.Lock()   # guards the small mutable state below
_current_rps = START_RPS
_last_call_ts = 0.0
_blocked_until = 0.0             # monotonic: no request may start before this
_success_streak = 0

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
            time.sleep(min(wait, 1.0))

def safe_api_call(func, *args, **kwargs):
    """
    Thread-safe wrapper for Spotify API calls.
    - Paces every call to stay under Spotify's app-wide rate budget (adaptive).
    - Short 429 (Retry-After ≤ 60s): blocks all threads for the window, halves
      the global rate once per episode, then resumes automatically.
    - Long 429 (Retry-After > 60s): raises CRITICAL_RATE_LIMIT so the scan stops
      and shows the user exactly how long Spotify is blocking the app.
    """
    global _current_rps, _blocked_until, _success_streak
    while True:
        _pace()

        try:
            result = func(*args, **kwargs)
        except SpotifyException as e:
            if e.http_status != 429:
                raise
            # With 429 excluded from status_forcelist, the real Retry-After
            # header arrives intact (unlike the old MaxRetryError path).
            hdr = e.headers.get('Retry-After') if e.headers else None
            retry_after = (int(hdr) + 1) if hdr else 6

            # Long block = Spotify has hard rate-limited the app → stop the scan
            # and tell the user exactly how long to wait.
            if retry_after > 60:
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
        else:
            msg = None
            with _state_lock:
                _success_streak += 1
                if _success_streak >= INCREASE_AFTER and _current_rps < CEIL_RPS:
                    _success_streak = 0
                    old = _current_rps
                    _current_rps = min(CEIL_RPS, _current_rps + INCREASE_STEP)
                    msg = f"📈 Rate limit healthy — stepping up {old:.2f}→{_current_rps:.2f} rps"
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
            # Check for critical errors raised by safe_api_call
            if "CRITICAL_RATE_LIMIT" in str(e):
                raise e # Propagate up to scanner
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
                                'release_date': album['release_date']
                            }
                        all_tracks[aid] = items
                    else:
                        all_tracks[aid] = []
        except Exception as e:
            # 429s never escape safe_api_call (it paces and retries internally);
            # only a hard rate-limit surfaces — propagate it so the scan stops.
            if "CRITICAL_RATE_LIMIT" in str(e):
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
