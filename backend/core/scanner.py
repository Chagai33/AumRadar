
import os
import json
import time
import datetime
import logging
import asyncio
from concurrent.futures import ThreadPoolExecutor
from .storage_manager import storage
from .engine import safe_api_call, ScanInterruptedException, reset_pacing

# Constants
CACHE_DIR = "cache"
SCAN_STATE_FILE = f"{CACHE_DIR}/scan_state.json"
RESULTS_FILE = f"{CACHE_DIR}/scan_results.json"
ARTISTS_CACHE_FILE = f"{CACHE_DIR}/artists_cache.json"
HISTORY_DIR = f"{CACHE_DIR}/scan_history"
HISTORY_INDEX_FILE = f"{HISTORY_DIR}/index.json"
MAX_HISTORY = 50

# Resilience: the frozen artist list for the in-flight scan (written ONCE) and the
# dynamic per-chunk progress (results + position). Split so we never re-upload the
# ~3MB artist list on every chunk — only the ~2MB-max results ride in the checkpoint.
SNAPSHOT_FILE = f"{CACHE_DIR}/scan_artists_snapshot.json"
CHECKPOINT_FILE = f"{CACHE_DIR}/scan_checkpoint.json"
AUTO_RESUME_FILE = f"{CACHE_DIR}/auto_resume.json"

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

    async def _heartbeat_loop(self, interval=20):
        """Keep SCAN_STATE_FILE's heartbeat fresh on a wall-clock cadence, NOT just
        once per chunk. Without this a long rate-limit block (a chunk can exceed the
        120s liveness window) would let the liveness checks — get_status staleness,
        the /start 409 gate, and the Job's _another_instance_alive lock — wrongly
        declare a still-running scan dead and allow a duplicate/second scan. The
        chunk loop awaits inside asyncio.gather, so this task is scheduled on time."""
        try:
            while self.state.get("is_running"):
                self.state["heartbeat"] = time.time()
                storage.save_json(SCAN_STATE_FILE, self.state)
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass

    def _cancel_heartbeat(self):
        task = getattr(self, "_hb_task", None)
        if task and not task.done():
            task.cancel()
        self._hb_task = None

    def _save_checkpoint(self, next_index, total, results_buffer, settings,
                         auto_export_name, status, blocked_until=0):
        """Persist the DYNAMIC scan progress after each chunk (and on every stop
        path). The frozen artist list lives separately in SNAPSHOT_FILE (written
        once), so this write stays ~2MB max and never re-uploads the ~3MB artists.

        next_index = the artist index to RESUME from. On a mid-chunk stop we pass
        the chunk's own start index i (not i+chunk_size) so the interrupted chunk
        re-runs on resume; the uri-dedup (Phase 2) removes any overlap.
        status: in_progress | blocked_resumable | interrupted_error.
        NOTE: settings must already carry resolved (non-'DYNAMIC') start/end dates
        so a resume runs the exact same date range."""
        storage.save_json(CHECKPOINT_FILE, {
            "status": status,
            "next_index": next_index,
            "total": total,
            "results": results_buffer,
            "settings": settings,
            "auto_export_name": auto_export_name,
            "blocked_until": blocked_until,
            "heartbeat": time.time(),
        })

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

        # Any failure here must abort the scan loudly. Silently proceeding with a
        # partial artist list (and caching it) poisons every future scan.
        while True:
            loop = asyncio.get_event_loop()
            results = await loop.run_in_executor(
                None,
                lambda: safe_api_call(sp.current_user_followed_artists, limit=50, after=last_artist_id)
            )

            chunk = results['artists']['items']
            if not chunk: break

            artists.extend(chunk)
            last_artist_id = chunk[-1]['id']

            self.log(f"Fetched {len(artists)} artists so far...")
            self.state["current_artist"] = f"Loading Artist List ({len(artists)} found)..."
            self._save_state()

            if len(chunk) < 50:
                break

        # Only cache a complete list — we only get here if pagination finished.
        if artists:
            self._save_artists_cache(artists)

        return artists



    async def fetch_liked_songs_artists(self, sp, min_count=1):
        artist_counts = {}
        offset = 0
        limit = 50
        
        # Any failure here must abort the scan loudly — a partial artist set from
        # Liked Songs silently changes what gets scanned.
        while True:
            loop = asyncio.get_event_loop()
            results = await loop.run_in_executor(
                None,
                lambda: safe_api_call(sp.current_user_saved_tracks, limit=limit, offset=offset)
            )
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

        # Filter by min_count
        filtered_artists = []
        for data in artist_counts.values():
            if data['count'] >= min_count:
                filtered_artists.append(data['artist'])
                
        return filtered_artists

    async def scan_process(self, sp, settings, app_sp=None, auto_export_name=None):
        # Use App Client for heavy lifting if provided, else fallback to User Client
        work_sp = app_sp if app_sp else sp

        # Clear the process-global pacing state (rate, block window, breather
        # counter, and the _CRITICAL_ABORT latch) so a flag left set by a previous
        # blocked scan can't instantly abort this fresh one.
        reset_pacing()

        self.state["is_running"] = True
        self.state["status"] = "initializing"
        self.state["progress"] = 0
        self.state["results_count"] = 0
        self.state["logs"] = []
        self.state["partial_scan"] = False
        self.state.pop("error", None)
        self.state.pop("rate_limit_until", None)
        self.state.pop("blocked_until", None)
        self._save_state()

        # Fresh scan abandons any prior resumable state ("new scan" semantics): a
        # blocked/interrupted checkpoint from a previous run is discarded here so it
        # can't be mistaken for THIS scan's progress. A resume goes through
        # resume_scan(), never this method.
        storage.delete_file(CHECKPOINT_FILE)
        storage.delete_file(SNAPSHOT_FILE)

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
                     # Saves the cache internally on complete success; raises on failure
                     followed_artists = await self.fetch_all_followed_artists(sp)

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
            
            # Optionally limit to a user-selected subset of artists
            selected_ids = set(settings.get('selected_artist_ids') or [])
            if selected_ids:
                artists = [a for a in artists if a['id'] in selected_ids]
                self.state["partial_scan"] = True

            self.state["total"] = len(artists)
            self.state["status"] = "scanning"
            self._save_state()

            # Freeze the EXACT artist list this scan runs on — written ONCE, never
            # per-chunk. Phase 2's resume reads this to continue from next_index
            # without re-fetching (option א': artists added later wait for the next
            # scan). Full artist objects kept (future-proofing).
            storage.save_json(SNAPSHOT_FILE, artists)
            
            # Fresh scan: start at index 0 with an empty results buffer and an
            # empty dedup set. From here the logic is IDENTICAL to a resume
            # (which passes a loaded buffer + next_index), so both run through
            # ONE shared method (_scan_and_finalize) and can never drift apart.
            await self._scan_and_finalize(
                work_sp, sp, artists, settings, auto_export_name,
                results_buffer=[], seen=set(), start_index=0
            )
            
        except ScanInterruptedException as e:
            # Transient failure exhausted retries BEFORE the scan loop (e.g. while
            # loading the artist list) — nothing scanned yet, so nothing to save.
            self.state["status"] = "interrupted_error"
            self.state["error"] = ("Scan interrupted by a network or server error "
                                   "while loading data. Please try again.")
            self.log(f"SCAN INTERRUPTED (pre-loop): {e}")
        except Exception as e:
            err_msg = str(e)
            self.state["status"] = "error"
            if "CRITICAL_RATE_LIMIT" in err_msg:
                # Hard rate-limit outside the batch loop (e.g. while fetching the
                # artist list) — show the same transparent message as in-scan blocks.
                try:
                    seconds = int(err_msg.split("CRITICAL_RATE_LIMIT:")[1].split()[0])
                except Exception:
                    seconds = -1
                msg, blocked_until = self._format_rate_limit_msg(seconds)
                self.state["error"] = msg
                if blocked_until:
                    self.state["blocked_until"] = blocked_until
            else:
                self.state["error"] = err_msg
            self.log(f"CRITICAL SCAN ERROR: {e}")
            import traceback
            traceback.print_exc()
        finally:
            self._cancel_heartbeat()
            self.log("DEBUG: scan_process cleanup (finally block).")
            self.state["is_running"] = False
            self._save_state()

    async def _scan_and_finalize(self, work_sp, sp, artists, settings,
                                 auto_export_name, results_buffer, seen, start_index):
        """Shared scan body for BOTH a fresh scan_process and a resume_scan. Runs
        the chunk loop from start_index, dedups results by uri, checkpoints after
        every chunk (and on every stop path), and on full completion writes
        RESULTS_FILE + history, clears the checkpoint/snapshot, and runs the
        auto-export. ONE code path, so fresh and resume can never drift apart —
        every Phase 1 safety-net fix applies to resume automatically."""
        # Keep the heartbeat fresh on a timer (not only per-chunk) so a long
        # rate-limit block can't make the liveness checks think the scan died.
        self._hb_task = asyncio.create_task(self._heartbeat_loop())
        start_date_str = settings.get('start_date') or ''
        end_date_str = settings.get('end_date') or ''
        if not start_date_str or not end_date_str:
            raise ValueError("start_date and end_date are required.")
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

        loop = asyncio.get_event_loop()

        # THREAD POOL for Synchronous Engine (Matches legacy script max_workers=5)
        executor = ThreadPoolExecutor(max_workers=5)

        chunk_size = 20
        self.log(f"DEBUG: Scan loop over {len(artists)} artists from index {start_index}")

        critical_error = False
        critical_seconds = -1
        interrupted_error = False

        for i in range(start_index, len(artists), chunk_size):
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
                    if isinstance(res, ScanInterruptedException):
                        # Transient 5xx/network retries exhausted → stop cleanly
                        # and keep everything collected so far (never a silent skip).
                        self.log(f"⚠️ SCAN INTERRUPTED (network/server): {err_msg}")
                        interrupted_error = True
                        break
                    continue

                if not res: continue

                kept, excluded = res
                # Dedup by uri across the WHOLE scan (fresh + resume). The same
                # track can surface from two artists (a collab) or from re-running
                # the interrupted chunk on resume; uri is an exact match, so this
                # never drops a distinct song. A track with no uri (rare/malformed)
                # is kept as-is — we never silently lose it.
                for t in (kept or []):
                    uri = t.get("uri")
                    if not uri:
                        results_buffer.append(t)
                        continue
                    if uri in seen:
                        continue
                    seen.add(uri)
                    results_buffer.append(t)

            # Hard rate-limit → SAVE the partial results to the checkpoint FIRST
            # (this is the fix for the old data-deleting return), then stop.
            # next_index=i so the interrupted chunk re-runs on resume.
            if critical_error:
                msg, blocked_until = self._format_rate_limit_msg(critical_seconds)
                self._save_checkpoint(i, len(artists), results_buffer, settings,
                                      auto_export_name, "blocked_resumable",
                                      blocked_until=(blocked_until or 0))
                self.state["is_running"] = False
                self.state["status"] = "blocked_resumable"
                self.state["error"] = msg
                if blocked_until:
                    self.state["blocked_until"] = blocked_until
                self.state["results_count"] = len(results_buffer)
                self._save_state()
                break

            # Network/server error that exhausted retries → same safety net,
            # different status so the UI can word it differently.
            if interrupted_error:
                self._save_checkpoint(i, len(artists), results_buffer, settings,
                                      auto_export_name, "interrupted_error")
                self.state["is_running"] = False
                self.state["status"] = "interrupted_error"
                self.state["error"] = ("Scan interrupted by a network or server "
                                       "error. Your partial results are saved — "
                                       "you can resume.")
                self.state["results_count"] = len(results_buffer)
                self._save_state()
                break

            # Chunk done → checkpoint the growing buffer so a crash/kill after
            # this point still keeps every track collected so far.
            self.state["progress"] += len(chunk)
            self.state["results_count"] = len(results_buffer)
            self._save_checkpoint(i + chunk_size, len(artists), results_buffer,
                                  settings, auto_export_name, "in_progress")
            self._save_state()

            # Small breathe
            await asyncio.sleep(0.5)

        # If we stopped early (rate-limit or network/server), the partial results
        # are already saved to the checkpoint — skip finalize so we don't mark an
        # interrupted scan "completed" or wipe the checkpoint.
        if critical_error or interrupted_error:
            self.log("Scan stopped early — partial results saved to checkpoint for resume.")
            return

        # Finalize
        self.log(f"DEBUG: Loop finished. Saving {len(results_buffer)} results.")
        storage.save_json(RESULTS_FILE, results_buffer)
        self._save_to_history(results_buffer, settings)
        # Completed successfully → drop the resumable checkpoint + frozen snapshot
        # so the interrupted-detection never offers to "resume" a finished scan.
        storage.delete_file(CHECKPOINT_FILE)
        storage.delete_file(SNAPSHOT_FILE)

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

    async def resume_scan(self, sp, app_sp=None):
        """Resume a blocked/interrupted scan from its checkpoint, on the FROZEN
        artist snapshot (option א'). Requires BOTH files — if either is missing
        there is nothing to resume. uri-dedup means the re-run of the interrupted
        chunk contributes no duplicates."""
        work_sp = app_sp if app_sp else sp
        reset_pacing()   # clear the _CRITICAL_ABORT latch (etc.) before resuming

        cp = storage.load_json(CHECKPOINT_FILE)
        artists = storage.load_json(SNAPSHOT_FILE)
        if not cp or not artists:
            self.log("Resume requested but no checkpoint/snapshot found — nothing to resume.")
            return

        results_buffer = cp.get("results") or []
        seen = {t["uri"] for t in results_buffer if t.get("uri")}
        settings = cp.get("settings") or {}
        auto_export_name = cp.get("auto_export_name")
        start_index = cp.get("next_index") or 0
        total = cp.get("total") or len(artists)

        self.state["is_running"] = True
        self.state["status"] = "scanning"
        self.state["progress"] = start_index
        self.state["total"] = total
        self.state["results_count"] = len(results_buffer)
        self.state["current_artist"] = f"Resuming from {start_index}/{total}"
        self.state["logs"] = []
        self.state["partial_scan"] = bool(settings.get('selected_artist_ids'))
        self.state.pop("error", None)
        self.state.pop("rate_limit_until", None)
        self.state.pop("blocked_until", None)
        self._save_state()
        self.log(f"Resuming scan from artist {start_index}/{total} with {len(results_buffer)} tracks kept.")

        try:
            await self._scan_and_finalize(
                work_sp, sp, artists, settings, auto_export_name,
                results_buffer=results_buffer, seen=seen, start_index=start_index
            )
        except ScanInterruptedException as e:
            self.state["status"] = "interrupted_error"
            self.state["error"] = ("Scan interrupted by a network or server error. "
                                   "Please try again.")
            self.log(f"SCAN INTERRUPTED (resume): {e}")
        except Exception as e:
            err_msg = str(e)
            self.state["status"] = "error"
            if "CRITICAL_RATE_LIMIT" in err_msg:
                try:
                    seconds = int(err_msg.split("CRITICAL_RATE_LIMIT:")[1].split()[0])
                except Exception:
                    seconds = -1
                msg, blocked_until = self._format_rate_limit_msg(seconds)
                self.state["error"] = msg
                if blocked_until:
                    self.state["blocked_until"] = blocked_until
            else:
                self.state["error"] = err_msg
            self.log(f"CRITICAL RESUME ERROR: {e}")
            import traceback
            traceback.print_exc()
        finally:
            self._cancel_heartbeat()
            self.log("DEBUG: resume_scan cleanup (finally block).")
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
            else:
                # Timer expired — clear error from GCS and return idle
                self.state["status"] = "idle"
                self.state.pop("error", None)
                self.state.pop("blocked_until", None)
                self._save_state()
                current_state["status"] = "idle"
                current_state.pop("error", None)

        rate_limit_until = current_state.get("rate_limit_until", 0)
        if time.time() < rate_limit_until:
            current_state["status"] = "rate_limited"
            current_state["retry_after"] = int(rate_limit_until - time.time())

        return current_state
    
    def get_results(self):
        # When a scan is paused (blocked/interrupted), RESULTS_FILE hasn't been
        # written yet — surface the partial results straight from the checkpoint so
        # the user can view / export what was collected before the stop.
        cp = storage.load_json(CHECKPOINT_FILE)
        if cp and cp.get("status") in ("blocked_resumable", "interrupted_error"):
            return cp.get("results") or []
        return storage.load_json(RESULTS_FILE, [])

    def get_auto_resume(self):
        """User toggle: should a blocked/interrupted scan resume automatically (via
        the resume_due poller) once the rate-limit block clears? Default ON."""
        return storage.load_json(AUTO_RESUME_FILE, {"enabled": True})

    def set_auto_resume(self, enabled):
        storage.save_json(AUTO_RESUME_FILE, {"enabled": bool(enabled)})
        return {"enabled": bool(enabled)}

    def get_checkpoint_info(self):
        """Summary of a resumable checkpoint for the frontend banner. Also flags a
        'silently died' scan: a checkpoint still marked in_progress whose heartbeat
        is stale (the instance running it went away, e.g. a closed tab before the
        Job model). Returns {"exists": False} when there is nothing to resume."""
        cp = storage.load_json(CHECKPOINT_FILE)
        if not cp:
            return {"exists": False}

        status = cp.get("status")
        # Liveness comes from SCAN_STATE_FILE (kept fresh every ~20s by the
        # heartbeat loop), NOT the checkpoint's own per-chunk heartbeat — otherwise
        # a long chunk would make a live scan look dead and offer a bogus resume.
        st = storage.load_json(SCAN_STATE_FILE) or {}
        st_hb = st.get("heartbeat", 0)
        live = bool(st.get("is_running")) and bool(st_hb) and (time.time() - st_hb < 120)
        stale = (status == "in_progress") and not live

        # Resumable if the scan explicitly stopped (blocked/interrupted) OR it
        # claims in_progress but no live scan is actually running it.
        resumable = status in ("blocked_resumable", "interrupted_error") or stale
        # resume_scan needs the snapshot too; without it there's nothing to resume.
        if resumable and not storage.exists(SNAPSHOT_FILE):
            resumable = False

        if status == "blocked_resumable":
            reason = "rate_limited"
        elif status == "interrupted_error":
            reason = "network_error"
        elif status == "in_progress" and stale:
            reason = "interrupted"
        else:
            reason = status  # live in_progress (not resumable)

        blocked_until = cp.get("blocked_until") or 0
        info = {
            "exists": True,
            "resumable": resumable,
            "status": status,
            "reason": reason,
            "next_index": cp.get("next_index") or 0,
            "total": cp.get("total") or 0,
            "results_count": len(cp.get("results") or []),
            "blocked_until": blocked_until,
        }
        if blocked_until:
            info["blocked_remaining"] = max(0, int(blocked_until - time.time()))
        return info

    # ── History ────────────────────────────────────────────────────────────
    def _save_to_history(self, tracks: list, settings: dict):
        """Append this scan to the rolling history index (max MAX_HISTORY entries)."""
        try:
            scan_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            storage.save_json(f"{HISTORY_DIR}/{scan_id}.json", tracks)

            index = storage.load_json(HISTORY_INDEX_FILE, [])
            selected_ids = settings.get('selected_artist_ids') or []
            completed = self.state.get("progress", 0) >= self.state.get("total", 1)

            index.insert(0, {
                "id": scan_id,
                "created_at": datetime.datetime.now().isoformat(),
                "start_date": settings.get('start_date', ''),
                "end_date": settings.get('end_date', ''),
                "track_count": len(tracks),
                "partial_scan": bool(selected_ids),
                "selected_artist_count": len(selected_ids) if selected_ids else None,
                "album_types": settings.get('album_types', []),
                "completed": completed,
            })

            # Rotate: drop oldest entries beyond the limit
            for old in index[MAX_HISTORY:]:
                storage.delete_file(f"{HISTORY_DIR}/{old['id']}.json")
            index = index[:MAX_HISTORY]

            storage.save_json(HISTORY_INDEX_FILE, index)
            self.log(f"Saved to history ({scan_id}, {len(tracks)} tracks)")
        except Exception as e:
            self.log(f"Warning: could not save to history: {e}")

    def get_history_index(self):
        return storage.load_json(HISTORY_INDEX_FILE, [])

    def get_history_scan(self, scan_id: str):
        return storage.load_json(f"{HISTORY_DIR}/{scan_id}.json")

    def delete_history_entry(self, scan_id: str):
        index = storage.load_json(HISTORY_INDEX_FILE, [])
        index = [e for e in index if e["id"] != scan_id]
        storage.save_json(HISTORY_INDEX_FILE, index)
        storage.delete_file(f"{HISTORY_DIR}/{scan_id}.json")
    
    def dismiss_error(self):
        self.state["is_running"] = False
        self.state["status"] = "idle"
        self.state.pop("error", None)
        self.state.pop("blocked_until", None)
        self._save_state()

    def stop_scan(self):
        self.state["is_running"] = False
        self.state["status"] = "stopping"

scanner = AdvancedEngine()
