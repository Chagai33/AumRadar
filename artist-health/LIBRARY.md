# Library Index — "what do I actually have of this artist?"

A **display-only** layer alongside the Artist Health Engine. It never writes a rank,
a band, a weight, or a cleanup candidate. Delete `cache/library_*.json` and the app
behaves exactly as it did before.

## 1. The question it answers

RANK answers *"did this artist ever enter an included playlist?"*. That is not the
same question as *"do I care about this artist?"* — an artist can have a RANK of 0,
sit in the 🔴 "never entered" band, and still have a dozen songs you personally
marked with a ❤️.

This layer answers the second question, per artist:

- **♥ liked** — how many of their songs are in your Liked Songs.
- **♪ playlists** — how many distinct playlists of yours their songs appear in.
- and the actual **song list**: name, liked-or-not, and which playlists each sits in.

## 2. Why it is a global pull, not a per-artist button

Spotify has **no artist-scoped view of your library**:

- `GET /me/tracks` returns *all* saved tracks, 50 per page, with no artist filter.
- There is no "which playlists contain artist X" endpoint at all.

So fetching for **one** artist costs exactly what fetching for **all 3,800** costs.
One pull therefore builds the index for everybody, and every per-artist lookup
afterwards is an in-memory dict hit.

The tempting alternative — walk the artist's discography via `/artists/{id}/albums`
and then ask `/me/tracks/contains` — was rejected: it is ~20 calls per artist (far
more for a deep back catalogue), it **silently misses tracks where the artist is
only a featured artist**, and it cannot answer the playlist half at all.

## 3. Scope of the pull

| In | Out |
|---|---|
| Liked Songs (all of them) | `outofplaylist` playlists |
| every `weekly` playlist | |
| every `other` playlist | |

Deliberately **independent of Recon's include/exclude flags**. Those govern the
score; this index governs what you can *see*. Recon currently includes only 343 of
1,052 playlists, so a score-scoped index would reproduce the exact blind spot this
layer exists to remove.

Only songs crediting **at least one followed artist** are kept — the feature exists
to inform an unfollow decision, so the rest is dead weight, and dropping it keeps
the index a few MB instead of a few tens of MB.

## 4. Files

| File | What |
|---|---|
| `cache/library_counts.json` | `{artist_uri: {liked, playlists, songs}}` — small (~300KB), read on every list render |
| `cache/library_index.json` | full per-artist song detail — MBs, downloaded lazily on the first panel open |
| `cache/library_state.json` | run state / progress / heartbeat |
| `cache/library_checkpoint.json` | resume point; deleted on success |
| `cache/library_stop_request.json` | stop flag, read by the Job from GCS |

The split is the point: a 3,800-row list never touches the big file.

`counts` is written **after** `index`, so the "index ready" flag the UI reads can
never point at a generation with no detail behind it.

## 5. Running it

`backend/library_job.py` — a Cloud Run Job, exactly like Bootstrap, because the run
is ~1,000 paged reads over several minutes.

```
python -m backend.library_job          # LIBRARY_MODE=run | resume
```

Same operational contract as Bootstrap: 120s heartbeat staleness, a checkpoint every
40 playlists, a stop flag honoured at the next safe point, and `REQUEST_PACE_SEC =
0.25` pacing — the 2026-07-17 hard block came from a sustained rate, and that failure
does **not** arrive as a clean 429.

Phase 1 (Liked Songs) is not folded atomically and does not need to be: an
interrupted liked pass simply re-runs from the top, because `liked` is a flag, not a
counter. Phase 2 folds each playlist atomically, so a crash re-pulls only the
playlists since the last checkpoint.

## 6. API

| Endpoint | |
|---|---|
| `POST /api/library/start` | fresh build |
| `POST /api/library/resume` | continue from checkpoint |
| `POST /api/library/stop` | request stop |
| `GET /api/library/status` | run state, progress, index age, scan scope |
| `GET /api/library/counts` | the counts map for the list column |
| `GET /api/library/artist/{id\|uri\|url}` | the detail panel for one artist |

## 7. UI

- **/cleanup** — a `♥N · ♪M` cell on every row, a 🔍 button per row, and
  **🔍 Review N** in the action bar, which steps through the artists you marked
  one at a time (←/→ keys) so you can check each before removing. The panel can
  un-mark an artist you decide to keep.
- **/health** — the same cell on the ranked rows, and an "In your library" strip in
  the *why* panel next to what makes the score.
- **Build / Refresh library data** lives in the Cleanup toolbar with progress, stop
  and resume.

Before the first build, every badge shows `—` and the panel says so. Nothing breaks.

## 8. Deploy

The Job does not exist yet in `aummusic`. After the image that contains
`backend/library_job.py` is built:

```
gcloud run jobs create aumradar-library \
  --project=aummusic --region=europe-west1 \
  --image=<IMAGE> \
  --command=python --args=-m,backend.library_job \
  --service-account=event-calendar@aummusic.iam.gserviceaccount.com \
  --set-env-vars=BUCKET_NAME=aumradar,GOOGLE_CLOUD_PROJECT=aummusic \
  --memory=1Gi --task-timeout=7200
```

Then point the Service at it — without this env var the Service silently runs the
pull **inline** as a BackgroundTask instead of as a Job (it logs a warning):

```
gcloud run services update aumradar --project=aummusic --region=europe-west1 \
  --update-env-vars=LIBRARY_JOB_NAME=aumradar-library
```

⚠️ **The Service needs `--memory=1Gi`** (it is currently 512Mi). It holds the parsed
detail index in process memory to keep panel opens instant, on top of the Health
engine's existing fold. 512Mi is not enough headroom for both.

⚠️ Like the Bootstrap Job, this Job **pins the image it was created with**. After
each build, repoint it:

```
gcloud run jobs update aumradar-library --image=<NEW-IMAGE> --region=europe-west1
```

## 9. Scopes

`user-library-read` is already in `config.SCOPE`, so **no re-login is needed**.
