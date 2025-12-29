
import os
import json
import time
import datetime
import logging
import asyncio
from concurrent.futures import ThreadPoolExecutor
from .storage_manager import storage

# Constants
CACHE_DIR = "cache"
SCAN_STATE_FILE = f"{CACHE_DIR}/scan_state.json"
RESULTS_FILE = f"{CACHE_DIR}/scan_results.json"
ARTISTS_CACHE_FILE = f"{CACHE_DIR}/artists_cache.json"

class AdvancedEngine:
    def __init__(self):
        self.state = {
            "is_running": False,
            "status": "idle",
            "progress": 0,
            "total": 0,
            "current_artist": "",
            "logs": [],
            "results_count": 0
        }
        self._load_state()

    def _load_state(self):
        loaded = storage.load_json(SCAN_STATE_FILE)
        if loaded:
            # We don't necessarily want to carry over 'is_running' as True on restart
            # But for resuming maybe?
            # Let's trust the loaded state but force is_running false on init
            self.state = loaded
            self.state["is_running"] = False # Reset on boot

    def _save_state(self):
        storage.save_json(SCAN_STATE_FILE, self.state)

    def log(self, msg):
        print(msg) 
        # Optional: Append to logs list in state, but keep it small
        # self.state['logs'].append(msg)

    def get_artists_cache_info(self):
        if storage.exists(ARTISTS_CACHE_FILE):
             try:
                 metadata = storage.get_metadata(ARTISTS_CACHE_FILE)
                 artists = storage.load_json(ARTISTS_CACHE_FILE, [])
                 return {
                     "exists": True, 
                     "count": len(artists), 
                     "last_updated": metadata.get("last_updated")
                 }
             except:
                 pass
        return {"exists": False, "count": 0, "last_updated": None}

    def _save_artists_cache(self, artists):
        storage.save_json(ARTISTS_CACHE_FILE, artists)

    def _load_artists_cache(self):
        return storage.load_json(ARTISTS_CACHE_FILE, [])

    async def fetch_all_followed_artists(self, sp):
        artists = []
        last_artist_id = None
        
        while True:
            try:
                loop = asyncio.get_event_loop()
                results = await loop.run_in_executor(None, lambda: sp.current_user_followed_artists(limit=50, after=last_artist_id))
                
                chunk = results['artists']['items']
                if not chunk: break
                    
                artists.extend(chunk)
                last_artist_id = chunk[-1]['id']
                
                self.log(f"Fetched {len(artists)} artists so far...")
                self.state["current_artist"] = f"Loading Artist List ({len(artists)} found)..."
                self._save_state()
                
                if len(chunk) < 50:
                    break
            except Exception as e:
                self.log(f"Error fetching artists: {e}")
                break
        
        if artists:
            self._save_artists_cache(artists)
            
        return artists



    async def fetch_liked_songs_artists(self, sp, min_count=1):
        artist_counts = {}
        offset = 0
        limit = 50
        
        while True:
            try:
                loop = asyncio.get_event_loop()
                results = await loop.run_in_executor(None, lambda: sp.current_user_saved_tracks(limit=limit, offset=offset))
                items = results['items']
                
                if not items:
                    break
                    
                for item in items:
                    track = item['track']
                    if not track: continue
                    for artist in track['artists']:
                        aid = artist['id']
                        if aid not in artist_counts:
                            artist_counts[aid] = {'count': 0, 'artist': artist}
                        artist_counts[aid]['count'] += 1
                
                offset += limit
                self.log(f"Scanned {offset} liked songs...")
                self.state["current_artist"] = f"Scanning Liked Songs ({len(artist_counts)} artists found)..."
                
                # Safety break for huge libraries (optional, but good practice)
                if offset > 10000: 
                    break
                    
                if len(items) < limit:
                    break
                    
            except Exception as e:
                self.log(f"Error fetching liked songs: {e}")
                break
                
        # Filter by min_count
        filtered_artists = []
        for data in artist_counts.values():
            if data['count'] >= min_count:
                filtered_artists.append(data['artist'])
                
        return filtered_artists

    async def scan_process(self, sp, settings, app_sp=None):
        # Use App Client for heavy lifting if provided, else fallback to User Client
        work_sp = app_sp if app_sp else sp
        
        self.state["is_running"] = True
        self.state["status"] = "initializing"
        self._save_state()
        
        try:
            # 1. Gather Artists
            artists = []
            refresh_artists = settings.get('refresh_artists', True)
            include_followed = settings.get('include_followed', True)
            include_liked = settings.get('include_liked_songs', False)
            min_liked = settings.get('min_liked_songs', 1)
            
            # Use cache ONLY if exclusively using Followed Artists and refresh is false
            # If Liked Songs are requested, we probably need to fetch them (or cache them separately, but for now we fetch)
            # Simplification: If include_liked is True, we skip full cache load or we append.
            # Best approach: Load followed from cache if available, then fetch liked if requested.
            
            followed_artists = []
            if include_followed:
                if not refresh_artists and storage.exists(ARTISTS_CACHE_FILE):
                     self.log("Loading followed artists from cache...")
                     followed_artists = self._load_artists_cache()
                if not followed_artists:
                     self.log("Fetching followed artists from Spotify...")
                     self.state["status"] = "fetching_artists" # generic status
                     self._save_state()
                     followed_artists = await self.fetch_all_followed_artists(sp)
                     self._save_artists_cache(followed_artists)

            liked_artists = []
            if include_liked:
                self.log("Fetching artists from Liked Songs...")
                self.state["status"] = "fetching_liked"
                self._save_state()
                liked_artists = await self.fetch_liked_songs_artists(sp, min_liked)
                
            # Merge lists unique by ID
            unique_map = {a['id']: a for a in followed_artists}
            for a in liked_artists:
                unique_map[a['id']] = a
            
            artists = list(unique_map.values())
            
            self.state["total"] = len(artists)
            self.state["status"] = "scanning"
            self._save_state()
            
            concurrency_limit = 5
            semaphore = asyncio.Semaphore(concurrency_limit)
            
            start_date_str = settings.get('start_date')
            end_date_str = settings.get('end_date')
            start_date = datetime.datetime.strptime(start_date_str, '%Y-%m-%d').date()
            end_date = datetime.datetime.strptime(end_date_str, '%Y-%m-%d').date()
            
            # Album Types (include_groups)
            album_types = settings.get('album_types', ['album', 'single'])
            include_groups_str = ",".join(album_types)
            
            results_buffer = []

            # Filter Config
            filter_config = {
                "min_duration_ms": settings.get('min_duration_sec', 90) * 1000,
                "max_duration_ms": settings.get('max_duration_sec', 270) * 1000,
                "forbidden_keywords": settings.get('forbidden_keywords', [])
            }

            # Define rate limit callback
            def on_rate_limit(seconds):
                if seconds > 300: # If wait is more than 5 minutes
                    self.state["status"] = "error"
                    self.state["error"] = f"Spotify blocked requests for {seconds} seconds (approx {round(seconds/3600, 1)} hours). Try again later."
                    self.state["is_running"] = False
                    self._save_state()
                    # We need to stop execution. Raising an exception is the easiest way to break the loop.
                    raise Exception(f"Rate limit too long: {seconds}s")
                
                self.state["rate_limit_until"] = time.time() + seconds + 1
                self.state["retry_after"] = seconds
                self.log(f"Rate Limit Hit! Pausing for {seconds}s")
                self._save_state()

            async def process_one(artist):
                if not self.state["is_running"]: return
                
                async with semaphore:
                    # Update progress UI less frequently to save IO
                    self.state["current_artist"] = artist['name']

                    from .engine import get_artist_new_release_ids
                    
                    try:
                        # Step 1: Just get IDs
                        new_ids, _ = await get_artist_new_release_ids(work_sp, artist, [], start_date, end_date, include_groups=include_groups_str, on_rate_limit=on_rate_limit)
                        return new_ids
                    except Exception as e:
                        str_e = str(e)
                        if "Rate limit too long" in str_e or "Persistent Rate Limit" in str_e:
                            raise e # Propagate crucial error to main loop
                        print(f"Error processing {artist['name']}: {e}")
                        return []
                    
                    self.state["progress"] += 1
                    if self.state["progress"] % 10 == 0:
                         self.log(f"Progress: {self.state['progress']}/{self.state['total']}")
            
            # Processing Loop with Global Batching
            from .engine import process_albums_batch_with_filter
            
            pending_album_ids = []
            chunk_size = 50 
            
            for i in range(0, len(artists), chunk_size):
                if not self.state["is_running"]: break
                
                chunk = artists[i:i + chunk_size]
                
                # 1. Gather Album IDs from this chunk of artists
                tasks = [process_one(a) for a in chunk]
                # We use return_exceptions=True to ensure one failure doesn't crash the batch
                results_ids_list = await asyncio.gather(*tasks, return_exceptions=True)
                
                for res in results_ids_list:
                    if isinstance(res, Exception):
                        str_e = str(res)
                        if "Rate limit too long" in str_e or "Persistent Rate Limit" in str_e:
                            raise res # This will trigger the outer exception handler and stop the scan
                    
                    if res and isinstance(res, list):
                        pending_album_ids.extend(res)
                        
                # self.state["progress"] = min(i + chunk_size, len(artists)) # REMOVED
                self.state["current_artist"] = "Batch Processing..." # update status for filtering phase
                
                # 2. Process pending albums if we have enough
                while len(pending_album_ids) >= 20:
                    batch = pending_album_ids[:20]
                    pending_album_ids = pending_album_ids[20:]
                    
                    kept, excluded = await process_albums_batch_with_filter(work_sp, batch, [], filter_options=filter_config, on_rate_limit=on_rate_limit)
                    if kept:
                        results_buffer.extend(kept)
                
                # Checkpoint results
                self.state["results_count"] = len(results_buffer)
                self._save_state()
            
            # 3. Process remaining albums
            if pending_album_ids:
                 kept, excluded = await process_albums_batch_with_filter(work_sp, pending_album_ids, [], filter_options=filter_config, on_rate_limit=on_rate_limit)
                 if kept:
                    results_buffer.extend(kept)
                
            # Finalize
            self.state["status"] = "completed"
            self.state["is_running"] = False
            
            # Save final results to disk
            storage.save_json(RESULTS_FILE, results_buffer)
                
        except Exception as e:
            self.state["status"] = "error"
            self.state["error"] = str(e)
            self.state["is_running"] = False
            self.log(f"CRITICAL SCAN ERROR: {e}")
            
        self._save_state()

    def get_status(self):
        # Dynamic status check
        current_state = self.state.copy()
        rate_limit_until = current_state.get("rate_limit_until", 0)
        
        if time.time() < rate_limit_until:
            current_state["status"] = "rate_limited"
            current_state["retry_after"] = int(rate_limit_until - time.time())
            
        return current_state
    
    def get_results(self):
        return storage.load_json(RESULTS_FILE, [])
    
    def stop_scan(self):
        self.state["is_running"] = False
        self.state["status"] = "stopping"

scanner = AdvancedEngine()
