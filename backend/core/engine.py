import os
import json
import time
import datetime
import logging
import asyncio
from concurrent.futures import ThreadPoolExecutor
# spotipy is synchronous by default. We will run blocking calls in a threadpool or use async wrapper if needed.
# For now, we keep the logic almost identical but wrap in async functions where we can.

import spotipy
from spotipy.exceptions import SpotifyException

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Constants (File paths - assuming they are in the root or config folder)
# In a web app, strict file paths might be an issue. We will allow passing them as args.
DEFAULT_EXCLUSION_FILE = 'ExclusionArtists.txt'
DEFAULT_NO_FILTER_FILE = 'ArtistNoFilter.txt'

def log_message(message):
    logger.info(message)

# --- Utility Functions ---
def load_ids_from_file(file_name):
    try:
        if os.path.exists(file_name):
            with open(file_name, 'r', encoding='utf-8') as f:
                return [line.strip() for line in f if line.strip()]
        else:
            log_message(f"File {file_name} not found. Proceeding without it.")
            return []
    except Exception as e:
        log_message(f"Error loading file {file_name}: {e}")
        return []

def save_json_to_file(file_name, data):
    try:
        # Ensure dir exists
        os.makedirs(os.path.dirname(os.path.abspath(file_name)), exist_ok=True)
        with open(file_name, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        log_message(f"Error saving to file {file_name}: {e}")

# --- Core Logic ---

def get_normalized_key(track):
    normalized_name = track['name'].lower().strip()
    artists = [artist['name'].lower().strip() for artist in track.get('artists', [])][:2]
    return (normalized_name, tuple(artists))

def filter_tracks(tracks, no_filter_artists):
    """
    Exact copy of legacy filtering logic.
    """
    filtered_tracks = []
    excluded_tracks = []
    basic_tracks = []
    forbidden_words = [" live ", "session", "לייב", "קאבר", "a capella", "acapella", "FSOE",
                       "techno", "extended", "sped up", "speed up", "intro", "slow", "remaster", "instrumental"]


    for track in tracks:
        name = track['name'].lower()
        duration_ms = track['duration_ms']
        # Safe access to artists
        if not track.get('artists'):
            continue
        artist_id = track['artists'][0]['id']
        
        if artist_id in no_filter_artists:
            filtered_tracks.append(track)
            continue
        
        if any(forbidden in name for forbidden in forbidden_words):
            excluded_tracks.append(track)
            continue
        
        # Duration check: 1:30 (90s) to 4:30 (270s)
        if duration_ms < 90000 or duration_ms > 270000:
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
            for t in explicit_tracks:
                filtered_tracks.append(t)
            for t in non_explicit_tracks:
                excluded_tracks.append(t)
        else:
            for t in group:
                filtered_tracks.append(t)

    return filtered_tracks, excluded_tracks

# --- Spotify Interactions (Async Wrappers) ---


async def get_artist_albums_async(sp, artist_id, include_groups='album,single', on_rate_limit=None):
    # Running sync call in thread to avoid blocking main loop
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _get_artist_albums_sync, sp, artist_id, include_groups, on_rate_limit)


def _get_artist_albums_sync(sp, artist_id, include_groups='album,single', on_rate_limit=None, attempt=1):
    try:
        return sp.artist_albums(artist_id, include_groups=include_groups, limit=5)['items']
    except SpotifyException as e:
        if e.http_status == 429:
            if attempt > 3:
                err_msg = f"Persistent Rate Limit reached for artist {artist_id} after 3 attempts."
                log_message(err_msg)
                raise Exception(err_msg)

            retry_after = int(e.headers.get('Retry-After', 5)) # Default to 5s if missing
            if retry_after < 5: retry_after = 5 # Force minimum 5s backoff
            
            # Simple exponential backoff for persistent undefined limits
            if retry_after == 5:
                retry_after = 5 * attempt 

            log_message(f"Rate Limit: sleeping {retry_after}s (attempt {attempt})")
            
            if on_rate_limit:
                on_rate_limit(retry_after)
                
            time.sleep(retry_after) 
            return _get_artist_albums_sync(sp, artist_id, include_groups, on_rate_limit, attempt + 1)
        return []
    except Exception as e:
        log_message(f"Error fetching albums for {artist_id}: {e}")
        return []

async def get_tracks_for_albums_in_batch(sp, album_ids, on_rate_limit=None):
    all_tracks = {}
    batch_size = 20
    idx = 0
    
    loop = asyncio.get_event_loop()
    
    while idx < len(album_ids):
        chunk = album_ids[idx:idx + batch_size]
        try:
            # Run the network call in a thread
            albums_data = await loop.run_in_executor(None, sp.albums, chunk)
            
            for album in albums_data['albums']:
                if album and 'id' in album:
                    aid = album['id']
                    if 'tracks' in album and album['tracks']['items']:
