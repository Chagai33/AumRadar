# Bootstrap (Stage 2) — deploy notes

Bootstrap runs as its **own Cloud Run Job** `aumradar-bootstrap` (separate from
`aumradar-scan`), same image as the Service, entrypoint `python -m backend.bootstrap_job`.
The Service launches it via `BOOTSTRAP_JOB_NAME`; the browser only polls `/api/bootstrap/status`.
Project `aummusic` · region `europe-west1` · bucket `aumradar` (see the AumRadar deployment memory).

## 1. One-time: create the Job

Use the **same image** the Service/scan Job currently runs, and the **same env**
as `aumradar-scan` (needs `CLIENT_ID`, `CLIENT_SECRET`, `BUCKET_NAME`,
`GOOGLE_CLOUD_PROJECT`). Grab the live image first, then create:

```bash
IMG=$(gcloud run jobs describe aumradar-scan --region europe-west1 \
      --format='value(template.template.containers[0].image)')

gcloud run jobs create aumradar-bootstrap \
  --region europe-west1 \
  --image "$IMG" \
  --command python \
  --args=-m,backend.bootstrap_job \
  --task-timeout=7200s \
  --max-retries=0 \
  --set-env-vars BUCKET_NAME=aumradar,GOOGLE_CLOUD_PROJECT=aummusic \
  --set-secrets CLIENT_ID=...,CLIENT_SECRET=...   # match aumradar-scan's secret refs
```

**`--task-timeout=7200s --max-retries=0` are mandatory** (same as `aumradar-scan`):
a Cloud Run Job's default task-timeout is only **10 minutes** — a full pull of a few
hundred playlists (worse under rate-limiting) blows past that and the Job is killed.
`--max-retries=0` stops Cloud Run auto-restarting a killed task with `BOOTSTRAP_MODE=run`
(which would re-pull from scratch); we resume manually via the checkpoint instead.
Match `aumradar-scan`'s `--memory` too (the in-memory accumulator is tens of MB).

Then tell the **Service** which Job to launch (one env var on the Cloud Run service):

```bash
gcloud run services update aumradar --region europe-west1 \
  --update-env-vars BOOTSTRAP_JOB_NAME=aumradar-bootstrap
```

(`GOOGLE_CLOUD_PROJECT` is already set on the Service for the scan Job. Region falls
back to `SCAN_JOB_REGION` then `europe-west1` if `BOOTSTRAP_JOB_REGION` is unset.)

## 2. The gotcha (BOOTSTRAP.md §4): repoint the image after every build

Cloud Build redeploys the **Service** only. A Cloud Run **Job pins the image it was
created with**, so after each build the Job keeps running the OLD code until you
repoint it — exactly the pain point the scan Job has:

```bash
IMG=$(gcloud run services describe aumradar --region europe-west1 \
      --format='value(spec.template.spec.containers[0].image)')
gcloud run jobs update aumradar-bootstrap --region europe-west1 --image "$IMG"
gcloud run jobs update aumradar-scan      --region europe-west1 --image "$IMG"
```

**Automate it** by appending a step to the Cloud Build config (runs after the image
is pushed, so both Jobs always match the latest Service):

```yaml
  - name: gcr.io/google.com/cloudsdktool/cloud-sdk
    entrypoint: gcloud
    args: ['run','jobs','update','aumradar-bootstrap','--region','europe-west1','--image','$_IMAGE']
```

## 3. Local dev

No Job needed. With `BOOTSTRAP_JOB_NAME` unset, `/api/bootstrap/start` runs the same
pipeline inline as a FastAPI BackgroundTask (browser-dependent; a resume picks up
from the checkpoint if the process dies). Everything else — status/stop/resume,
scoring, the `cleanup_candidates.json` output — is identical to prod.
```
