# -*- coding: utf-8 -*-
"""Trigger a scan as a Cloud Run Job (production) or as an in-process
BackgroundTask (local dev).

The API layer calls trigger_scan_job() instead of running the scan inline, so a
scan runs to completion regardless of the browser. In production the Service
launches the `aumradar-scan` Job (which runs backend.scan_job); in local dev,
where no Job exists, it falls back to the old BackgroundTask behaviour so the app
still works end-to-end on a laptop.
"""
import os
import time
import logging
from .storage_manager import storage
from .scanner import scanner, SCAN_STATE_FILE

logger = logging.getLogger(__name__)

JOB_REQUEST_FILE = "cache/scan_job_request.json"


def _write_starting_state(mode):
    """Reflect the scan in the shared state the instant it's triggered, so the UI
    shows it immediately instead of looking idle for the ~1-2 min the Job takes to
    cold-start. is_running=True keeps the frontend polling; status='starting' is
    exempted by the Job's concurrency guard, and get_status()'s 120s heartbeat
    staleness clears it if the Job never boots."""
    label = "Updating artist list…" if mode == "refresh_artists" else "Starting scan…"
    storage.save_json(SCAN_STATE_FILE, {
        "is_running": True, "status": "starting", "progress": 0, "total": 0,
        "results_count": 0, "current_artist": label, "logs": [],
        "heartbeat": time.time(),
    })


def _job_configured():
    """True when the Service knows which Cloud Run Job to launch (set in prod)."""
    return bool(os.getenv("SCAN_JOB_NAME") and os.getenv("GOOGLE_CLOUD_PROJECT"))


def _run_cloud_job(mode):
    """Launch the Cloud Run Job, overriding SCAN_MODE for this one execution.
    The google-cloud-run client is imported lazily so local dev without the
    library (or its auth) is unaffected."""
    from google.cloud import run_v2  # lazy: only needed on the cloud path

    project = os.environ["GOOGLE_CLOUD_PROJECT"]
    region = os.getenv("SCAN_JOB_REGION", "europe-west1")
    job = os.environ["SCAN_JOB_NAME"]

    client = run_v2.JobsClient()
    name = f"projects/{project}/locations/{region}/jobs/{job}"
    overrides = run_v2.RunJobRequest.Overrides(
        container_overrides=[
            run_v2.RunJobRequest.Overrides.ContainerOverride(
                env=[run_v2.EnvVar(name="SCAN_MODE", value=mode)]
            )
        ]
    )
    client.run_job(request=run_v2.RunJobRequest(name=name, overrides=overrides))


def trigger_scan_job(mode, sp=None, app_sp=None, settings=None,
                     auto_export_name=None, background_tasks=None):
    """Start a scan. mode: 'manual' | 'resume' | 'scheduled'.

    For 'manual', the resolved settings are persisted to GCS so the Job can read
    them (the Job runs in a different process/instance). 'scheduled' reads
    automation_config inside the Job; 'resume' reads the checkpoint. Cloud mode
    launches the Job; local mode falls back to a BackgroundTask running the same
    scanner coroutine (sp/app_sp are only used by that fallback)."""
    if mode == "manual" and settings is not None:
        if not storage.save_json(JOB_REQUEST_FILE, {
            "settings": settings,
            "auto_export_name": auto_export_name,
        }):
            # The Job reads this file for its settings — a lost write would make it
            # no-op or run a STALE prior request. Fail loudly instead of launching.
            return {"status": "error", "message": "could not persist scan request to storage; scan not started"}

    _write_starting_state(mode)   # immediate UI feedback across the Job's cold-start

    if _job_configured():
        _run_cloud_job(mode)
        return {"status": "job_triggered", "mode": mode}

    # Not configured for the Job → in-process fallback (browser-dependent; dev).
    # If exactly ONE of the two vars is set it's almost certainly a PROD misconfig
    # (Cloud Run does NOT auto-inject GOOGLE_CLOUD_PROJECT) — warn loudly so a scan
    # silently running inline instead of as a Job is visible in the logs.
    if os.getenv("SCAN_JOB_NAME") or os.getenv("GOOGLE_CLOUD_PROJECT"):
        logger.warning(
            "SCAN_JOB_NAME/GOOGLE_CLOUD_PROJECT not BOTH set (SCAN_JOB_NAME set=%s, "
            "GOOGLE_CLOUD_PROJECT set=%s) — running scan INLINE as a BackgroundTask, "
            "NOT as a Cloud Run Job. It will die if the container is recycled.",
            bool(os.getenv("SCAN_JOB_NAME")), bool(os.getenv("GOOGLE_CLOUD_PROJECT")),
        )

    # ── Local dev fallback: run inline as a BackgroundTask (browser-dependent). ──
    if background_tasks is None:
        return {"status": "error", "message": "no Cloud Run Job configured and no task runner available"}
    if mode == "resume":
        background_tasks.add_task(scanner.resume_scan, sp, app_sp)
    elif mode == "refresh_artists":
        background_tasks.add_task(scanner.refresh_followed_artists, sp)
    else:
        background_tasks.add_task(scanner.scan_process, sp, settings, app_sp, auto_export_name)
    return {"status": "started_local", "mode": mode}
