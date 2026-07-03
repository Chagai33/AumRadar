import time
import random
import datetime
import logging
from spotipy.exceptions import SpotifyException
import threading

# ── Rate-limit coordination across threads ──────────────────────────────────
# Green (set) = all clear. Red (cleared) = one thread is sleeping off a 429.
rate_limit_event = threading.Event()
rate_limit_event.set()

# Global request pacer. Spotify rate-limits the app-wide Client-Credentials token
# on a rolling window. With 5 threads and no throttle the scan bursts ~9 req/s and
# Spotify escalates to a multi-hour hard block. We cap the COMBINED start rate of
# all threads to ~MAX_RPS requests/sec so we stay under budget and never trip it.
MAX_RPS = 4.0
MIN_INTERVAL = 1.0 / MAX_RPS  # seconds between request starts, globally
_pace_lock = threading.Lock()
_last_call_ts = 0.0

# Backoff escalates while we keep getting throttled, resets on any success.
# Only affects how long we sleep — never aborts the scan.
_rl_lock = threading.Lock()
_backoff_rounds = 0
MAX_BACKOFF = 60  # cap a short-429 sleep at 60s (CRITICAL path handles real blocks)

def _pace():
    """Block until at least MIN_INTERVAL has passed since the last request start.
    Serializes request *timing* across all threads to ~MAX_RPS/sec."""
    global _last_call_ts
    with _pace_lock:
        now = time.monotonic()
        wait = _last_call_ts + MIN_INTERVAL - now
        if wait > 0:
            time.sleep(wait)
            now = time.monotonic()
        _last_call_ts = now

def safe_api_call(func, *args, **kwargs):
    """
    Thread-safe wrapper for Spotify API calls.
    - Paces every call to stay under Spotify's app-wide rate budget.
    - Short 429 (Retry-After ≤ 60s): one thread pauses everyone, sleeps (with
      escalating backoff), resumes.
    - Long 429 (Retry-After > 60s): raises CRITICAL_RATE_LIMIT so the scan stops
      and shows the user exactly how long Spotify is blocking the app.
    """
    global _backoff_rounds
    while True:
        rate_limit_event.wait()  # Block if another thread is sleeping off a 429
        _pace()                  # Global rate cap — throttle request start rate

        try:
            result = func(*args, **kwargs)
            # Success — clear the escalating backoff.
            with _rl_lock:
                _backoff_rounds = 0
            return result
        except SpotifyException as e:
            if e.http_status == 429:
                # With 429 excluded from status_forcelist, the real Retry-After
                # header arrives intact (unlike the old MaxRetryError path).
                hdr = e.headers.get('Retry-After') if e.headers else None
                retry_after = (int(hdr) + 1) if hdr else 6

                # Long block = Spotify has hard rate-limited the app → stop the scan
                # and tell the user exactly how long to wait.
                if retry_after > 60:
                    raise Exception(f"CRITICAL_RATE_LIMIT:{retry_after}")

                # Short block = normal throttle. Exactly ONE thread becomes the
                # handler per round (guarded by the lock, so no lockstep clear);
                # everyone else loops back to rate_limit_event.wait() above.
                became_handler = False
                with _rl_lock:
                    if rate_limit_event.is_set():
                        rate_limit_event.clear()  # Red Light — stop all threads
                        _backoff_rounds += 1
                        rounds = _backoff_rounds
                        became_handler = True

                if became_handler:
                    sleep_for = min(retry_after * (2 ** min(rounds - 1, 4)), MAX_BACKOFF)
                    log_message(f"⛔ RATE LIMIT HIT (round {rounds}). Pausing all threads for {sleep_for}s.")
                    time.sleep(sleep_for)
                    log_message("✅ Resuming API calls...")
                    rate_limit_event.set()  # Green Light — all threads continue
                else:
                    # Another thread is handling the sleep. Wait, then add jitter
                    # before retrying so threads don't resume in lockstep and
                    # immediately re-burst the API.
                    time.sleep(0.1)
                    time.sleep(random.uniform(0, 0.3))
            else:
                raise e


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
        except SpotifyException as e:
            if e.http_status == 429:
                retry_after = int(e.headers.get('Retry-After', 5))
                log_message(f"429 Too Many Requests (Batch). Retrying after {retry_after} seconds.")
                time.sleep(retry_after)
                continue # Retry same chunk
            else:
                log_message(f"SpotifyException in batch: {e}")
        except Exception as e:
            # Propagate hard rate-limit so the scanner can stop and report it
            if "CRITICAL_RATE_LIMIT" in str(e):
                raise
            log_message(f"Error in batch fetch: {e}")

        idx += batch_size
        time.sleep(0.5) # Gentle cooldown between batches
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
