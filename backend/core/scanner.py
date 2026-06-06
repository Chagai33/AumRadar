
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
            self.state = loaded
            # Always reset transient fields on boot — never carry over a stale error
            self.state["is_running"] = False
            self.state["status"] = "idle"
            self.state.pop("error", None)
            self.state.pop("rate_limit_until", None)

    def _save_state(self):
        self.state["heartbeat"] = time.time()
        storage.save_json(SCAN_STATE_FILE, self.state)

    def log(self, msg):
        print(msg) 
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.state['logs'].append(f"[{timestamp}] {msg}")
        if len(self.state['logs']) > 50:
             self.state['logs'].pop(0)

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

    async def scan_process(self, sp, settings, app_sp=None, auto_export_name=None):
        # Use App Client for heavy lifting if provided, else fallback to User Client
        work_sp = app_sp if app_sp else sp
        
        self.state["is_running"] = True
        self.state["status"] = "initializing"
        self.state["progress"] = 0
        self.state["results_count"] = 0
        self.state["logs"] = []
        self.state.pop("error", None)          # Clear any previous error
        self.state.pop("rate_limit_until", None)
        self.state.pop("blocked_until", None)
        self._save_state()
        
        try:
            # 1. Gather Artists
            refresh_artists = settings.get('refresh_artists', True)
            include_followed = settings.get('include_followed', True)
            include_liked = settings.get('include_liked_songs', False)
            min_liked = settings.get('min_liked_songs', 1)
            
            # Exclude logic
            exclude_raw = settings.get('exclude_artists', [])
            exclude_ids = set()
            exclude_names = set()
            for ex in exclude_raw:
                if len(ex) == 22 and " " not in ex: # Simple ID check
                     exclude_ids.add(ex)
                else:
                     exclude_names.add(ex.lower().strip())

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
            
            all_artists = list(unique_map.values())

            # Filter Excluded Artists
            artists = []
            for a in all_artists:
                if a['id'] in exclude_ids: continue
                if a['name'].lower().strip() in exclude_names: continue
                artists.append(a)
            
            self.state["total"] = len(artists)
            self.state["status"] = "scanning"
            self._save_state()
            
            concurrency_limit = 5
            
            start_date_str = settings.get('start_date')
            end_date_str = settings.get('end_date')
            start_date = datetime.datetime.strptime(start_date_str, '%Y-%m-%d').date()
            end_date = datetime.datetime.strptime(end_date_str, '%Y-%m-%d').date()
            
            # Album Types (include_groups)
            album_types = settings.get('album_types', ['album', 'single'])
            include_groups_str = ",".join(album_types)
            
            # Filter Config
            filter_config = {
                "min_duration_ms": settings.get('min_duration_sec', 90) * 1000,
                "max_duration_ms": settings.get('max_duration_sec', 270) * 1000,
                "forbidden_keywords": settings.get('forbidden_keywords', []),
                "include_groups": include_groups_str
            }

            from .engine import process_artist
            
            results_buffer = []
            loop = asyncio.get_event_loop()
            
            # THREAD POOL for Synchronous Engine (Matches legacy script max_workers=5)
            executor = ThreadPoolExecutor(max_workers=5)
            
            chunk_size = 20
            self.log(f"DEBUG: Starting scan loop for {len(artists)} artists")
            
            critical_error = False
            critical_seconds = -1

            for i in range(0, len(artists), chunk_size):
                if not self.state["is_running"]: break

                chunk = artists[i:i + chunk_size]
                self.state["current_artist"] = f"Processing batch {i}-{i+len(chunk)}"

                tasks = []
                for artist in chunk:
                    # Run sync function in thread
                    task = loop.run_in_executor(
                        executor,
                        process_artist,
                        work_sp,          # App Token (or User Token)
                        artist,
                        [],               # exclusion_artists handled above
                        [],               # no_filter_artists
                        start_date,
                        end_date,
                        filter_config
                    )
                    tasks.append(task)

                # Wait for batch
                batch_results = await asyncio.gather(*tasks, return_exceptions=True)

                for res in batch_results:
                    if isinstance(res, Exception):
                        err_msg = str(res)
                        print(f"Batch Error: {err_msg}")

                        if "CRITICAL_RATE_LIMIT" in err_msg:
                            self.log(f"⛔ CRITICAL ERROR: {err_msg}")
                            try:
                                critical_seconds = int(err_msg.split("CRITICAL_RATE_LIMIT:")[1].split()[0])
                            except Exception:
                                critical_seconds = -1
                            critical_error = True
                            break
                        continue

                    if not res: continue

                    kept, excluded = res
                    if kept:
                        results_buffer.extend(kept)

                # Stop everything on a hard rate-limit — set the error LAST so nothing overwrites it
                if critical_error:
                    msg, blocked_until = self._format_rate_limit_msg(critical_seconds)
                    self.state["is_running"] = False
                    self.state["status"] = "error"
                    self.state["error"] = msg
                    if blocked_until:
                        self.state["blocked_until"] = blocked_until
                    self.state["results_count"] = len(results_buffer)
                    self._save_state()
                    break

                self.state["progress"] += len(chunk)
                self.state["results_count"] = len(results_buffer)
                self._save_state()

                # Small breathe
                await asyncio.sleep(0.5)

            # If we aborted on a critical error, skip finalize/auto-export entirely
            if critical_error:
                self.log("Scan aborted due to Spotify rate limit.")
                return

            # Finalize
            self.log(f"DEBUG: Loop finished. Saving {len(results_buffer)} results.")
            storage.save_json(RESULTS_FILE, results_buffer)
            
            # Auto Export Logic
            if auto_export_name and results_buffer:
                self.log(f"Starting Auto-Export to playlist '{auto_export_name}'...")
                try:
                    export_tracks = results_buffer

                    # Album exclusion: remove tracks from albums with 4+ tracks (same artist + album)
                    if settings.get('exclude_albums', False):
                        from collections import defaultdict
                        groups = defaultdict(list)
                        for t in results_buffer:
                            album_name  = (t.get('album') or {}).get('name', '')
                            artist_name = ((t.get('artists') or [{}])[0]).get('name', '')
                            groups[f"{artist_name}::{album_name}"].append(t)
                        album_keys = {k for k, v in groups.items() if len(v) >= 4}
                        export_tracks = [
                            t for t in results_buffer
                            if f"{((t.get('artists') or [{}])[0]).get('name','')}::{(t.get('album') or {}).get('name','')}" not in album_keys
                        ]
                        self.log(f"Album exclusion: removed {len(results_buffer) - len(export_tracks)} tracks from {len(album_keys)} albums")

                    if export_tracks:
                        # auto_export_name already contains the date range (built in scan.py)
                        user_id = sp.current_user()['id']
                        pl = sp.user_playlist_create(user_id, auto_export_name, public=False)
                        uris = [t['uri'] for t in export_tracks]
                        for j in range(0, len(uris), 100):
                            sp.playlist_add_items(pl['id'], uris[j:j+100])
                        self.log(f"SUCCESS: Auto-exported {len(export_tracks)} tracks to '{auto_export_name}'")
                    else:
                        self.log("No tracks to export after album exclusion.")
                except Exception as exp:
                    self.log(f"ERROR: Auto-export failed: {exp}")
            
            self.state["results_count"] = len(results_buffer)
            self.state["status"] = "completed"
            
        except Exception as e:
            self.state["status"] = "error"
            self.state["error"] = str(e)
            self.log(f"CRITICAL SCAN ERROR: {e}")
            import traceback
            traceback.print_exc()
        finally:
            self.log("DEBUG: scan_process cleanup (finally block).")
            self.state["is_running"] = False
            self._save_state()

    def _format_rate_limit_msg(self, seconds):
        """Build a clear, transparent rate-limit message + absolute unblock time (epoch).
        seconds < 0 means Spotify didn't tell us how long — fall back to a generic note."""
        if not seconds or seconds < 0:
            return ("Spotify rate limit reached — too many requests. "
                    "Please try again in a few hours.", 0)

        blocked_until = time.time() + seconds
        h = seconds // 3600
        m = (seconds % 3600) // 60
        if h > 0:
            dur = f"about {h}h {m}m" if m else f"about {h} hours"
        elif m > 0:
            dur = f"about {m} minutes"
        else:
            dur = f"{seconds} seconds"
        return (f"Spotify rate limit reached. Try again in {dur}.", blocked_until)

    def get_status(self):
        # Read from GCS (shared source of truth) so polling works even when Cloud Run
        # serves the request from a different instance than the one running the scan.
        persisted = storage.load_json(SCAN_STATE_FILE)
        current_state = persisted if persisted else self.state.copy()

        # Staleness check: if a scan claims to be running but hasn't sent a heartbeat
        # in 2+ minutes, the instance running it died (e.g. Cloud Run scaled it down or
        # a deploy replaced it). Report it as a clear error instead of a stuck "scanning".
        if current_state.get("is_running"):
            heartbeat = current_state.get("heartbeat", 0)
            if heartbeat and (time.time() - heartbeat > 120):
                current_state["is_running"] = False
                current_state["status"] = "error"
                current_state["error"] = "Scan was interrupted (server restarted). Please try again."

        # Keep the rate-limit message accurate over time: recompute the remaining
        # wait live from the stored absolute unblock time.
        blocked_until = current_state.get("blocked_until", 0)
        if current_state.get("status") == "error" and blocked_until:
            remaining = int(blocked_until - time.time())
            if remaining > 0:
                msg, _ = self._format_rate_limit_msg(remaining)
                current_state["error"] = msg

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
