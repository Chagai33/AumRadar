# Why the weekly scan of 2026-10-09 did not finish

All facts below are MEASURED from Cloud Run logs (project `aummusic`, job `aumradar-scan`) and from `gs://aumradar/cache/`.

## 1. The scheduled weekly scan (Fri 2026-10-09, 05:00 UTC / 08:00 IDT) — DIED
Measured, job log `aumradar-scan`:
```
05:00:44  [scan_job] starting, SCAN_MODE=scheduled
05:00:45  [scan_job] scheduled fresh scan 2026-10-03..2026-10-09 -> auto-export 'Weekly Radar 2026-10-03 – 2026-10-09'
05:00:45  Fetching followed artists from Spotify...
05:00:51..05:01:00  Fetched 50 / 100 / 150 / 200 / 250 artists so far...
05:01:05  ⚠️ network error — transient, retry 1/3 in 2.1s
05:01:13  ⚠️ network error — transient, retry 2/3 in 4.4s
05:01:22  ⚠️ network error — transient, retry 3/3 in 8.2s
05:01:36  SCAN INTERRUPTED (pre-loop): Network kept failing after 3 retries:
          HTTPSConnectionPool(host='api.spotify.com', port=443): Read timed out. (read timeout=5)
05:01:36  DEBUG: scan_process cleanup (finally block).
05:01:36  [scan_job] done, exit=0
```
- Failure phase: **pre-loop**, i.e. `fetch_all_followed_artists` paging over `/me/following`. It died after ~250 of ~3,800 artists.
- Cause in the log: **read timeout (5s) on api.spotify.com**, 3 retries exhausted. **NOT** a 429 / rate limit — no 429 or `CRITICAL_RATE_LIMIT` line anywhere on 10-09 or 10-10 (measured by grep over the full window).
- The retry budget is 3 attempts with 2.1s / 4.4s / 8.2s backoff — total ~31s of tolerance. That is the whole resilience the artist-fetch phase has.
- `maxRetries: 0` on the Cloud Run Job (measured in the execution spec), so the job itself was not retried.

## 2. Nothing retried it
Measured: every `aumradar-auto-resume` run (every 30 min, before and after) logs
`[scan_job] auto-resume is disabled by the user — skipping.` — so the blocked checkpoint was never resumed.
`aumradar-weekly` (Fri 10:00) is paused since 2026-08-06, so there was no second weekly attempt either.

## 3. The 10-10 manual scan DID complete — but it is not the weekly deliverable
Measured, job log + `cache/scan_state.json` + `cache/scan_history/index.json`:
```
2026-10-10 04:59:50  [scan_job] starting, SCAN_MODE=manual
2026-10-10 04:59:51  [scan_job] manual fresh scan 2026-10-03..2026-10-09      <-- no "-> auto-export"
2026-10-10 04:59:52  Loading followed artists from cache...
2026-10-10 04:59:53  DEBUG: Scan loop over 3789 artists from index 0
2026-10-10 05:43:31  DEBUG: Loop finished. Enriching ISRC for 438 results.
2026-10-10 05:43:36  ISRC enrichment: tagged 438/438 tracks.
2026-10-10 05:43:37  Saved to history (20261010_054336, 438 tracks)
2026-10-10 05:43:37  [scan_job] done, exit=0
```
State: `is_running=false, status="completed", progress 3789/3789, results_count 438, partial_scan=false`.
History: `{"id":"20261010_054336","start_date":"2026-10-03","end_date":"2026-10-09","track_count":438,"completed":true}`.

Two measured gaps versus the scheduled run:
- **No auto-export.** `manual` mode logs no `-> auto-export`, and grep over the whole 10-09/10-10 window finds **zero** export/playlist lines. The playlist `Weekly Radar 2026-10-03 – 2026-10-09` was never created by either run.
- **Stale artist list.** `gs://aumradar/cache/artists_cache.json` Creation Time = **2026-10-02T05:02:39Z**, i.e. last week's list. The 10-09 run was the "fresh" one that would have rewritten it, and because the cache is only written after complete pagination, the aborted fetch left the old 3,789-artist list in place. The 10-10 scan scanned that list — any artist followed/unfollowed during the week is not reflected.

## 4. Noise to ignore
- `Error deleting from GCS (cache/scan_checkpoint.json / scan_artists_snapshot.json / scan_stop_request.json): 404` at the start of every fresh scan — expected cleanup of non-existent files.
- The ~hundreds of empty-payload lines at 2026-10-10T06:30:31Z are Cloud Run audit logs garbage-collecting old job executions (`Execution completed for deletion`), not scan activity.
- `[scan_job] another scan is already running (fresh heartbeat) — exiting cleanly.` from the 30-min poller — correct guard behaviour.

## 5. Gaps this exposes (not fixed, recorded only)
1. `fetch_all_followed_artists` has no checkpoint: a timeout at artist 250 throws away the whole phase and the scan is unrecoverable, whereas the per-artist loop has a checkpoint.
2. 5s read timeout + 3 retries / ~31s is thin for a ~76-page pagination against Spotify.
3. The only path that creates the weekly playlist is `SCAN_MODE=scheduled`; a manual rerun after a failed Friday produces the history entry but no playlist, with no warning in the UI.
