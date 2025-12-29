import time
import datetime
import logging
from spotipy.exceptions import SpotifyException
import threading

# Global locks for Rate Limit Synchronization
rate_limit_event = threading.Event()
rate_limit_event.set() # Initially Green

def safe_api_call(func, *args, **kwargs):
    """
    Thread-safe wrapper for Spotify API calls.
    Blocks all threads if a Rate Limit (429) is hit by any thread.
    """
    while True:
        rate_limit_event.wait() # Wait if Red Light is on

        try:
            return func(*args, **kwargs)
        except SpotifyException as e:
            if e.http_status == 429:
                # If we are the first to hit the wall, set Red Light
                if rate_limit_event.is_set():
                    rate_limit_event.clear() # Red Light - STOP EVERYONE
                    
                    retry_after = int(e.headers.get('Retry-After', 5)) + 1
                    msg = f"⛔ GLOBAL RATE LIMIT HIT! Pausing ALL threads for {retry_after}s."
                    log_message(msg)
                    
                    if retry_after > 70: # If too long, maybe just abort?
                         # For now we sleep, but user can see status. 
                         # Actually, let's Raise Critical if HUGE
                         if retry_after > 120:
                             rate_limit_event.set() # Release so others can fail too? or keep blocked?
                             raise Exception(f"CRITICAL_RATE_LIMIT: Wait time {retry_after}s is too long.")
                    
                    time.sleep(retry_after)
                    
                    log_message("✅ Resuming API calls...")
                    rate_limit_event.set() # Green Light
                else:
                    # Someone else is already handling the sleep, just wait
                    time.sleep(1) 
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
