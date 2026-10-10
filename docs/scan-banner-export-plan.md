# Plan: scan identity + Spotify export status in the "Scan Completed" banner

Read-only survey first. Every claim below is MEASURED by reading the code.

## What the banner has today
`frontend/src/pages/Dashboard.tsx:866-894` — the completion banner renders exactly two facts:
`scanStatus.status === 'completed'` and `results.length` ("Found 438 new releases."). Nothing else.

## What data exists today
- **No scan name anywhere.** A scan is identified only by `id` (a timestamp, e.g. `20261010_054336`)
  plus `start_date`/`end_date`. `_save_to_history` (`backend/core/scanner.py:832-861`) writes
  `id, created_at, start_date, end_date, track_count, partial_scan, selected_artist_count,
  album_types, completed` — **no name, no export fields**.
- **The auto-export result is thrown away.** `backend/core/scanner.py:601-632` creates the playlist
  (`sp.user_playlist_create` → `pl`), adds tracks, and then only calls
  `self.log("SUCCESS: Auto-exported …")`. `pl['id']` and `pl['external_urls']['spotify']` are never
  persisted — not to `scan_state.json`, not to the history entry. **The link is unrecoverable from
  app data for every past scan.**
- **The manual export result is thrown away too.** `backend/routers/scan.py:274-293` returns
  `playlist_url` to the browser; `Dashboard.tsx:629-637` shows it in a `confirm()` and opens a tab.
  Nothing is stored.
- **Auto-export only runs in scheduled mode.** `backend/scan_job.py:104-106` passes
  `auto_export_name` only for `SCAN_MODE=scheduled`; `manual` passes none (this is why the
  2026-10-10 manual rerun produced no playlist — see `scan-2026-10-09-10-findings.md`).
- A failed auto-export is logged as `ERROR: Auto-export failed: …` (`scanner.py:632`) and then
  `status` is still set to `"completed"` — the UI cannot tell a scan that exported from one
  whose export blew up.

## Planned change (not yet written — awaiting approval)
1. **Backend, one export record.** Add an `export` object written by BOTH export paths:
   `{"status": "success"|"failed"|"none", "playlist_name", "playlist_url", "playlist_id",
     "track_count", "at", "error"}`.
   - `scanner.py` auto-export block: capture `pl` and write the record.
   - `routers/scan.py` `/export`: accept the target `scan_id` (or `"current"`) and write the same record.
   - Persist it in two places: the live `scan_state.json` (so the banner can read it from
     `/api/status`) and the matching `scan_history/index.json` entry (so history rows keep it).
2. **Backend, scan identity.** Add `name` to the history entry and to the state, built from the
   scanned range (the single source already used for playlist naming), so the banner and the history
   list say the same thing.
3. **Frontend, the banner.** Under "Scan Completed" show: scan name + range, release count, and one
   of: "Exported to Spotify: <playlist name>" with a clickable link / "Not exported to Spotify" /
   "Export failed: <reason>" in red.
4. **Frontend, history rows** get the same export indicator (same data, no extra request).

## What was built (approved 1א / 2א / 3א)

### Backend
- `backend/core/scanner.py`
  - `scan_display_name(start, end, partial)` — ONE formula for both the scan's display
    name and the auto-export playlist name, so they can never disagree.
    Output: `Weekly Radar 2026-10-03 – 2026-10-09`, with ` [SEL]` on a partial scan.
  - `export_record(status, source, playlist, track_count, error)` — the single record
    shape: `status` = success | failed | none, `source` = auto | manual | attached,
    plus `playlist_id/name/url`, `track_count`, `at`, `error`.
  - `NO_EXPORT = {"status": "none"}` — "never exported" is explicit, so the UI can tell
    it apart from "tried and failed".
  - `scan_process` / `resume_scan` now stamp `scan_name`, `start_date`, `end_date`,
    `scan_id` (null until the history row exists) and a fresh `export` on the state.
  - `_save_to_history` writes `name` + `export` on the row, sets `state["scan_id"]`,
    flushes the state (so the Service can find the id) and returns the id.
  - `_write_export_to_history(scan_id, record)` — stamps one history row, best-effort.
  - `record_export(record, scan_id)` — writes the record to BOTH the live state (the
    banner) and the history row. Re-reads the state from storage first, because the
    Service and the scan Job are separate processes; patches only the `export` field
    (never swaps the whole state); leaves the heartbeat alone, since an export is not
    a sign of life; and touches the banner only when the record belongs to the scan
    the banner is showing.
  - The auto-export block now keeps `pl` and records success, the album-exclusion
    empty case, and the exception path (previously only a log line; the link was
    thrown away).
- `backend/routers/scan.py`
  - `ExportRequest` gained `scan_id` (omitted = the scan in the banner). `/export`
    records success AND failure.
  - New `POST /api/export/attach` — `{playlist_url, scan_id}`. Accepts a share link, a
    `spotify:playlist:` uri or a bare 22-char id, reads the playlist back from Spotify
    so the stored name and track count are real, writes an `attached` record. 400 on a
    bad link / no target, 404 when the scan is not in history, 502 when Spotify refuses.
  - `/automation/run` uses `scan_display_name` for the playlist name.
- `backend/scan_job.py` — scheduled mode uses `scan_display_name` too.

### Frontend (`frontend/src/pages/Dashboard.tsx`)
- `ExportInfo` type; `export`/`name` on `HistoryEntry`; `scan_id`/`scan_name`/dates/
  `export` on `ScanStatus`; `scanName()` fallback for rows recorded before names existed.
- Completion banner: the scan's name as the title, `Scan completed · N new releases · <id>`,
  then exactly one of — `Exported to Spotify: <name>` as a link with the track count
  (and ", linked manually" for an attached one) / `Export failed: <reason>` in red /
  `Not exported to Spotify.` + a `Link an existing playlist` button.
- History rows: name as the title, an `On Spotify` / `Export failed` badge, and either
  the playlist link or a `Link a playlist` button (both `stopPropagation`, so they do
  not open the scan).
- The history-view bar gets the same link / failed / link-a-playlist line.
- The manual Export button now sends the right `scan_id` (the viewed history scan, else
  the live one) and refreshes status + history so the link appears at once.

## Verification (MEASURED)
- `npx tsc --noEmit` → exit 0. `npm run build` → built, 2166 modules, no errors.
- Backend modules import clean; `scan_display_name`, `export_record` and
  `_playlist_id_from_url` checked against share link / uri / bare id / junk / empty.
- `record_export` functional test (local storage, scratchpad) — 4 cases all pass:
  1. current scan → record in state AND history, heartbeat unchanged;
  2. older scan → history only, the current banner untouched;
  3. failed export → `status=failed` with the reason kept;
  4. unknown scan id → `in_history: False`, no exception.

## Not done — needs a separate approval
Deploy. `scanner.py` is scan logic, so after the Cloud Build trigger runs, the
`aumradar-scan` Job image must be repointed as well, or the change is live on the web
service only and NOT in the scans (see `aumradar-deployment` memory). Never while a
scan runs.