# ... (rest of logic same) ...
                        # INJECT ALBUM METADATA into tracks
                        full_tracks = []
                        for t in album['tracks']['items']:
                            t['album'] = {
                                'id': album['id'],
                                'name': album['name'],
                                'images': album['images'],
                                'release_date': album['release_date']
                            }
                            full_tracks.append(t)
                        all_tracks[aid] = full_tracks
                    else:
                        all_tracks[aid] = []
        except SpotifyException as e:
            if e.http_status == 429:
                retry_after = int(e.headers.get('Retry-After', 5))
                if retry_after < 5: retry_after = 5
                
                log_message(f"Rate limited (Albums Batch). Wait {retry_after}s")
                await asyncio.sleep(retry_after) # Non-blocking sleep for the loop
            else:
                log_message(f"SpotifyException: {e}")
        except Exception as e:
            log_message(f"Error in batch fetch: {e}")
            
        idx += batch_size
        await asyncio.sleep(0.1) # Yield control
        
    return all_tracks

async def get_artist_new_release_ids(sp, artist, exclusion_artists, start_date, end_date, include_groups='album,single', on_rate_limit=None):
    """
    Step 1: Get Albums for artist and filter by date.
    Returns: (list_of_album_ids, processed_count: 1)
    """
    if artist['id'] in exclusion_artists:
        return ([], 1)

    # 1. Get Albums
    albums = await get_artist_albums_async(sp, artist['id'], include_groups, on_rate_limit)
    
    new_release_ids = []
    for album in albums:
        try:
            r_date_str = album['release_date']
            # Handle YYYY vs YYYY-MM-DD
            if len(r_date_str) == 4:
                r_date = datetime.datetime.strptime(r_date_str, '%Y').date()
            elif len(r_date_str) == 7: 
                 r_date = datetime.datetime.strptime(r_date_str, '%Y-%m').date()
            else:
                r_date = datetime.datetime.strptime(r_date_str, '%Y-%m-%d').date()
                
            if start_date <= r_date <= end_date:
                new_release_ids.append(album['id'])
        except ValueError:
            continue
            
    return (new_release_ids, 1)

async def process_albums_batch_with_filter(sp, album_ids, no_filter_artists, on_rate_limit=None):
    """
    Step 2: Batch fetch full album details (tracks) and apply filters.
    """
    if not album_ids:
        return ([], [])

    # Fetch tracks for all these albums in one go (chunked internally if needed, but we pass 20 usually)
    # The existing get_tracks_for_albums_in_batch handles internal chunking of 20
    batched_tracks_map = await get_tracks_for_albums_in_batch(sp, album_ids, on_rate_limit)
    
    filtered_total = []
    excluded_total = []
    
    for aid, tracks in batched_tracks_map.items():
        if tracks:
            # We need to filter. 
            # Note: tracks returned by get_tracks... already have album metadata injected.
            f, e = filter_tracks(tracks, no_filter_artists)
            filtered_total.extend(f)
            excluded_total.extend(e)
            
    return (filtered_total, excluded_total)

async def scan_new_releases(sp, artists, start_date, end_date, exclusion_path=DEFAULT_EXCLUSION_FILE, no_filter_path=DEFAULT_NO_FILTER_FILE):
    """
    Main entry point for scanning.
    """
    exclusion_artists = load_ids_from_file(exclusion_path)
    no_filter_artists = load_ids_from_file(no_filter_path)
    
    all_kept = []
    all_excluded = []
    
    # Process concurrently with a semaphore to avoid rate limits?
    # Actually, Spotify handles parallel reqs okay, but we should limit concurrency.
    # The original script used ThreadPoolExecutor(max_workers=5).
    # We will use asyncio.gather with a semaphore.
    
    semaphore = asyncio.Semaphore(5)
    
    async def sem_task(artist):
        async with semaphore:
            return await process_artist(sp, artist, exclusion_artists, no_filter_artists, start_date, end_date)
            
    tasks = [sem_task(a) for a in artists]
    results = await asyncio.gather(*tasks)
    
    for kept, excluded, _ in results:
        all_kept.extend(kept)
        all_excluded.extend(excluded)
       
    # Remove duplicates (by URI)
    # Since we are storing dicts now, we need to be careful.
    # We'll use a dict keyed by URI to dedup.
    
    kept_dict = {t['uri']: t for t in all_kept}
    excluded_dict = {t['uri']: t for t in all_excluded}
        
    return {
        "kept_tracks": list(kept_dict.values()),
        "excluded_tracks": list(excluded_dict.values()),
        "stats": {
            "processed_artists": len(artists),
            "kept_count": len(kept_dict),
            "excluded_count": len(excluded_dict)
        }
    }
